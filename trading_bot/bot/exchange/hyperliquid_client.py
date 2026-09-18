"""Live exchange client backed by the official `hyperliquid-python-sdk`.

Every method signature and response shape used here was verified directly
against `hyperliquid-python-sdk`'s installed source (function signatures
via `inspect.signature`, response shapes via the library's own docstrings)
-- not guessed, and not taken second-hand from third-party docs:

    Exchange(wallet, base_url=None, ..., account_address=None, ...)
    Exchange.order(name, is_buy, sz, limit_px, order_type, reduce_only=False, cloid=None, builder=None)
    Exchange.market_open(name, is_buy, sz, px=None, slippage=0.05, cloid=None, builder=None)
    Exchange.market_close(coin, sz=None, px=None, slippage=..., ...)
    Exchange.update_leverage(leverage, name, is_cross=True)
    Info(base_url=None, skip_ws=False, ...)
    Info.candles_snapshot(name, interval, startTime, endTime)
        -> [{T: int, c: "float string", h: "float string", i: str, l: "float string",
             n: int, o: "float string", s: str, t: int, v: "float string"}, ...]
    Info.user_state(address)
        -> {assetPositions: [{position: {coin, entryPx, leverage: {type, value, rawUsd?},
             liquidationPx, marginUsed, positionValue, returnOnEquity, szi, unrealizedPnl},
             type: "oneWay"}], crossMarginSummary, marginSummary: {accountValue,
             totalMarginUsed, totalNtlPos, totalRawUsd}, withdrawable}
    Info.meta() -> {"universe": [{"name": str, "szDecimals": int, "maxLeverage": int}, ...]}

Pin the SDK version (see requirements.txt) and re-run
`scripts/verify_hyperliquid_connection.py` after any upgrade -- exchange
APIs do change over time. `_parse_candles_raw` below stays defensive
regardless: it validates the shape it gets and raises a clear error
instead of silently mis-mapping columns if a future version changes it.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional

import pandas as pd

from bot.exchange.base import (
    AccountState,
    ExchangeClient,
    OrderResult,
    PositionInfo,
    Side,
    validate_candles,
)
from bot.exchange.errors import ExchangeConnectionError, ExchangeOrderError

log = logging.getLogger("trading_bot.exchange.hyperliquid")

MAINNET_API_URL = "https://api.hyperliquid.xyz"
TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"

_TIMEFRAME_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
    "1M": 2_592_000_000,
}

# Candidate key names for OHLCV fields in the candleSnapshot response.
# Hyperliquid's documented shorthand is t/T/o/h/l/c/v/n; this list also
# tolerates a couple of plausible alternate spellings so a minor API
# change degrades to a clear error instead of silently wrong numbers.
_FIELD_CANDIDATES = {
    "timestamp": ("t", "time", "T"),
    "open": ("o", "open"),
    "high": ("h", "high"),
    "low": ("l", "low"),
    "close": ("c", "close"),
    "volume": ("v", "volume", "vlm"),
}


def base_url_for(network: str) -> str:
    return MAINNET_API_URL if network == "mainnet" else TESTNET_API_URL


def _first_present(d: dict, keys: tuple[str, ...]) -> object:
    for k in keys:
        if k in d:
            return d[k]
    raise KeyError(keys)


def _retry_call(description: str, fn, *args, max_retries: int = 3, backoff_seconds: float = 2.0, **kwargs):
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - SDK raises plain Exception/HTTPError
            last_exc = exc
            log.warning(
                "Hyperliquid call '%s' failed (attempt %d/%d): %s",
                description, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                time.sleep(backoff_seconds * attempt)
    raise ExchangeConnectionError(
        f"{description} failed after {max_retries} attempts: {last_exc}"
    ) from last_exc


def _parse_candles_raw(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        raise ExchangeConnectionError("candles_snapshot returned no data")
    rows = []
    try:
        for c in raw:
            rows.append({
                "timestamp": int(_first_present(c, _FIELD_CANDIDATES["timestamp"])),
                "open": float(_first_present(c, _FIELD_CANDIDATES["open"])),
                "high": float(_first_present(c, _FIELD_CANDIDATES["high"])),
                "low": float(_first_present(c, _FIELD_CANDIDATES["low"])),
                "close": float(_first_present(c, _FIELD_CANDIDATES["close"])),
                "volume": float(_first_present(c, _FIELD_CANDIDATES["volume"])),
            })
    except (KeyError, TypeError, ValueError) as exc:
        raise ExchangeConnectionError(
            f"candles_snapshot response shape did not match any known schema "
            f"(sample row: {raw[0]!r}): {exc}. Run "
            "scripts/verify_hyperliquid_connection.py and update "
            "bot/exchange/hyperliquid_client.py::_FIELD_CANDIDATES if the API changed."
        ) from exc
    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


class PublicMarketData:
    """Read-only public market data. No wallet, no signing, no API key --
    Hyperliquid's `/info` endpoint needs none. This is what powers the
    DRY_RUN candle feed so paper-trading reacts to real prices without
    ever touching account credentials."""

    def __init__(self, network: str = "testnet", max_retries: int = 3, retry_backoff_seconds: float = 2.0):
        from hyperliquid.info import Info

        self._info = Info(base_url_for(network), skip_ws=True)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds

    def get_candles(self, symbol: str, timeframe: str, lookback: int) -> pd.DataFrame:
        interval_ms = _TIMEFRAME_MS.get(timeframe)
        if interval_ms is None:
            raise ExchangeOrderError(f"Unsupported timeframe '{timeframe}'")
        end_time = int(time.time() * 1000)
        start_time = end_time - interval_ms * (lookback + 5)
        raw = _retry_call(
            "candles_snapshot", self._info.candles_snapshot, symbol, timeframe, start_time, end_time,
            max_retries=self._max_retries, backoff_seconds=self._retry_backoff,
        )
        df = _parse_candles_raw(raw).tail(lookback).reset_index(drop=True)
        return validate_candles(df, source="hyperliquid.candles_snapshot")


class HyperliquidClient(ExchangeClient):
    """Authenticated client: everything PublicMarketData offers, plus
    account state and order placement. Requires a signing private key --
    only ever constructed when DRY_RUN=false."""

    def __init__(
        self,
        private_key: str,
        network: str = "testnet",
        account_address: Optional[str] = None,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
    ):
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info

        base_url = base_url_for(network)
        wallet = Account.from_key(private_key)
        self._address = account_address or wallet.address
        self._network = network
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._info = Info(base_url, skip_ws=True)
        self._exchange = Exchange(wallet, base_url, account_address=self._address)
        self._meta_cache: Optional[dict] = None
        log.info("HyperliquidClient initialised: network=%s address=%s", network, self._address)

    def _retry(self, description: str, fn, *args, **kwargs):
        return _retry_call(
            description, fn, *args, max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff, **kwargs,
        )

    # -- asset metadata / rounding -----------------------------------------------------------
    def _asset_meta(self, symbol: str) -> dict:
        if self._meta_cache is None:
            self._meta_cache = self._retry("meta", self._info.meta)
        for asset in self._meta_cache.get("universe", []):
            if asset.get("name") == symbol:
                return asset
        raise ExchangeOrderError(f"Unknown symbol '{symbol}' -- not in Hyperliquid's asset universe")

    def _round_size(self, symbol: str, size: float) -> float:
        sz_decimals = int(self._asset_meta(symbol).get("szDecimals", 4))
        factor = 10 ** sz_decimals
        return math.floor(size * factor) / factor

    def _round_price(self, symbol: str, price: float) -> float:
        # Hyperliquid perp price rule: <= 5 significant figures AND
        # <= (6 - szDecimals) decimal places.
        sz_decimals = int(self._asset_meta(symbol).get("szDecimals", 4))
        max_decimals = max(0, 6 - sz_decimals)
        sig_rounded = float(f"{price:.5g}")
        return round(sig_rounded, max_decimals)

    def _capped_leverage(self, symbol: str, requested_leverage: int) -> int:
        exchange_max = int(self._asset_meta(symbol).get("maxLeverage", requested_leverage))
        return max(1, min(requested_leverage, exchange_max))

    # -- candles -----------------------------------------------------------
    def get_candles(self, symbol: str, timeframe: str, lookback: int) -> pd.DataFrame:
        interval_ms = _TIMEFRAME_MS.get(timeframe)
        if interval_ms is None:
            raise ExchangeOrderError(f"Unsupported timeframe '{timeframe}'")
        end_time = int(time.time() * 1000)
        start_time = end_time - interval_ms * (lookback + 5)
        raw = self._retry(
            "candles_snapshot", self._info.candles_snapshot, symbol, timeframe, start_time, end_time,
        )
        df = _parse_candles_raw(raw).tail(lookback).reset_index(drop=True)
        return validate_candles(df, source="hyperliquid.candles_snapshot")

    # -- account -----------------------------------------------------------
    def get_account_state(self) -> AccountState:
        raw = self._retry("user_state", self._info.user_state, self._address)
        margin_summary = raw.get("marginSummary", {}) or {}
        equity = float(margin_summary.get("accountValue", 0.0))
        withdrawable = float(raw.get("withdrawable", 0.0))
        return AccountState(equity_usd=equity, withdrawable_usd=withdrawable)

    def set_leverage(self, symbol: str, leverage: int) -> None:
        safe_leverage = self._capped_leverage(symbol, leverage)
        if safe_leverage != leverage:
            log.warning(
                "%s: requested leverage %dx exceeds exchange max, using %dx instead",
                symbol, leverage, safe_leverage,
            )
        self._retry("update_leverage", self._exchange.update_leverage, safe_leverage, symbol, True)

    # -- orders -----------------------------------------------------------
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
        is_buy = side == "LONG"
        rounded_size = self._round_size(symbol, size)
        if rounded_size <= 0:
            return OrderResult(False, f"{symbol}: size rounds down to zero at this symbol's lot size")

        sl_price = self._round_price(symbol, stop_loss_price)
        tp_price = self._round_price(symbol, take_profit_price)

        try:
            entry_result = self._retry(
                "market_open", self._exchange.market_open,
                symbol, is_buy, rounded_size, None, slippage_pct / 100.0,
            )
        except ExchangeConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExchangeOrderError(f"market_open failed for {symbol}: {exc}") from exc

        try:
            stop_order_type = {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}}
            tp_order_type = {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}}
            self._retry(
                "attach_stop_loss", self._exchange.order,
                symbol, not is_buy, rounded_size, sl_price, stop_order_type, True,
            )
            self._retry(
                "attach_take_profit", self._exchange.order,
                symbol, not is_buy, rounded_size, tp_price, tp_order_type, True,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "%s: entry filled but SL/TP attachment failed (%s). Position is "
                "UNPROTECTED -- attempting an emergency market close.", symbol, exc,
            )
            try:
                self._exchange.market_close(symbol)
                log.warning("%s: emergency close after failed SL/TP attachment succeeded.", symbol)
            except Exception as close_exc:  # noqa: BLE001
                log.critical(
                    "%s: emergency close ALSO failed (%s). MANUAL INTERVENTION REQUIRED -- "
                    "check your position on the Hyperliquid UI immediately.", symbol, close_exc,
                )
            raise ExchangeOrderError(
                f"{symbol}: SL/TP attachment failed after entry filled: {exc}"
            ) from exc

        return OrderResult(True, "entry + SL/TP submitted", raw=entry_result, fill_price=entry_price_hint)

    def get_open_position(self, symbol: str, latest_candle: "pd.Series | None" = None) -> Optional[PositionInfo]:
        raw = self._retry("user_state", self._info.user_state, self._address)
        for entry in raw.get("assetPositions", []) or []:
            pos = entry.get("position", {}) or {}
            if pos.get("coin") != symbol:
                continue
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            leverage_field = pos.get("leverage", {}) or {}
            leverage_val = leverage_field.get("value", 1) if isinstance(leverage_field, dict) else leverage_field
            return PositionInfo(
                symbol=symbol,
                side="LONG" if szi > 0 else "SHORT",
                size=abs(szi),
                entry_price=float(pos.get("entryPx", 0) or 0),
                leverage=int(leverage_val or 1),
                unrealized_pnl=float(pos.get("unrealizedPnl", 0) or 0),
            )
        return None

    def close_position(self, symbol: str) -> OrderResult:
        try:
            result = self._retry("market_close", self._exchange.market_close, symbol)
            return OrderResult(True, "position closed", raw=result)
        except Exception as exc:  # noqa: BLE001
            raise ExchangeOrderError(f"market_close failed for {symbol}: {exc}") from exc
