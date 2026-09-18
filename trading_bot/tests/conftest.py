from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import CapitalConfig, IndicatorConfig  # noqa: E402


def make_ohlcv(closes: list[float], start_ts: int = 1_700_000_000_000, step_ms: int = 60_000) -> pd.DataFrame:
    """Build a minimal OHLCV frame from a list of closes. open == previous
    close, high/low pad the open/close range slightly -- good enough for
    indicator warm-up / math tests where only `close` actually matters."""
    rows = []
    prev_close = closes[0]
    for i, close in enumerate(closes):
        open_ = prev_close
        high = max(open_, close) * 1.0005
        low = min(open_, close) * 0.9995
        rows.append({
            "timestamp": start_ts + i * step_ms,
            "open": open_, "high": high, "low": low, "close": close,
            "volume": 100.0,
        })
        prev_close = close
    return pd.DataFrame(rows)


@pytest.fixture
def default_indicator_cfg() -> IndicatorConfig:
    return IndicatorConfig()


@pytest.fixture
def default_capital_cfg() -> CapitalConfig:
    return CapitalConfig()
