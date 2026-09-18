from __future__ import annotations

import pytest

from bot.config import (
    ABSOLUTE_MAX_LEVERAGE,
    ABSOLUTE_MAX_RISK_PER_TRADE_PCT,
    CapitalConfig,
    ConfigError,
    EnvSettings,
    StrategyConfig,
)


def test_default_strategy_config_is_valid():
    cfg = StrategyConfig()
    cfg.validate()  # should not raise


def test_capital_config_rejects_leverage_above_absolute_ceiling():
    cfg = CapitalConfig(max_leverage=ABSOLUTE_MAX_LEVERAGE + 1)
    with pytest.raises(ConfigError):
        cfg.validate()


def test_capital_config_rejects_risk_above_absolute_ceiling():
    cfg = CapitalConfig(risk_per_trade_pct=ABSOLUTE_MAX_RISK_PER_TRADE_PCT + 1)
    with pytest.raises(ConfigError):
        cfg.validate()


def test_capital_config_rejects_tiny_capital():
    cfg = CapitalConfig(initial_capital_usd=1.0)
    with pytest.raises(ConfigError):
        cfg.validate()


# `load_dotenv(..., override=False)` -- the default, and the right choice
# in production so a real shell-exported var wins over the .env file --
# only ever SETS a key that isn't already in os.environ. That means it
# silently no-ops across tests in the same process if a previous test left
# a value behind, since load_dotenv's mutation of os.environ bypasses
# pytest's monkeypatch tracking entirely. Every test below therefore
# explicitly delenv/setenv all three keys up front: that both clears
# whatever a previous test's load_dotenv call left behind AND registers
# proper teardown, regardless of what load_dotenv does to them mid-test.
_ENV_KEYS = ("HL_NETWORK", "DRY_RUN", "HYPERLIQUID_PRIVATE_KEY")


def _clear_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_env_settings_default_is_dry_run_testnet(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    settings = EnvSettings.load(env_file=str(empty_env))
    assert settings.network == "testnet"
    assert settings.dry_run is True
    assert settings.is_live is False


def test_env_settings_refuses_live_without_private_key(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("DRY_RUN=false\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        EnvSettings.load(env_file=str(env_file))


def test_env_settings_refuses_malformed_private_key(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("DRY_RUN=false\nHYPERLIQUID_PRIVATE_KEY=not_a_real_key\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        EnvSettings.load(env_file=str(env_file))


def test_env_settings_accepts_live_with_valid_key_format(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    fake_key = "0x" + "ab" * 32
    env_file = tmp_path / ".env"
    env_file.write_text(f"DRY_RUN=false\nHYPERLIQUID_PRIVATE_KEY={fake_key}\n", encoding="utf-8")
    settings = EnvSettings.load(env_file=str(env_file))
    assert settings.is_live is True
    assert settings.private_key == fake_key


def test_strategy_config_from_yaml(tmp_path):
    yaml_content = """
capital:
  initial_capital_usd: 75.0
  risk_per_trade_pct: 1.5
  max_leverage: 2
market:
  symbols: ["ETH"]
  timeframe: "5m"
"""
    path = tmp_path / "settings.yaml"
    path.write_text(yaml_content, encoding="utf-8")
    cfg = StrategyConfig.from_yaml(path)
    assert cfg.capital.initial_capital_usd == 75.0
    assert cfg.capital.max_leverage == 2
    assert cfg.market.symbols == ("ETH",)
    assert cfg.market.timeframe == "5m"


def test_strategy_config_from_yaml_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        StrategyConfig.from_yaml(tmp_path / "does_not_exist.yaml")
