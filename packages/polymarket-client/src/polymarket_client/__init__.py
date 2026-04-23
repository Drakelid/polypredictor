"""Polymarket client — Gamma + CLOB + Data REST APIs, plus WSS.

Entry point: :class:`PolymarketClient`.
"""

from .cache import TTLCache
from .client import PolymarketClient
from .clob import ClobClient
from .data import DataClient
from .errors import (
    PolymarketAPIError,
    PolymarketAuthError,
    PolymarketNotFoundError,
    PolymarketRateLimitError,
)
from .gamma import GammaClient
from .models import (
    Book,
    BookLevel,
    HolderEntry,
    LeaderboardEntry,
    Market,
    Midpoint,
    Position,
    PriceHistoryBucket,
    PricePoint,
    Trade,
)
from .rate_limiter import TokenBucket
from .wss import MarketWssManager, UserAuth, UserWssManager, WssCallback, WssEvent

__all__ = [
    "Book",
    "BookLevel",
    "ClobClient",
    "DataClient",
    "GammaClient",
    "HolderEntry",
    "LeaderboardEntry",
    "Market",
    "MarketWssManager",
    "Midpoint",
    "PolymarketAPIError",
    "PolymarketAuthError",
    "PolymarketClient",
    "PolymarketNotFoundError",
    "PolymarketRateLimitError",
    "Position",
    "PriceHistoryBucket",
    "PricePoint",
    "TTLCache",
    "TokenBucket",
    "Trade",
    "UserAuth",
    "UserWssManager",
    "WssCallback",
    "WssEvent",
]
