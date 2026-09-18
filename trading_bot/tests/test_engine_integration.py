"""Full-cycle engine test: flat -> signal -> sized+opened position -> stop
-loss triggers -> detected closed -> logged to the trade ledger.

The strategy's own indicator math is already covered by test_signals.py
and test_indicators.py in isolation; here `generate_signal` is
monkeypatched so this test verifies the ENGINE's wiring (sizing, order
placement, position tracking, close detection, ledger + state persistence)
rather than re-deriving a natural signal from noisy synthetic candles.
"""
from __future__ import annotations

import json

import pytest

from bot.analysis.signals import Action, Signal
from bot.config import (
    AppConfig,
    CapitalConfig,
    EnvSettings,
    ExecutionConfig,
    FeesConfig,
    IndicatorConfig,
    MarketConfig,
    StrategyConfig,
    TradeConfig,
)
from bot.core.control import ControlCommand
from bot.core.engine import TradingEngine
from bot.exchange.dry_run import DryRunExchangeClient
from tests.conftest import make_ohlcv


def _build_config(tmp_path) -> AppConfig:
    env = EnvSettings(network="testnet", dry_run=True, state_dir=str(tmp_path / "runtime"))
    strategy = StrategyConfig(
        capital=CapitalConfig(
            initial_capital_usd=50.0, risk_per_trade_pct=2.0, max_leverage=3,
            liquidation_safety_factor=0.5, max_daily_loss_pct=6.0,
            max_concurrent_positions=1, min_order_notional_usd=1.0,
        ),
        market=MarketConfig(symbols=("BTC",), timeframe="1m", candle_lookback=30),
        indicators=IndicatorConfig(),
        trade=TradeConfig(stop_loss_pct=1.5, risk_reward_ratio=1.5, slippage_pct=0.5),
        execution=ExecutionConfig(
            loop_interval_seconds=60, heartbeat_interval_seconds=300,
            max_consecutive_errors=3, error_backoff_seconds=1,
        ),
        fees=FeesConfig(taker_fee_pct=0.0, maker_fee_pct=0.0),
    )
    return AppConfig(env=env, strategy=strategy)


def test_engine_full_cycle_open_then_stop_loss(tmp_path, monkeypatch):
    config = _build_config(tmp_path)
    df = make_ohlcv([100.0] * 30)

    def candle_provider(symbol, timeframe, lookback):
        return df.tail(lookback).reset_index(drop=True)

    exchange = DryRunExchangeClient(
        candle_provider=candle_provider,
        starting_equity_usd=config.strategy.capital.initial_capital_usd,
        taker_fee_pct=0.0, maker_fee_pct=0.0,
    )

    calls = {"n": 0}

    def fake_generate_signal(frame, cfg):
        calls["n"] += 1
        if calls["n"] == 1:
            return Signal(Action.LONG, "forced-for-test", price=100.0, rsi=25, macd_hist=0.1, bb_lower=99, bb_upper=101)
        return Signal(Action.NONE, "already positioned", price=100.0)

    monkeypatch.setattr("bot.core.engine.generate_signal", fake_generate_signal)

    log_dir = tmp_path / "logs"
    engine = TradingEngine(
        config=config, exchange=exchange, state_dir=tmp_path / "runtime", log_dir=log_dir,
    )

    # Tick 1: forced LONG signal -> sized and opened via plan_position + DryRunExchangeClient.
    engine.run_iterations(1)
    pos = exchange.get_open_position("BTC")
    assert pos is not None
    assert pos.side == "LONG"
    assert engine.risk_manager.state.open_positions == 1
    # equity=50, risk=2% -> $1 risk; stop=1.5% -> notional ~66.67 -> size ~0.6667 BTC @ $100.
    assert pos.size == pytest.approx(0.666667, rel=1e-4)
    assert pos.leverage == 2

    # Append a candle whose low blows through the 1.5% stop-loss (98.5).
    losing_row = {
        "timestamp": int(df["timestamp"].iloc[-1]) + 60_000,
        "open": 99.0, "high": 99.5, "low": 96.0, "close": 96.5, "volume": 100.0,
    }
    df.loc[len(df)] = losing_row

    # Tick 2: engine must detect the now-closed position and log it.
    engine.run_iterations(1)
    assert exchange.get_open_position("BTC") is None
    assert engine.risk_manager.state.open_positions == 0

    ledger_path = log_dir / "trades.jsonl"
    assert ledger_path.exists()
    lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["symbol"] == "BTC"
    assert record["side"] == "LONG"
    assert record["exit_reason"] == "stop_loss"
    assert record["realized_pnl_usd"] < 0
    assert record["realized_pnl_usd"] == -1.0  # exactly the planned $1 risk (zero fees in this test)
    assert record["equity_after_usd"] == 49.0

    state_path = tmp_path / "runtime" / "state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["snapshot"]["open_positions"] == 0
    assert state["snapshot"]["equity_usd"] == 49.0


def test_engine_pause_prevents_new_entries(tmp_path, monkeypatch):
    config = _build_config(tmp_path)
    df = make_ohlcv([100.0] * 30)

    def candle_provider(symbol, timeframe, lookback):
        return df.tail(lookback).reset_index(drop=True)

    exchange = DryRunExchangeClient(
        candle_provider=candle_provider,
        starting_equity_usd=config.strategy.capital.initial_capital_usd,
    )

    def fake_generate_signal(frame, cfg):
        return Signal(Action.LONG, "forced-for-test", price=100.0, rsi=25, macd_hist=0.1, bb_lower=99, bb_upper=101)

    monkeypatch.setattr("bot.core.engine.generate_signal", fake_generate_signal)

    engine = TradingEngine(
        config=config, exchange=exchange, state_dir=tmp_path / "runtime", log_dir=tmp_path / "logs",
    )
    engine.control.send(ControlCommand.PAUSE)

    engine.run_iterations(1)
    assert exchange.get_open_position("BTC") is None
    assert engine.risk_manager.state.open_positions == 0


def test_engine_kill_switch_blocks_new_entry_after_daily_loss(tmp_path, monkeypatch):
    config = _build_config(tmp_path)
    df = make_ohlcv([100.0] * 30)

    def candle_provider(symbol, timeframe, lookback):
        return df.tail(lookback).reset_index(drop=True)

    exchange = DryRunExchangeClient(
        candle_provider=candle_provider,
        starting_equity_usd=config.strategy.capital.initial_capital_usd,
    )

    def fake_generate_signal(frame, cfg):
        return Signal(Action.LONG, "forced-for-test", price=100.0, rsi=25, macd_hist=0.1, bb_lower=99, bb_upper=101)

    monkeypatch.setattr("bot.core.engine.generate_signal", fake_generate_signal)

    engine = TradingEngine(
        config=config, exchange=exchange, state_dir=tmp_path / "runtime", log_dir=tmp_path / "logs",
    )
    # Simulate a >6% daily loss having already happened (e.g. from a prior trade).
    engine.risk_manager.update_equity(46.0)
    assert engine.risk_manager.state.kill_switch_active

    engine.run_iterations(1)
    assert exchange.get_open_position("BTC") is None
