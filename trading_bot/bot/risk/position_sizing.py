"""Position sizing: turns (equity, entry price, stop distance) into a
concrete (size, leverage, margin) plan that respects three independent
caps at once:

1. Target risk:   risk_amount = equity * risk_per_trade_pct
2. Leverage cap:   min(configured max_leverage, a liquidation-safety cap
                    derived from the stop distance itself)
3. Capital cap:     margin_required must never exceed available equity

Whenever these conflict, the function narrows the trade (smaller notional,
therefore less than the target risk) rather than ever relaxing a safety
cap -- silently trading smaller is safe, silently over-leveraging is not.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from bot.config import CapitalConfig


@dataclass(frozen=True)
class PositionPlan:
    approved: bool
    reason: str
    side: str = ""
    size: float = 0.0
    notional_usd: float = 0.0
    leverage: int = 1
    margin_required_usd: float = 0.0
    risk_amount_usd: float = 0.0
    entry_price: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0


def plan_position(
    *,
    side: str,
    equity_usd: float,
    entry_price: float,
    capital_cfg: CapitalConfig,
    stop_loss_pct: float,
    take_profit_pct: float,
) -> PositionPlan:
    if side not in ("LONG", "SHORT"):
        return PositionPlan(approved=False, reason=f"unknown side '{side}'")
    if entry_price <= 0:
        return PositionPlan(approved=False, reason="entry_price must be > 0")
    if equity_usd <= 0:
        return PositionPlan(approved=False, reason="equity_usd must be > 0")

    stop_frac = stop_loss_pct / 100.0
    tp_frac = take_profit_pct / 100.0
    if stop_frac <= 0:
        return PositionPlan(approved=False, reason="stop_loss_pct must be > 0")

    if side == "LONG":
        stop_loss_price = entry_price * (1 - stop_frac)
        take_profit_price = entry_price * (1 + tp_frac)
    else:
        stop_loss_price = entry_price * (1 + stop_frac)
        take_profit_price = entry_price * (1 - tp_frac)

    risk_amount_usd = equity_usd * (capital_cfg.risk_per_trade_pct / 100.0)
    notional_from_risk = risk_amount_usd / stop_frac

    # Approx. liquidation distance at leverage L is ~ 1/L. Keep the stop
    # comfortably inside a fraction of that distance so a stop-loss fill
    # should always happen before liquidation would.
    safety_leverage_cap = capital_cfg.liquidation_safety_factor / stop_frac
    leverage_cap = min(capital_cfg.max_leverage, math.floor(safety_leverage_cap))

    if leverage_cap < 1:
        return PositionPlan(
            approved=False,
            reason=(
                f"stop_loss_pct={stop_loss_pct:.3f}% is too wide to respect the "
                f"liquidation_safety_factor ({capital_cfg.liquidation_safety_factor}) at "
                "any leverage >= 1x -- tighten the stop or raise the safety factor"
            ),
        )

    leverage_needed = notional_from_risk / equity_usd
    if leverage_needed <= leverage_cap:
        notional_usd = notional_from_risk
        leverage_final = max(1, math.ceil(leverage_needed))
    else:
        # Capital/leverage-constrained: accept less than the target risk
        # rather than exceed the safety cap or borrow beyond available equity.
        notional_usd = equity_usd * leverage_cap
        leverage_final = leverage_cap

    if notional_usd < capital_cfg.min_order_notional_usd:
        return PositionPlan(
            approved=False,
            reason=(
                f"computed notional ${notional_usd:.2f} is below the minimum order size "
                f"${capital_cfg.min_order_notional_usd:.2f} -- capital too small for the "
                "current risk/stop settings on this symbol"
            ),
        )

    margin_required_usd = notional_usd / leverage_final
    if margin_required_usd > equity_usd + 1e-9:
        return PositionPlan(approved=False, reason="internal sizing error: margin would exceed equity")

    size = notional_usd / entry_price
    actual_risk_usd = size * entry_price * stop_frac

    return PositionPlan(
        approved=True,
        reason="ok",
        side=side,
        size=size,
        notional_usd=notional_usd,
        leverage=leverage_final,
        margin_required_usd=margin_required_usd,
        risk_amount_usd=actual_risk_usd,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )
