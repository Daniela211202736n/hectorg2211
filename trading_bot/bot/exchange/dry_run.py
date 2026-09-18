"""Simulated exchange client: no network writes, ever.

Used for (a) DRY_RUN=true (the default) and (b) all unit/integration
tests. It gets candle data from an injected `candle_provider` callable so
the exact same class can run against synthetic data (tests) or real public
market data (a paper-trading smoke test) without any code change.

Simplifications, clearly called out because they matter if you're staring
at simulated PnL and wondering why it doesn't match a real fill:
  - Entry fills exactly at `entry_price_hint` (no simulated slippage).
  - Stop-loss/take-profit fill exactly at their trigger price (no slippage
    past the trigger, no partial fills).
  - If both SL and TP fall inside the same candle's [low, high] range, the
    stop-loss is assumed to have hit first (the conservative assumption
    also standard in backtesting).
  - Margin/leverage bookkeeping is simplified to a single running `equity`
    number adjusted by realized PnL and fees; it does not model exchange
    margin requirements while a position is open.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

from bot.exchange.base import (
    AccountState,
    ExchangeClient,
    OrderResult,
    PositionInfo,
    Side,
    validate_candles,
)

log = logging.getLogger("trading_bot.exchange.dry_run")

CandleProvider = Callable[[str, str, int], pd.DataFrame]


@dataclass
class _SimPosition:
    side: Side
    size: float
    entry_price: float
    leverage: int
    stop_loss_price: float
    take_profit_price: float
    margin_usd: float
    fees_paid_usd: float = 0.0


@dataclass
class _ClosedTrade:
    symbol: str
    side: Side
    size: float
    entry_price: float
    exit_price: float
    leverage: int
    notional_usd: float
    margin_usd: float
    fees_paid_usd: float
    realized_pnl_usd: float
    exit_reason: str


class DryRunExchangeClient(ExchangeClient):
    def __init__(
        self,
        candle_provider: CandleProvider,
        starting_equity_usd: float,
        taker_fee_pct: float = 0.035,
        maker_fee_pct: float = 0.01,
    ):
        self._candle_provider = candle_provider
        self._equity = float(starting_equity_usd)
        self._taker_fee = taker_fee_pct / 100.0
        self._maker_fee = maker_fee_pct / 100.0
        self._positions: dict[str, _SimPosition] = {}
        self._leverage: dict[str, int] = {}
        self.closed_trades: list[_ClosedTrade] = []

    # -- data -----------------------------------------------------------
    def get_candles(self, symbol: str, timeframe: str, lookback: int) -> pd.DataFrame:
        df = self._candle_provider(symbol, timeframe, lookback)
        return validate_candles(df, source="dry_run.candle_provider")

    def get_account_state(self) -> AccountState:
        return AccountState(equity_usd=self._equity, withdrawable_usd=self._equity)

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._leverage[symbol] = leverage

    # -- orders -----------------------------------------------------------
    def open_bracket_position(
        self,
        symbol: str,
        side: Side,
        size: float,
        entry_price_hint: float,
        stop_loss_price: float,
        take_profit_price: float,
        slippage_pct: float,
    ) -> OrderResult:
        if symbol in self._positions:
            return OrderResult(False, f"{symbol}: simulated position already open")
        if size <= 0 or entry_price_hint <= 0:
            return OrderResult(False, "size and entry_price_hint must be > 0")

        leverage = self._leverage.get(symbol, 1)
        notional = size * entry_price_hint
        margin = notional / max(1, leverage)
        fee = notional * self._taker_fee

        if margin + fee > self._equity + 1e-6:
            return OrderResult(False, f"{symbol}: simulated equity too low for this order")

        self._equity -= fee
        self._positions[symbol] = _SimPosition(
            side=side, size=size, entry_price=entry_price_hint, leverage=leverage,
            stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
            margin_usd=margin, fees_paid_usd=fee,
        )
        log.info(
            "[DRY-RUN] Opened simulated %s %s size=%.6f entry=%.4f sl=%.4f tp=%.4f lev=%dx",
            side, symbol, size, entry_price_hint, stop_loss_price, take_profit_price, leverage,
        )
        return OrderResult(True, "simulated fill", fill_price=entry_price_hint)

    def get_open_position(self, symbol: str, latest_candle: "pd.Series | None" = None) -> Optional[PositionInfo]:
        self._maybe_trigger_close(symbol, latest_candle)
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        return PositionInfo(
            symbol=symbol, side=pos.side, size=pos.size, entry_price=pos.entry_price,
            leverage=pos.leverage,
        )

    def close_position(self, symbol: str) -> OrderResult:
        pos = self._positions.get(symbol)
        if pos is None:
            return OrderResult(False, f"{symbol}: no simulated position open")
        candles = self._candle_provider(symbol, "1m", 1)
        last_price = float(candles.iloc[-1]["close"]) if not candles.empty else pos.entry_price
        self._settle(symbol, pos, exit_price=last_price, reason="manual_close")
        return OrderResult(True, "simulated close", fill_price=last_price)

    # -- internals -----------------------------------------------------------
    def _maybe_trigger_close(self, symbol: str, latest_candle: "pd.Series | None") -> None:
        pos = self._positions.get(symbol)
        if pos is None or latest_candle is None:
            return
        high = float(latest_candle["high"])
        low = float(latest_candle["low"])

        exit_price: Optional[float] = None
        reason = ""
        if pos.side == "LONG":
            if low <= pos.stop_loss_price:
                exit_price, reason = pos.stop_loss_price, "stop_loss"
            elif high >= pos.take_profit_price:
                exit_price, reason = pos.take_profit_price, "take_profit"
        else:
            if high >= pos.stop_loss_price:
                exit_price, reason = pos.stop_loss_price, "stop_loss"
            elif low <= pos.take_profit_price:
                exit_price, reason = pos.take_profit_price, "take_profit"

        if exit_price is not None:
            self._settle(symbol, pos, exit_price=exit_price, reason=reason)

    def _settle(self, symbol: str, pos: _SimPosition, exit_price: float, reason: str) -> None:
        if pos.side == "LONG":
            gross_pnl = (exit_price - pos.entry_price) * pos.size
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.size
        exit_fee = exit_price * pos.size * self._taker_fee
        net_pnl = gross_pnl - exit_fee

        self._equity += net_pnl
        total_fees = pos.fees_paid_usd + exit_fee

        self.closed_trades.append(_ClosedTrade(
            symbol=symbol, side=pos.side, size=pos.size, entry_price=pos.entry_price,
            exit_price=exit_price, leverage=pos.leverage, notional_usd=pos.size * pos.entry_price,
            margin_usd=pos.margin_usd, fees_paid_usd=total_fees, realized_pnl_usd=net_pnl,
            exit_reason=reason,
        ))
        log.info(
            "[DRY-RUN] Closed simulated %s %s exit=%.4f reason=%s pnl=%.4f equity=%.4f",
            pos.side, symbol, exit_price, reason, net_pnl, self._equity,
        )
        del self._positions[symbol]
