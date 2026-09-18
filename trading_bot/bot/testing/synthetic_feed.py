"""Synthetic OHLCV feed: a safety-net fallback used only when no real
market data is reachable (e.g. this machine has no internet access right
now), and as a fully deterministic, offline data source for tests.

Never used when DRY_RUN's public-data probe succeeds, and never used in
LIVE mode at all.
"""
from __future__ import annotations

import random
from typing import Callable

import pandas as pd


def synthetic_candle_provider(
    start_price: float = 60_000.0,
    volatility_pct: float = 0.4,
    seed: int = 42,
) -> Callable[[str, str, int], pd.DataFrame]:
    """Returns a CandleProvider-compatible callable backed by a seeded
    random walk. Each call advances the series by one bar (simulating one
    tick of real time passing) and returns the trailing `lookback` rows."""
    rng = random.Random(seed)
    history: list[dict] = []
    price = start_price
    t0 = 1_700_000_000_000  # arbitrary fixed epoch ms, irrelevant to the math

    def _extend_to(n: int) -> None:
        nonlocal price
        while len(history) < n:
            idx = len(history)
            drift = rng.gauss(0, volatility_pct / 100.0)
            open_price = price
            close_price = max(0.01, open_price * (1 + drift))
            wick = abs(rng.gauss(0, volatility_pct / 400.0))
            high = max(open_price, close_price) * (1 + wick)
            low = min(open_price, close_price) * (1 - wick)
            volume = abs(rng.gauss(100, 30))
            history.append({
                "timestamp": t0 + idx * 60_000,
                "open": open_price, "high": high, "low": max(0.01, low),
                "close": close_price, "volume": volume,
            })
            price = close_price

    def provider(symbol: str, timeframe: str, lookback: int) -> pd.DataFrame:
        if len(history) < lookback:
            _extend_to(lookback)
        else:
            _extend_to(len(history) + 1)
        return pd.DataFrame(history[-lookback:]).reset_index(drop=True)

    return provider
