"""Configuration loading and validation.

Two sources, deliberately kept separate:

- Environment variables (.env)  -> secrets + which network to hit + master
  safety switches. Never checked into git.
- config/settings.yaml          -> strategy/risk parameters. Safe to version
  control, safe to tweak without touching any code.

Hard safety ceilings (ABSOLUTE_MAX_LEVERAGE, ABSOLUTE_MAX_RISK_PCT, ...) are
enforced here regardless of what settings.yaml says, so a typo in the YAML
file can't silently authorize an account-destroying trade.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Hard safety ceilings. These are NOT configurable via settings.yaml on
# purpose -- they exist to catch operator mistakes, not to be tuned away.
# ---------------------------------------------------------------------------
ABSOLUTE_MAX_LEVERAGE = 20
ABSOLUTE_MAX_RISK_PER_TRADE_PCT = 10.0
ABSOLUTE_MAX_DAILY_LOSS_PCT = 50.0
MIN_INITIAL_CAPITAL_USD = 5.0

VALID_NETWORKS = ("testnet", "mainnet")
VALID_TIMEFRAMES = (
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1M",
)


class ConfigError(ValueError):
    """Raised when configuration is missing or fails a safety check."""


def _get_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class EnvSettings:
    """Secrets and network/safety switches. Sourced from environment/.env."""

    network: str = "testnet"
    dry_run: bool = True
    private_key: str | None = None
    account_address: str | None = None
    log_level: str = "INFO"
    settings_path: str = "config/settings.yaml"
    state_dir: str = "runtime"

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "EnvSettings":
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            # Look for a .env next to the project root (trading_bot/).
            default_path = Path(__file__).resolve().parent.parent / ".env"
            load_dotenv(default_path, override=False)

        network = os.getenv("HL_NETWORK", "testnet").strip().lower()
        dry_run = _get_bool(os.getenv("DRY_RUN"), default=True)

        settings = cls(
            network=network,
            dry_run=dry_run,
            private_key=os.getenv("HYPERLIQUID_PRIVATE_KEY") or None,
            account_address=os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS") or None,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            settings_path=os.getenv("STRATEGY_CONFIG_PATH", "config/settings.yaml"),
            state_dir=os.getenv("BOT_STATE_DIR", "runtime"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.network not in VALID_NETWORKS:
            raise ConfigError(
                f"HL_NETWORK must be one of {VALID_NETWORKS}, got '{self.network}'"
            )
        if not self.dry_run:
            if not self.private_key:
                raise ConfigError(
                    "DRY_RUN=false (live trading) but HYPERLIQUID_PRIVATE_KEY is not set. "
                    "Refusing to start in live mode without a signing key."
                )
            if not self.private_key.startswith("0x") or len(self.private_key) != 66:
                raise ConfigError(
                    "HYPERLIQUID_PRIVATE_KEY does not look like a valid 32-byte hex private "
                    "key (expected format: 0x + 64 hex chars)."
                )

    @property
    def is_live(self) -> bool:
        """True only when this process is allowed to place real orders."""
        return not self.dry_run


@dataclass(frozen=True)
class CapitalConfig:
    initial_capital_usd: float = 50.0
    risk_per_trade_pct: float = 2.0
    max_leverage: int = 3
    liquidation_safety_factor: float = 0.5
    max_daily_loss_pct: float = 6.0
    max_concurrent_positions: int = 1
    min_order_notional_usd: float = 10.0

    def validate(self) -> None:
        if self.initial_capital_usd < MIN_INITIAL_CAPITAL_USD:
            raise ConfigError(
                f"initial_capital_usd must be >= {MIN_INITIAL_CAPITAL_USD} USD"
            )
        if not (0 < self.risk_per_trade_pct <= ABSOLUTE_MAX_RISK_PER_TRADE_PCT):
            raise ConfigError(
                f"capital.risk_per_trade_pct must be in (0, {ABSOLUTE_MAX_RISK_PER_TRADE_PCT}]"
            )
        if not (1 <= self.max_leverage <= ABSOLUTE_MAX_LEVERAGE):
            raise ConfigError(
                f"capital.max_leverage must be in [1, {ABSOLUTE_MAX_LEVERAGE}]"
            )
        if not (0 < self.liquidation_safety_factor <= 0.9):
            raise ConfigError("capital.liquidation_safety_factor must be in (0, 0.9]")
        if not (0 < self.max_daily_loss_pct <= ABSOLUTE_MAX_DAILY_LOSS_PCT):
            raise ConfigError(
                f"capital.max_daily_loss_pct must be in (0, {ABSOLUTE_MAX_DAILY_LOSS_PCT}]"
            )
        if self.max_concurrent_positions < 1:
            raise ConfigError("capital.max_concurrent_positions must be >= 1")
        if self.min_order_notional_usd <= 0:
            raise ConfigError("capital.min_order_notional_usd must be > 0")


@dataclass(frozen=True)
class MarketConfig:
    symbols: tuple[str, ...] = ("BTC",)
    timeframe: str = "15m"
    candle_lookback: int = 200

    def validate(self) -> None:
        if not self.symbols:
            raise ConfigError("market.symbols must not be empty")
        if self.timeframe not in VALID_TIMEFRAMES:
            raise ConfigError(f"market.timeframe must be one of {VALID_TIMEFRAMES}")
        if self.candle_lookback < 50:
            raise ConfigError("market.candle_lookback must be >= 50 (indicators need warm-up)")


@dataclass(frozen=True)
class IndicatorConfig:
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    bb_tolerance_pct: float = 0.5

    def validate(self) -> None:
        if self.rsi_period < 2:
            raise ConfigError("indicators.rsi_period must be >= 2")
        if not (0 < self.rsi_oversold < self.rsi_overbought < 100):
            raise ConfigError("indicators: need 0 < rsi_oversold < rsi_overbought < 100")
        if self.macd_fast >= self.macd_slow:
            raise ConfigError("indicators.macd_fast must be < macd_slow")
        if self.bb_period < 2 or self.bb_std <= 0:
            raise ConfigError("indicators.bb_period must be >= 2 and bb_std > 0")


@dataclass(frozen=True)
class TradeConfig:
    stop_loss_pct: float = 1.5
    risk_reward_ratio: float = 1.5
    slippage_pct: float = 0.5

    def validate(self) -> None:
        if not (0 < self.stop_loss_pct <= 20):
            raise ConfigError("trade.stop_loss_pct must be in (0, 20]")
        if self.risk_reward_ratio <= 0:
            raise ConfigError("trade.risk_reward_ratio must be > 0")
        if not (0 <= self.slippage_pct <= 5):
            raise ConfigError("trade.slippage_pct must be in [0, 5]")

    @property
    def take_profit_pct(self) -> float:
        return self.stop_loss_pct * self.risk_reward_ratio


@dataclass(frozen=True)
class ExecutionConfig:
    loop_interval_seconds: int = 60
    heartbeat_interval_seconds: int = 300
    max_consecutive_errors: int = 5
    error_backoff_seconds: int = 30

    def validate(self) -> None:
        if self.loop_interval_seconds < 5:
            raise ConfigError("execution.loop_interval_seconds must be >= 5")
        if self.heartbeat_interval_seconds < self.loop_interval_seconds:
            raise ConfigError(
                "execution.heartbeat_interval_seconds should be >= loop_interval_seconds"
            )
        if self.max_consecutive_errors < 1:
            raise ConfigError("execution.max_consecutive_errors must be >= 1")


@dataclass(frozen=True)
class FeesConfig:
    taker_fee_pct: float = 0.035
    maker_fee_pct: float = 0.01

    def validate(self) -> None:
        if not (0 <= self.maker_fee_pct <= 1) or not (0 <= self.taker_fee_pct <= 1):
            raise ConfigError("fees.*_fee_pct must be in [0, 1] (percent, e.g. 0.035 = 0.035%)")


@dataclass(frozen=True)
class StrategyConfig:
    capital: CapitalConfig = field(default_factory=CapitalConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    trade: TradeConfig = field(default_factory=TradeConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    fees: FeesConfig = field(default_factory=FeesConfig)

    def validate(self) -> None:
        for section in (
            self.capital, self.market, self.indicators,
            self.trade, self.execution, self.fees,
        ):
            section.validate()

    @classmethod
    def from_yaml(cls, path: str | Path) -> "StrategyConfig":
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"Strategy config file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}

        def section(name: str, klass):
            data = raw.get(name, {}) or {}
            try:
                return klass(**data)
            except TypeError as exc:
                raise ConfigError(f"Invalid key in settings.yaml section '{name}': {exc}") from exc

        market_raw = raw.get("market", {}) or {}
        if "symbols" in market_raw:
            market_raw = {**market_raw, "symbols": tuple(market_raw["symbols"])}

        cfg = cls(
            capital=section("capital", CapitalConfig),
            market=MarketConfig(**market_raw),
            indicators=section("indicators", IndicatorConfig),
            trade=section("trade", TradeConfig),
            execution=section("execution", ExecutionConfig),
            fees=section("fees", FeesConfig),
        )
        cfg.validate()
        return cfg


@dataclass(frozen=True)
class AppConfig:
    """Everything the bot needs, assembled from env + yaml."""

    env: EnvSettings
    strategy: StrategyConfig

    @classmethod
    def load(cls, env_file: str | Path | None = None, settings_path: str | Path | None = None) -> "AppConfig":
        env = EnvSettings.load(env_file)
        yaml_path = settings_path or env.settings_path
        # Resolve relative to the trading_bot project root, not the CWD.
        project_root = Path(__file__).resolve().parent.parent
        candidate = Path(yaml_path)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        strategy = StrategyConfig.from_yaml(candidate)
        return cls(env=env, strategy=strategy)
