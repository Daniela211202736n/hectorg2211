from __future__ import annotations

from bot.config import CapitalConfig
from bot.risk.risk_manager import RiskManager


def test_kill_switch_triggers_on_daily_loss():
    cfg = CapitalConfig(max_daily_loss_pct=6.0)
    rm = RiskManager(cfg, initial_equity=50.0)
    rm.update_equity(47.0)  # -6% exactly
    can_open, reason = rm.can_open_new_position()
    assert not can_open
    assert "kill switch" in reason.lower()


def test_no_kill_switch_below_threshold():
    cfg = CapitalConfig(max_daily_loss_pct=6.0)
    rm = RiskManager(cfg, initial_equity=50.0)
    rm.update_equity(48.0)  # -4%
    can_open, _ = rm.can_open_new_position()
    assert can_open


def test_max_concurrent_positions_enforced():
    cfg = CapitalConfig(max_concurrent_positions=1)
    rm = RiskManager(cfg, initial_equity=50.0)
    can_open, _ = rm.can_open_new_position()
    assert can_open
    rm.record_trade_opened()
    can_open, reason = rm.can_open_new_position()
    assert not can_open
    assert "max_concurrent_positions" in reason
    rm.record_trade_closed(realized_pnl_usd=1.0)
    can_open, _ = rm.can_open_new_position()
    assert can_open


def test_realized_pnl_accumulates_and_open_positions_never_negative():
    cfg = CapitalConfig()
    rm = RiskManager(cfg, initial_equity=50.0)
    rm.record_trade_closed(realized_pnl_usd=-2.0)  # closing with nothing open
    assert rm.state.open_positions == 0
    rm.record_trade_opened()
    rm.record_trade_closed(realized_pnl_usd=3.5)
    assert rm.state.realized_pnl_today == 1.5
    assert rm.state.open_positions == 0
