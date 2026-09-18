"""Technical indicator calculations: RSI, MACD, Bollinger Bands.

Pure pandas/numpy -- no TA-Lib dependency (avoids a C-extension install
step so the bot runs immediately on a fresh machine).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bot.config import IndicatorConfig


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI via an exponential (not simple) moving average of
    gains/losses -- the standard, widely-used RSI formulation."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi_values = 100 - (100 / (1 + rs))

    # Explicit handling of the division-by-zero edge cases (avg_loss == 0
    # only once warmed up -- during warm-up avg_loss is NaN, not 0, so these
    # masks are False there and rsi_values correctly stays NaN).
    all_gains = (avg_loss == 0) & (avg_gain > 0)
    flat = (avg_loss == 0) & (avg_gain == 0)
    rsi_values = rsi_values.where(~all_gains, 100.0)
    rsi_values = rsi_values.where(~flat, 50.0)
    return rsi_values


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(window=period).mean()
    std = close.rolling(window=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def compute_indicators(df: pd.DataFrame, cfg: IndicatorConfig) -> pd.DataFrame:
    """Return a copy of `df` with rsi, macd/macd_signal/macd_hist and
    bb_upper/bb_mid/bb_lower columns appended."""
    out = df.copy()
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    macd_line, signal_line, hist = macd(out["close"], cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist
    upper, mid, lower = bollinger_bands(out["close"], cfg.bb_period, cfg.bb_std)
    out["bb_upper"] = upper
    out["bb_mid"] = mid
    out["bb_lower"] = lower
    return out


def indicators_ready(df: pd.DataFrame) -> bool:
    """True once the last row has no NaN indicator values (warm-up done)."""
    cols = ["rsi", "macd", "macd_signal", "macd_hist", "bb_upper", "bb_mid", "bb_lower"]
    present = [c for c in cols if c in df.columns]
    if not present or df.empty:
        return False
    return not df.iloc[-1][present].isna().any()
