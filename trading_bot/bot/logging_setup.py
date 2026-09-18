"""Logging: console + rotating file handler + a structured trade ledger
(JSON Lines, one line per closed trade) + a periodic heartbeat.

Kept as plain stdlib `logging` -- no extra dependency needed for something
this codebase only needs to do well, not fancily.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def setup_logging(log_dir: str | Path, level: str = "INFO") -> logging.Logger:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("trading_bot")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


@dataclass(frozen=True)
class TradeRecord:
    timestamp: str
    symbol: str
    side: str
    reason_opened: str
    entry_price: float
    exit_price: float
    size: float
    leverage: int
    notional_usd: float
    margin_used_usd: float
    fees_paid_usd: float
    realized_pnl_usd: float
    equity_after_usd: float
    exit_reason: str


class TradeLedger:
    """Appends one JSON line per closed trade to logs/trades.jsonl.

    JSON Lines (not a single JSON array) so the file is always valid to
    tail/append to even if the process is killed mid-write.
    """

    def __init__(self, log_dir: str | Path):
        self._path = Path(log_dir) / "trades.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, trade: TradeRecord) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(trade)) + "\n")

    @property
    def path(self) -> Path:
        return self._path


class Heartbeat:
    """Logs a one-line status summary every `interval_seconds`, without
    blocking the caller -- `maybe_beat()` is cheap to call every loop tick
    and only actually logs once the interval has elapsed."""

    def __init__(self, logger: logging.Logger, interval_seconds: int):
        self._logger = logger
        self._interval = interval_seconds
        self._last_beat = 0.0

    def maybe_beat(self, status: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_beat < self._interval:
            return
        self._last_beat = now
        parts = ", ".join(f"{k}={v}" for k, v in status.items())
        self._logger.info("HEARTBEAT | %s", parts)
