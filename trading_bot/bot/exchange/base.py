"""Exchange-agnostic interface the engine talks to.

Both the DryRunExchangeClient (simulation, no network writes) and the real
HyperliquidClient implement this ABC identically from the engine's point of
view -- the engine never branches on which one it has.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

Side = Literal["LONG", "SHORT"]

REQUIRED_CANDLE_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class AccountState:
    equity_usd: float
    withdrawable_usd: float


@dataclass(frozen=True)
class PositionInfo:
    symbol: str
    side: Side
    size: float
    entry_price: float
    leverage: int
    unrealized_pnl: float = 0.0


@dataclass(frozen=True)
class OrderResult:
    success: bool
    message: str
    raw: object = None
    fill_price: Optional[float] = None


class ExchangeClient(ABC):
    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, lookback: int) -> pd.DataFrame:
        """Return a DataFrame with columns REQUIRED_CANDLE_COLUMNS, oldest row
        first, at most `lookback` rows, most recent candle last."""

    @abstractmethod
    def get_account_state(self) -> AccountState:
        ...

    @abstractmethod
    def set_leverage(self, symbol: str, leverage: int) -> None:
        ...

    @abstractmethod
    def open_bracket_position(
        self,
        symbol: str,
        side: Side,
        size: float,
        entry_price_hint: float,
        stop_loss_price: float,
        take_profit_price: float,
        slippage_pct: float,
    ) -> OrderResult:
        """Open a position with stop-loss and take-profit attached.

        entry_price_hint is the last known price, used for market-order
        slippage bounds and (in dry-run) as the simulated fill price -- the
        real exchange fills at the actual market price, which may differ
        slightly.
        """

    @abstractmethod
    def get_open_position(self, symbol: str, latest_candle: "pd.Series | None" = None) -> Optional[PositionInfo]:
        """Return the current open position for `symbol`, or None if flat.

        `latest_candle` is only consulted by the dry-run client (to check
        whether the simulated stop-loss/take-profit would have triggered
        within that candle's high/low range); the live client ignores it
        and asks the exchange directly.
        """

    @abstractmethod
    def close_position(self, symbol: str) -> OrderResult:
        """Force-close any open position at market, e.g. on shutdown or
        kill-switch. Not part of the normal SL/TP flow."""


def validate_candles(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Fail loudly rather than silently trading on a malformed feed."""
    missing = [c for c in REQUIRED_CANDLE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{source}: candle data missing columns {missing}")
    if df.empty:
        raise ValueError(f"{source}: candle data is empty")
    if df[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError(f"{source}: candle data contains NaN prices")
    return df
