"""Rule-based entry signal generation.

Deliberately conservative: it requires three-way confirmation (RSI turning
away from an extreme + a fresh MACD histogram cross + price actually
touching the corresponding Bollinger Band) before proposing a trade. Fewer,
higher-conviction trades matter more than trade frequency when starting
from a small account.

This module only decides ENTRIES. Exits are handled by the exchange-side
stop-loss/take-profit bracket orders placed by the risk/exchange layers, not
by this strategy re-evaluating every tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from bot.analysis.indicators import compute_indicators, indicators_ready
from bot.config import IndicatorConfig


class Action(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


@dataclass(frozen=True)
class Signal:
    action: Action
    reason: str
    price: float
    rsi: float = float("nan")
    macd_hist: float = float("nan")
    bb_lower: float = float("nan")
    bb_upper: float = float("nan")


def generate_signal(df: pd.DataFrame, cfg: IndicatorConfig) -> Signal:
    """`df` must have raw OHLCV columns; indicators are computed here.
    Evaluates strictly on the last *closed* candle (df.iloc[-1])."""
    if len(df) < 2:
        return Signal(Action.NONE, "not enough candles yet", price=float("nan"))

    enriched = compute_indicators(df, cfg)
    if not indicators_ready(enriched):
        last_price = float(enriched.iloc[-1]["close"])
        return Signal(Action.NONE, "indicators still warming up", price=last_price)

    return generate_signal_from_indicators(enriched, cfg)


def generate_signal_from_indicators(enriched: pd.DataFrame, cfg: IndicatorConfig) -> Signal:
    """Pure decision logic on an already-enriched DataFrame (must have
    close/rsi/macd_hist/bb_lower/bb_upper columns on its last two rows).
    Split out from `generate_signal` so the rule logic can be unit-tested
    with exact, hand-picked indicator values instead of relying on
    coincidental output from real indicator math."""
    last = enriched.iloc[-1]
    prev = enriched.iloc[-2]

    price = float(last["close"])
    rsi_val = float(last["rsi"])
    macd_hist = float(last["macd_hist"])
    bb_lower = float(last["bb_lower"])
    bb_upper = float(last["bb_upper"])

    band_tol = cfg.bb_tolerance_pct / 100.0
    near_lower_band = price <= bb_lower * (1 + band_tol)
    near_upper_band = price >= bb_upper * (1 - band_tol)

    macd_cross_up = prev["macd_hist"] <= 0 and macd_hist > 0
    macd_cross_down = prev["macd_hist"] >= 0 and macd_hist < 0

    rsi_recovering = prev["rsi"] <= cfg.rsi_oversold and rsi_val > prev["rsi"]
    rsi_falling = prev["rsi"] >= cfg.rsi_overbought and rsi_val < prev["rsi"]

    if rsi_recovering and macd_cross_up and near_lower_band:
        return Signal(
            Action.LONG,
            "RSI oversold recovery + MACD bullish cross + price at lower Bollinger Band",
            price, rsi_val, macd_hist, bb_lower, bb_upper,
        )

    if rsi_falling and macd_cross_down and near_upper_band:
        return Signal(
            Action.SHORT,
            "RSI overbought rollover + MACD bearish cross + price at upper Bollinger Band",
            price, rsi_val, macd_hist, bb_lower, bb_upper,
        )

    return Signal(Action.NONE, "no confirmed setup", price, rsi_val, macd_hist, bb_lower, bb_upper)
