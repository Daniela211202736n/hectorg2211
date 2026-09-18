"""The main autonomous monitoring/trading loop.

One tick = for each configured symbol: fetch candles -> check/manage any
open position -> if flat and risk allows it, evaluate the signal and
possibly open a new bracket (entry + stop-loss + take-profit) position.
Sleeps `execution.loop_interval_seconds` between ticks. Runs until a STOP
command arrives (via ControlChannel) or the process receives Ctrl+C.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.analysis.signals import Action, generate_signal
from bot.config import AppConfig
from bot.core.control import ControlChannel, ControlCommand
from bot.core.state import BotState, StateStore
from bot.exchange.base import ExchangeClient
from bot.exchange.errors import ExchangeConnectionError, ExchangeOrderError
from bot.logging_setup import Heartbeat, TradeLedger, TradeRecord
from bot.risk.position_sizing import PositionPlan, plan_position
from bot.risk.risk_manager import RiskManager

log = logging.getLogger("trading_bot.engine")


class TradingEngine:
    def __init__(
        self,
        config: AppConfig,
        exchange: ExchangeClient,
        state_dir: str | Path,
        log_dir: str | Path,
    ):
        self.config = config
        self.exchange = exchange
        self.state_store = StateStore(state_dir)
        self.control = ControlChannel(state_dir)
        self.ledger = TradeLedger(log_dir)
        self.heartbeat = Heartbeat(log, config.strategy.execution.heartbeat_interval_seconds)

        account = exchange.get_account_state()
        self.risk_manager = RiskManager(config.strategy.capital, account.equity_usd)

        self._open_symbol: Optional[str] = None
        self._entry_equity_usd: float = account.equity_usd
        self._pending_plan: Optional[PositionPlan] = None
        self._consecutive_errors = 0
        self._running = False
        self._start_time = time.monotonic()

    # -- public API -----------------------------------------------------------
    def run_forever(self) -> None:
        self._running = True
        mode = "LIVE (real money)" if self.config.env.is_live else "DRY_RUN (simulated)"
        log.info(
            "Engine starting | mode=%s network=%s symbols=%s timeframe=%s loop=%ss",
            mode, self.config.env.network, self.config.strategy.market.symbols,
            self.config.strategy.market.timeframe, self.config.strategy.execution.loop_interval_seconds,
        )
        self.control.write_status("RUNNING")
        try:
            while self._running:
                self._handle_control_commands()
                if self._running and self.control.current_status != "PAUSED":
                    self._tick()
                self._persist_state()
                self.heartbeat.maybe_beat(self._status_snapshot())
                if self._running:
                    time.sleep(self.config.strategy.execution.loop_interval_seconds)
        finally:
            self.control.write_status("STOPPED")
            self._persist_state()
            log.info("Engine stopped.")

    def run_iterations(self, n: int) -> None:
        """Run exactly `n` ticks then return. Used by tests and the
        dry-run smoke test -- never used by the long-running CLI path."""
        self._running = True
        self.control.write_status("RUNNING")
        for _ in range(n):
            self._handle_control_commands()
            if not self._running:
                break
            if self.control.current_status != "PAUSED":
                self._tick()
            self._persist_state()
        self.control.write_status("STOPPED")

    # -- status -----------------------------------------------------------
    def _status_snapshot(self, extra: Optional[dict] = None) -> dict:
        state = self.risk_manager.state
        snapshot = {
            "mode": "LIVE" if self.config.env.is_live else "DRY_RUN",
            "network": self.config.env.network,
            "equity_usd": round(state.current_equity, 4),
            "open_positions": state.open_positions,
            "trades_today": state.trades_today,
            "realized_pnl_today_usd": round(state.realized_pnl_today, 4),
            "kill_switch_active": state.kill_switch_active,
            "kill_switch_reason": state.kill_switch_reason,
            "uptime_seconds": int(time.monotonic() - self._start_time),
            "consecutive_errors": self._consecutive_errors,
        }
        if extra:
            snapshot.update(extra)
        return snapshot

    def _persist_state(self) -> None:
        self.state_store.save(BotState(
            status=self.control.current_status,
            updated_at=datetime.now(timezone.utc).isoformat(),
            snapshot=self._status_snapshot(),
        ))

    # -- control -----------------------------------------------------------
    def _handle_control_commands(self) -> None:
        command = self.control.poll()
        if command == ControlCommand.STOP:
            log.info("STOP command received -- shutting down gracefully.")
            self._running = False
        elif command == ControlCommand.PAUSE:
            log.info("PAUSE command received -- holding new entries until RESUME.")
            self.control.write_status("PAUSED")
        elif command == ControlCommand.RESUME:
            log.info("RESUME command received.")
            self.control.write_status("RUNNING")

    # -- tick -----------------------------------------------------------
    def _tick(self) -> None:
        try:
            self._tick_unsafe()
            self._consecutive_errors = 0
        except (ExchangeConnectionError, ExchangeOrderError) as exc:
            self._consecutive_errors += 1
            log.error(
                "Exchange error (%d/%d consecutive): %s",
                self._consecutive_errors, self.config.strategy.execution.max_consecutive_errors, exc,
            )
            self._maybe_backoff_or_halt()
        except Exception as exc:  # noqa: BLE001 - the loop must never die on an unexpected error
            self._consecutive_errors += 1
            log.exception("Unexpected error in engine tick: %s", exc)
            self._maybe_backoff_or_halt()

    def _maybe_backoff_or_halt(self) -> None:
        max_errors = self.config.strategy.execution.max_consecutive_errors
        if self._consecutive_errors >= max_errors:
            log.critical(
                "%d consecutive errors (limit %d) -- pausing trading. Any open position "
                "keeps its exchange-side stop-loss/take-profit; investigate logs/bot.log, "
                "then run `python cli.py resume`.",
                self._consecutive_errors, max_errors,
            )
            self.control.write_status("PAUSED")
        else:
            time.sleep(self.config.strategy.execution.error_backoff_seconds)

    def _tick_unsafe(self) -> None:
        account = self.exchange.get_account_state()
        self.risk_manager.update_equity(account.equity_usd)
        for symbol in self.config.strategy.market.symbols:
            self._process_symbol(symbol, account.equity_usd)
        # A position may have closed while processing symbols above (a
        # dry-run fill, or a live SL/TP that filled between polls). Refresh
        # equity once more so the kill switch and the persisted state
        # snapshot reflect it immediately instead of one tick late.
        refreshed = self.exchange.get_account_state()
        self.risk_manager.update_equity(refreshed.equity_usd)

    # -- per-symbol logic -----------------------------------------------------------
    def _process_symbol(self, symbol: str, equity_usd: float) -> None:
        df = self.exchange.get_candles(
            symbol, self.config.strategy.market.timeframe, self.config.strategy.market.candle_lookback,
        )
        latest_candle = df.iloc[-1]
        open_position = self.exchange.get_open_position(symbol, latest_candle=latest_candle)

        if self._open_symbol == symbol and open_position is None:
            self._handle_position_closed(symbol)
            return

        if open_position is not None:
            return  # already positioned; exchange-side SL/TP manage the exit

        can_open, reason = self.risk_manager.can_open_new_position()
        if not can_open:
            log.debug("%s: not evaluating entries -- %s", symbol, reason)
            return

        signal = generate_signal(df, self.config.strategy.indicators)
        if signal.action == Action.NONE:
            return

        self._open_position(symbol, signal, equity_usd)

    def _open_position(self, symbol: str, signal, equity_usd: float) -> None:
        trade_cfg = self.config.strategy.trade
        plan = plan_position(
            side=signal.action.value,
            equity_usd=equity_usd,
            entry_price=signal.price,
            capital_cfg=self.config.strategy.capital,
            stop_loss_pct=trade_cfg.stop_loss_pct,
            take_profit_pct=trade_cfg.take_profit_pct,
        )
        if not plan.approved:
            log.info("%s: signal %s rejected by position sizing -- %s", symbol, signal.action.value, plan.reason)
            return

        log.info(
            "%s: signal=%s (%s) -> size=%.6f notional=$%.2f leverage=%dx margin=$%.2f risk=$%.2f "
            "sl=%.4f tp=%.4f",
            symbol, signal.action.value, signal.reason, plan.size, plan.notional_usd,
            plan.leverage, plan.margin_required_usd, plan.risk_amount_usd,
            plan.stop_loss_price, plan.take_profit_price,
        )

        self.exchange.set_leverage(symbol, plan.leverage)
        result = self.exchange.open_bracket_position(
            symbol=symbol, side=signal.action.value, size=plan.size,
            entry_price_hint=plan.entry_price, stop_loss_price=plan.stop_loss_price,
            take_profit_price=plan.take_profit_price, slippage_pct=trade_cfg.slippage_pct,
        )
        if not result.success:
            log.warning("%s: order rejected -- %s", symbol, result.message)
            return

        self._open_symbol = symbol
        self._entry_equity_usd = equity_usd
        self._pending_plan = plan
        self.risk_manager.record_trade_opened()

    def _pop_matching_sim_trade(self, symbol: str):
        trades = getattr(self.exchange, "closed_trades", None)
        if not trades:
            return None
        for i in range(len(trades) - 1, -1, -1):
            if trades[i].symbol == symbol:
                return trades.pop(i)
        return None

    def _handle_position_closed(self, symbol: str) -> None:
        plan = self._pending_plan
        sim_trade = self._pop_matching_sim_trade(symbol)
        # Re-fetch equity now rather than reusing the value captured at the
        # top of this tick: in dry-run, the simulated fill that just closed
        # this position happens as a side effect of get_open_position()
        # moments after that snapshot was taken, so the tick-start value is
        # stale by exactly this trade's PnL. Re-fetching keeps both the
        # ledger's equity_after_usd and the live-client PnL fallback (below)
        # accurate.
        equity_after = self.exchange.get_account_state().equity_usd

        if sim_trade is not None:
            realized_pnl = sim_trade.realized_pnl_usd
            exit_price = sim_trade.exit_price
            fees_paid = sim_trade.fees_paid_usd
            exit_reason = sim_trade.exit_reason
        else:
            # Live client: no fills API wired up yet, so PnL is inferred
            # from the equity delta across the trade. Accurate for total
            # PnL, but exit_price/fees are unknown -- left at 0 and the
            # reason is explicitly marked approximate. See README for how
            # to extend this with a fills-based lookup if you need exact
            # per-trade fill prices in the ledger.
            realized_pnl = equity_after - self._entry_equity_usd
            exit_price = 0.0
            fees_paid = 0.0
            exit_reason = "detected_closed_approximate"

        self.risk_manager.record_trade_closed(realized_pnl)
        log.info(
            "%s: position closed (%s) -- realized PnL $%.4f, equity now $%.4f",
            symbol, exit_reason, realized_pnl, equity_after,
        )

        self.ledger.record(TradeRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=symbol,
            side=plan.side if plan else "",
            reason_opened=getattr(plan, "reason", "") if plan else "",
            entry_price=plan.entry_price if plan else 0.0,
            exit_price=exit_price,
            size=plan.size if plan else 0.0,
            leverage=plan.leverage if plan else 1,
            notional_usd=plan.notional_usd if plan else 0.0,
            margin_used_usd=plan.margin_required_usd if plan else 0.0,
            fees_paid_usd=fees_paid,
            realized_pnl_usd=realized_pnl,
            equity_after_usd=equity_after,
            exit_reason=exit_reason,
        ))
        self._open_symbol = None
        self._pending_plan = None
