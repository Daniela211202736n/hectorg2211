from __future__ import annotations

import pandas as pd
import pytest

from bot.exchange.dry_run import DryRunExchangeClient
from tests.conftest import make_ohlcv


def _static_provider(df: pd.DataFrame):
    def provider(symbol, timeframe, lookback):
        return df.tail(lookback).reset_index(drop=True)
    return provider


def test_open_and_stop_loss_hit():
    df = make_ohlcv([100.0] * 5)
    client = DryRunExchangeClient(candle_provider=_static_provider(df), starting_equity_usd=50.0, taker_fee_pct=0.0)
    client.set_leverage("BTC", 2)
    result = client.open_bracket_position(
        "BTC", "LONG", size=1.0, entry_price_hint=100.0,
        stop_loss_price=95.0, take_profit_price=110.0, slippage_pct=0.5,
    )
    assert result.success

    pos = client.get_open_position("BTC")
    assert pos is not None and pos.side == "LONG"

    losing_candle = pd.Series({"open": 96.0, "high": 96.5, "low": 94.0, "close": 94.5})
    pos_after = client.get_open_position("BTC", latest_candle=losing_candle)
    assert pos_after is None
    assert len(client.closed_trades) == 1
    trade = client.closed_trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(95.0)
    assert trade.realized_pnl_usd == pytest.approx(-5.0)  # (95-100)*1.0, zero fees
    assert client.get_account_state().equity_usd == pytest.approx(45.0)


def test_open_and_take_profit_hit():
    df = make_ohlcv([100.0] * 5)
    client = DryRunExchangeClient(candle_provider=_static_provider(df), starting_equity_usd=50.0, taker_fee_pct=0.0)
    client.set_leverage("BTC", 2)  # $100 notional needs >=2x leverage to fit $50 equity
    client.open_bracket_position(
        "BTC", "SHORT", size=1.0, entry_price_hint=100.0,
        stop_loss_price=105.0, take_profit_price=90.0, slippage_pct=0.5,
    )
    winning_candle = pd.Series({"open": 92.0, "high": 93.0, "low": 88.0, "close": 89.0})
    pos_after = client.get_open_position("BTC", latest_candle=winning_candle)
    assert pos_after is None
    trade = client.closed_trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.realized_pnl_usd == pytest.approx(10.0)  # (100-90)*1.0 short


def test_stop_loss_takes_priority_when_both_hit_same_candle():
    df = make_ohlcv([100.0] * 5)
    client = DryRunExchangeClient(candle_provider=_static_provider(df), starting_equity_usd=50.0, taker_fee_pct=0.0)
    client.set_leverage("BTC", 2)
    client.open_bracket_position(
        "BTC", "LONG", size=1.0, entry_price_hint=100.0,
        stop_loss_price=95.0, take_profit_price=105.0, slippage_pct=0.5,
    )
    wild_candle = pd.Series({"open": 100.0, "high": 106.0, "low": 94.0, "close": 96.0})
    client.get_open_position("BTC", latest_candle=wild_candle)
    assert client.closed_trades[0].exit_reason == "stop_loss"


def test_cannot_open_second_position_on_same_symbol():
    df = make_ohlcv([100.0] * 5)
    client = DryRunExchangeClient(candle_provider=_static_provider(df), starting_equity_usd=50.0)
    client.set_leverage("BTC", 3)  # leave headroom for the default (non-zero) taker fee
    first = client.open_bracket_position("BTC", "LONG", 1.0, 100.0, 95.0, 110.0, 0.5)
    assert first.success  # sanity check: the first order actually opened
    second = client.open_bracket_position("BTC", "LONG", 1.0, 100.0, 95.0, 110.0, 0.5)
    assert not second.success
    assert "already open" in second.message


def test_fees_are_deducted_from_equity():
    df = make_ohlcv([100.0] * 5)
    client = DryRunExchangeClient(
        candle_provider=_static_provider(df), starting_equity_usd=50.0, taker_fee_pct=1.0,
    )
    client.set_leverage("BTC", 3)  # $100 notional / 3x = $33.33 margin + $1 fee, fits in $50
    client.open_bracket_position("BTC", "LONG", 1.0, 100.0, 95.0, 110.0, 0.5)
    # entry fee = notional(100) * 1% = 1.0
    assert client.get_account_state().equity_usd == pytest.approx(49.0)
