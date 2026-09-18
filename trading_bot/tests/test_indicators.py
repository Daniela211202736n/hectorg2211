from __future__ import annotations

from bot.analysis.indicators import bollinger_bands, compute_indicators, indicators_ready, macd, rsi
from tests.conftest import make_ohlcv


def test_rsi_uptrend_approaches_100():
    closes = [100 + i for i in range(60)]  # strictly increasing
    df = make_ohlcv(closes)
    values = rsi(df["close"], period=14)
    assert values.iloc[-1] > 95


def test_rsi_downtrend_approaches_0():
    closes = [200 - i for i in range(60)]  # strictly decreasing
    df = make_ohlcv(closes)
    values = rsi(df["close"], period=14)
    assert values.iloc[-1] < 5


def test_rsi_flat_series_is_neutral():
    closes = [100.0] * 40
    df = make_ohlcv(closes)
    values = rsi(df["close"], period=14)
    assert values.iloc[-1] == 50.0


def test_rsi_bounded_0_100_on_noisy_series():
    import random
    rng = random.Random(7)
    price = 100.0
    closes = []
    for _ in range(200):
        price = max(1.0, price * (1 + rng.gauss(0, 0.01)))
        closes.append(price)
    df = make_ohlcv(closes)
    values = rsi(df["close"], period=14).dropna()
    assert (values >= 0).all() and (values <= 100).all()


def test_macd_line_matches_ema_difference():
    closes = [100 + i * 0.5 for i in range(80)]
    df = make_ohlcv(closes)
    macd_line, signal_line, hist = macd(df["close"], fast=12, slow=26, signal=9)
    ema_fast = df["close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["close"].ewm(span=26, adjust=False).mean()
    assert (macd_line - (ema_fast - ema_slow)).abs().max() < 1e-9
    assert (hist - (macd_line - signal_line)).abs().max() < 1e-9


def test_macd_positive_in_sustained_uptrend():
    closes = [100 + i * 1.5 for i in range(80)]
    df = make_ohlcv(closes)
    macd_line, _, _ = macd(df["close"])
    assert macd_line.iloc[-1] > 0


def test_bollinger_bands_ordering_and_midline():
    import random
    rng = random.Random(3)
    price = 100.0
    closes = []
    for _ in range(60):
        price = max(1.0, price + rng.gauss(0, 1.0))
        closes.append(price)
    df = make_ohlcv(closes)
    upper, mid, lower = bollinger_bands(df["close"], period=20, num_std=2.0)
    tail = slice(20, None)
    assert (upper[tail] >= mid[tail]).all()
    assert (mid[tail] >= lower[tail]).all()
    expected_mid = df["close"].rolling(20).mean()
    assert (mid - expected_mid).abs().max() < 1e-9


def test_compute_indicators_and_ready_flag(default_indicator_cfg):
    closes = [100 + i * 0.1 for i in range(10)]
    short_df = make_ohlcv(closes)
    enriched_short = compute_indicators(short_df, default_indicator_cfg)
    assert not indicators_ready(enriched_short)  # not enough warm-up yet

    long_closes = [100 + i * 0.1 for i in range(120)]
    long_df = make_ohlcv(long_closes)
    enriched_long = compute_indicators(long_df, default_indicator_cfg)
    assert indicators_ready(enriched_long)
    for col in ("rsi", "macd", "macd_signal", "macd_hist", "bb_upper", "bb_mid", "bb_lower"):
        assert col in enriched_long.columns
