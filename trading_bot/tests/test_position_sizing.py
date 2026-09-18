from __future__ import annotations

import pytest

from bot.config import CapitalConfig
from bot.risk.position_sizing import plan_position


def test_normal_case_uses_partial_leverage(default_capital_cfg):
    # equity=50, risk=2% -> $1 risk. stop=1.5% -> notional_from_risk=$66.67.
    # safety_leverage_cap = 0.5/0.015 = 33 (floored), capped by max_leverage=3.
    # leverage_needed = 66.67/50 = 1.33 -> leverage_final = ceil(1.33) = 2.
    plan = plan_position(
        side="LONG", equity_usd=50.0, entry_price=1000.0,
        capital_cfg=default_capital_cfg, stop_loss_pct=1.5, take_profit_pct=2.25,
    )
    assert plan.approved
    assert plan.leverage == 2
    assert plan.notional_usd == pytest.approx(66.666667, rel=1e-4)
    assert plan.margin_required_usd == pytest.approx(33.333333, rel=1e-4)
    assert plan.margin_required_usd <= 50.0
    assert plan.risk_amount_usd == pytest.approx(1.0, rel=1e-4)
    assert plan.stop_loss_price == pytest.approx(985.0)
    assert plan.take_profit_price == pytest.approx(1022.5)


def test_short_side_prices_are_mirrored(default_capital_cfg):
    plan = plan_position(
        side="SHORT", equity_usd=50.0, entry_price=1000.0,
        capital_cfg=default_capital_cfg, stop_loss_pct=1.5, take_profit_pct=2.25,
    )
    assert plan.approved
    assert plan.stop_loss_price == pytest.approx(1015.0)
    assert plan.take_profit_price == pytest.approx(977.5)


def test_capital_constrained_case_shrinks_notional_never_exceeds_margin():
    cfg = CapitalConfig(
        initial_capital_usd=50.0, risk_per_trade_pct=5.0, max_leverage=3,
        liquidation_safety_factor=0.5, max_daily_loss_pct=6.0,
        max_concurrent_positions=1, min_order_notional_usd=10.0,
    )
    # risk_amount = 2.5, stop=0.5% -> notional_from_risk = 500 -> way beyond
    # what $50 at <=3x leverage can margin ($150 max) -- must be capped.
    plan = plan_position(
        side="LONG", equity_usd=50.0, entry_price=1000.0,
        capital_cfg=cfg, stop_loss_pct=0.5, take_profit_pct=1.0,
    )
    assert plan.approved
    assert plan.leverage == 3
    assert plan.notional_usd == pytest.approx(150.0)
    assert plan.margin_required_usd == pytest.approx(50.0)
    assert plan.risk_amount_usd < 2.5  # target risk was not fully reached, by design


def test_margin_never_exceeds_equity_across_random_grid():
    cfg = CapitalConfig(max_leverage=5, liquidation_safety_factor=0.5)
    for equity in (5.0, 20.0, 50.0, 500.0):
        for stop_pct in (0.2, 0.5, 1.0, 1.5, 3.0, 8.0):
            for risk_pct in (0.5, 1.0, 2.0, 5.0):
                cfg2 = CapitalConfig(
                    initial_capital_usd=cfg.initial_capital_usd, risk_per_trade_pct=risk_pct,
                    max_leverage=cfg.max_leverage, liquidation_safety_factor=cfg.liquidation_safety_factor,
                    max_daily_loss_pct=cfg.max_daily_loss_pct, max_concurrent_positions=cfg.max_concurrent_positions,
                    min_order_notional_usd=1.0,
                )
                plan = plan_position(
                    side="LONG", equity_usd=equity, entry_price=100.0,
                    capital_cfg=cfg2, stop_loss_pct=stop_pct, take_profit_pct=stop_pct * 1.5,
                )
                if plan.approved:
                    assert plan.margin_required_usd <= equity + 1e-6
                    assert plan.leverage <= cfg2.max_leverage
                    assert plan.leverage <= (cfg2.liquidation_safety_factor / (stop_pct / 100.0)) + 1e-6


def test_rejects_when_stop_too_wide_for_any_safe_leverage():
    cfg = CapitalConfig(liquidation_safety_factor=0.05, max_leverage=3)
    plan = plan_position(
        side="LONG", equity_usd=50.0, entry_price=1000.0,
        capital_cfg=cfg, stop_loss_pct=20.0, take_profit_pct=30.0,
    )
    assert not plan.approved
    assert "leverage" in plan.reason.lower()


def test_rejects_below_min_notional():
    cfg = CapitalConfig(
        initial_capital_usd=50.0, risk_per_trade_pct=0.1, max_leverage=3,
        liquidation_safety_factor=0.5, min_order_notional_usd=10.0,
    )
    # risk_amount = 0.05, stop=1.5% -> notional_from_risk = 3.33 < min_order_notional_usd
    plan = plan_position(
        side="LONG", equity_usd=50.0, entry_price=1000.0,
        capital_cfg=cfg, stop_loss_pct=1.5, take_profit_pct=2.25,
    )
    assert not plan.approved
    assert "minimum" in plan.reason.lower()


def test_rejects_invalid_inputs(default_capital_cfg):
    assert not plan_position(
        side="LONG", equity_usd=0.0, entry_price=100.0,
        capital_cfg=default_capital_cfg, stop_loss_pct=1.0, take_profit_pct=1.5,
    ).approved
    assert not plan_position(
        side="LONG", equity_usd=50.0, entry_price=0.0,
        capital_cfg=default_capital_cfg, stop_loss_pct=1.0, take_profit_pct=1.5,
    ).approved
    assert not plan_position(
        side="SIDEWAYS", equity_usd=50.0, entry_price=100.0,
        capital_cfg=default_capital_cfg, stop_loss_pct=1.0, take_profit_pct=1.5,
    ).approved
