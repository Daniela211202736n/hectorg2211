"""Exchange-layer exceptions.

Kept deliberately narrow: the engine only needs to distinguish "transient,
worth retrying/continuing the loop" from "the order itself was rejected or
unsafe", it doesn't need a large exception hierarchy.
"""


class ExchangeError(Exception):
    """Base class for all exchange-layer errors."""


class ExchangeConnectionError(ExchangeError):
    """Network/API call failed after exhausting retries. Transient by nature
    -- the engine should log it, back off, and keep running, not crash."""


class ExchangeOrderError(ExchangeError):
    """An order was rejected, malformed, or could not be safely placed
    (e.g. stop-loss/take-profit attachment failed)."""
