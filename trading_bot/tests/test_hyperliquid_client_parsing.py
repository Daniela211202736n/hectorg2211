"""Tests for the dependency-free parts of hyperliquid_client.py -- the
candle-parsing helpers. These run without hyperliquid-python-sdk or
eth-account installed, since HyperliquidClient/PublicMarketData only
import those lazily inside __init__ (see module docstring for why: DRY_RUN
should never require the SDK to even be importable).

This is also where the module's biggest documented risk lives: the exact
candleSnapshot response field names could not be verified against live
Hyperliquid docs while this bot was built (sandboxed, no route to their
docs site). These tests pin down what the parser does with the schema we
believe is correct, and prove it fails loudly rather than silently on an
unrecognised shape.
"""
from __future__ import annotations

import pytest

from bot.exchange.errors import ExchangeConnectionError
from bot.exchange.hyperliquid_client import _first_present, _parse_candles_raw, base_url_for


def test_base_url_for_network():
    assert base_url_for("mainnet") == "https://api.hyperliquid.xyz"
    assert base_url_for("testnet") == "https://api.hyperliquid-testnet.xyz"
    assert base_url_for("anything-else") == "https://api.hyperliquid-testnet.xyz"  # safe default


def test_first_present_returns_first_matching_key():
    assert _first_present({"t": 1, "o": 2}, ("t", "time", "T")) == 1
    assert _first_present({"time": 1}, ("t", "time", "T")) == 1
    with pytest.raises(KeyError):
        _first_present({"x": 1}, ("t", "time", "T"))


def test_parse_candles_raw_with_documented_short_key_schema():
    raw = [
        {"t": 1000, "T": 1060, "s": "BTC", "i": "1m", "o": "100.0", "h": "101.0", "l": "99.0", "c": "100.5", "v": "12.3", "n": 5},
        {"t": 1060, "T": 1120, "s": "BTC", "i": "1m", "o": "100.5", "h": "102.0", "l": "100.0", "c": "101.5", "v": "8.1", "n": 3},
    ]
    df = _parse_candles_raw(raw)
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df.iloc[0]["close"] == 100.5
    assert df.iloc[1]["open"] == 100.5
    assert df["timestamp"].is_monotonic_increasing


def test_parse_candles_raw_tolerates_verbose_key_alternates():
    raw = [{"time": 1000, "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "1"}]
    df = _parse_candles_raw(raw)
    assert df.iloc[0]["close"] == 10.5


def test_parse_candles_raw_rejects_empty_response():
    with pytest.raises(ExchangeConnectionError):
        _parse_candles_raw([])


def test_parse_candles_raw_fails_loudly_on_unknown_schema():
    raw = [{"unexpected_field": 1, "another_one": 2}]
    with pytest.raises(ExchangeConnectionError) as exc_info:
        _parse_candles_raw(raw)
    assert "schema" in str(exc_info.value).lower()


def test_parse_candles_raw_sorts_out_of_order_input():
    raw = [
        {"t": 2000, "o": "2", "h": "2", "l": "2", "c": "2", "v": "1"},
        {"t": 1000, "o": "1", "h": "1", "l": "1", "c": "1", "v": "1"},
    ]
    df = _parse_candles_raw(raw)
    assert df["timestamp"].tolist() == [1000, 2000]
