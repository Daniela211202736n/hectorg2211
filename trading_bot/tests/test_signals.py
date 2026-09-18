from __future__ import annotations

import pandas as pd

from bot.analysis.signals import Action, generate_signal_from_indicators
from bot.config import IndicatorConfig


def _rows(prev: dict, last: dict) -> pd.DataFrame:
    return pd.DataFrame([prev, last])


def test_long_signal_on_full_confirmation(default_indicator_cfg):
    df = _rows(
        {"close": 100.0, "rsi": 28.0, "macd_hist": -0.5, "bb_lower": 99.0, "bb_upper": 110.0},
        {"close": 99.2, "rsi": 31.0, "macd_hist": 0.2, "bb_lower": 99.0, "bb_upper": 110.0},
    )
    signal = generate_signal_from_indicators(df, default_indicator_cfg)
    assert signal.action == Action.LONG


def test_short_signal_on_full_confirmation(default_indicator_cfg):
    df = _rows(
        {"close": 100.0, "rsi": 72.0, "macd_hist": 0.5, "bb_lower": 90.0, "bb_upper": 100.5},
        {"close": 100.8, "rsi": 69.0, "macd_hist": -0.2, "bb_lower": 90.0, "bb_upper": 100.5},
    )
    signal = generate_signal_from_indicators(df, default_indicator_cfg)
    assert signal.action == Action.SHORT


def test_no_signal_when_macd_has_not_crossed(default_indicator_cfg):
    df = _rows(
        {"close": 100.0, "rsi": 28.0, "macd_hist": 0.3, "bb_lower": 99.0, "bb_upper": 110.0},
        {"close": 99.2, "rsi": 31.0, "macd_hist": 0.5, "bb_lower": 99.0, "bb_upper": 110.0},
    )
    signal = generate_signal_from_indicators(df, default_indicator_cfg)
    assert signal.action == Action.NONE


def test_no_signal_when_price_not_near_band(default_indicator_cfg):
    df = _rows(
        {"close": 105.0, "rsi": 28.0, "macd_hist": -0.5, "bb_lower": 99.0, "bb_upper": 110.0},
        {"close": 105.5, "rsi": 31.0, "macd_hist": 0.2, "bb_lower": 99.0, "bb_upper": 110.0},
    )
    signal = generate_signal_from_indicators(df, default_indicator_cfg)
    assert signal.action == Action.NONE


def test_no_signal_when_rsi_was_not_oversold(default_indicator_cfg):
    df = _rows(
        {"close": 100.0, "rsi": 45.0, "macd_hist": -0.5, "bb_lower": 99.0, "bb_upper": 110.0},
        {"close": 99.2, "rsi": 47.0, "macd_hist": 0.2, "bb_lower": 99.0, "bb_upper": 110.0},
    )
    signal = generate_signal_from_indicators(df, default_indicator_cfg)
    assert signal.action == Action.NONE


def test_generate_signal_not_enough_candles(default_indicator_cfg):
    from bot.analysis.signals import generate_signal
    df = pd.DataFrame([{
        "timestamp": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
    }])
    signal = generate_signal(df, default_indicator_cfg)
    assert signal.action == Action.NONE
