"""Portfolio-level risk controls layered on top of per-trade position sizing:
a daily-loss kill switch and a max-concurrent-positions cap.

The kill switch stops the bot from OPENING new trades; it deliberately does
not force-close existing positions, because those already have exchange-
side stop-loss/take-profit orders resting on them -- yanking them early
would remove protection rather than add it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from bot.config import CapitalConfig


@dataclass
class RiskState:
    day_key: str
    day_start_equity: float
    current_equity: float
    open_positions: int = 0
    trades_today: int = 0
    realized_pnl_today: float = 0.0
    kill_switch_active: bool = False
    kill_switch_reason: str = ""


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class RiskManager:
    def __init__(self, capital_cfg: CapitalConfig, initial_equity: float):
        self._cfg = capital_cfg
        self.state = RiskState(
            day_key=_today_key(),
            day_start_equity=initial_equity,
            current_equity=initial_equity,
        )

    def _roll_day_if_needed(self) -> None:
        today = _today_key()
        if today != self.state.day_key:
            self.state.day_key = today
            self.state.day_start_equity = self.state.current_equity
            self.state.realized_pnl_today = 0.0
            self.state.trades_today = 0
            self.state.kill_switch_active = False
            self.state.kill_switch_reason = ""

    def update_equity(self, equity_usd: float) -> None:
        self._roll_day_if_needed()
        self.state.current_equity = equity_usd
        if self.state.day_start_equity <= 0:
            return
        daily_loss_pct = (self.state.day_start_equity - equity_usd) / self.state.day_start_equity * 100.0
        if daily_loss_pct >= self._cfg.max_daily_loss_pct and not self.state.kill_switch_active:
            self.state.kill_switch_active = True
            self.state.kill_switch_reason = (
                f"daily loss {daily_loss_pct:.2f}% >= max_daily_loss_pct "
                f"{self._cfg.max_daily_loss_pct:.2f}%"
            )

    def record_trade_opened(self) -> None:
        self.state.open_positions += 1
        self.state.trades_today += 1

    def record_trade_closed(self, realized_pnl_usd: float) -> None:
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.realized_pnl_today += realized_pnl_usd

    def can_open_new_position(self) -> tuple[bool, str]:
        self._roll_day_if_needed()
        if self.state.kill_switch_active:
            return False, f"kill switch active: {self.state.kill_switch_reason}"
        if self.state.open_positions >= self._cfg.max_concurrent_positions:
            return False, (
                f"max_concurrent_positions reached "
                f"({self.state.open_positions}/{self._cfg.max_concurrent_positions})"
            )
        return True, "ok"
