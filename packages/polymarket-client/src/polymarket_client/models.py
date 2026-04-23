"""Pydantic models for Polymarket API responses.

These are lenient intentionally — Polymarket occasionally adds fields or
returns slightly different shapes. We keep ``extra='ignore'`` and use
``Optional`` for fields that aren't always present.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --- Gamma ------------------------------------------------------------------


class Market(_Base):
    """Subset of the Gamma ``/markets`` shape used downstream."""

    condition_id: str = Field(alias="conditionId")
    event_id: str | None = Field(default=None, alias="eventId")
    question: str
    slug: str
    description: str = ""
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    active: bool = False
    closed: bool = False
    archived: bool = False
    volume: float = 0.0
    liquidity: float = 0.0
    open_interest: float = Field(default=0.0, alias="openInterest")
    end_date: datetime | None = Field(default=None, alias="endDate")
    resolution_source: str | None = Field(default=None, alias="resolutionSource")
    # Polymarket returns token ids in either ``clobTokenIds`` (stringified JSON
    # array in some responses) or a nested tokens array; the sub-client
    # normalizes both to a list of strings.
    token_ids: list[str] = Field(default_factory=list)


class Event(_Base):
    id: str
    slug: str
    title: str
    tags: list[str] = Field(default_factory=list)
    markets: list[Market] = Field(default_factory=list)


# --- CLOB -------------------------------------------------------------------


class BookLevel(_Base):
    price: float
    size: float


class Book(_Base):
    token_id: str
    market: str | None = None  # condition_id when present
    bids: list[BookLevel] = Field(default_factory=list)
    asks: list[BookLevel] = Field(default_factory=list)
    hash: str | None = None
    timestamp: int | None = None


class Midpoint(_Base):
    token_id: str
    mid: float


class PricePoint(_Base):
    token_id: str
    side: str  # 'BUY' (best bid) or 'SELL' (best ask)
    price: float


class PriceHistoryBucket(_Base):
    t: int  # epoch seconds
    p: float  # price (usually close)


class Trade(_Base):
    trade_id: str | None = None
    token_id: str
    market: str | None = None
    price: float
    size: float
    side: str  # 'BUY' or 'SELL'
    timestamp: datetime | None = None


# --- Data API ----------------------------------------------------------------


class Position(_Base):
    proxy_wallet: str = Field(alias="proxyWallet")
    condition_id: str = Field(alias="conditionId")
    token_id: str = Field(alias="asset")
    outcome: str
    size: float
    avg_price: float = Field(alias="avgPrice")
    current_value: float = Field(default=0.0, alias="currentValue")
    pnl: float = Field(default=0.0, alias="cashPnl")


class LeaderboardEntry(_Base):
    proxy_wallet: str = Field(alias="proxyWallet")
    username: str | None = None
    rank: int
    pnl: float = 0.0
    volume: float = 0.0


class HolderEntry(_Base):
    proxy_wallet: str = Field(alias="proxyWallet")
    size: float
    outcome: str | None = None


class UserEarnings(_Base):
    proxy_wallet: str = Field(alias="proxyWallet")
    total_earnings: float = Field(default=0.0, alias="totalEarnings")
    payload: dict[str, Any] = Field(default_factory=dict)
