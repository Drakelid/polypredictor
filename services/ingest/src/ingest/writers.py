"""Row-shaping helpers for ClickHouse inserts.

Keeping these in one place means the ClickHouse column order is the single
source of truth; workers produce ``tuple`` rows via these helpers rather than
open-coding column orders at each callsite.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from polymarket_client import Market

# -- markets_snapshots --------------------------------------------------------

MARKETS_COLS = (
    "condition_id",
    "event_id",
    "question",
    "description",
    "slug",
    "category",
    "tags",
    "active",
    "closed",
    "archived",
    "volume_usdc",
    "liquidity_usdc",
    "open_interest_usdc",
    "end_date",
    "resolution_source",
    "token_ids",
    "event_time",
    "observed_at",
    "payload_hash",
)


def market_row(m: Market, *, observed_at: datetime, event_id: str = "") -> tuple[Any, ...]:
    """Shape a :class:`Market` into a ``markets_snapshots`` row."""
    event_time = observed_at
    payload_hash = hashlib.sha256(
        json.dumps(
            {
                "cid": m.condition_id,
                "q": m.question,
                "active": m.active,
                "closed": m.closed,
                "vol": m.volume,
                "liq": m.liquidity,
                "oi": m.open_interest,
                "tokens": m.token_ids,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return (
        m.condition_id,
        event_id,
        m.question,
        m.description or "",
        m.slug,
        m.category or "",
        list(m.tags),
        1 if m.active else 0,
        1 if m.closed else 0,
        1 if m.archived else 0,
        float(m.volume),
        float(m.liquidity),
        float(m.open_interest),
        m.end_date,
        m.resolution_source or "",
        list(m.token_ids),
        event_time,
        observed_at,
        payload_hash,
    )


# -- market_quotes ------------------------------------------------------------

QUOTES_COLS = (
    "token_id",
    "condition_id",
    "mid",
    "best_bid",
    "best_ask",
    "bid_size",
    "ask_size",
    "spread",
    "event_time",
    "observed_at",
)


def quote_row(
    *,
    token_id: str,
    condition_id: str,
    mid: float,
    best_bid: float,
    best_ask: float,
    bid_size: float,
    ask_size: float,
    observed_at: datetime,
) -> tuple[Any, ...]:
    spread = max(0.0, best_ask - best_bid)
    return (
        token_id,
        condition_id,
        mid,
        best_bid,
        best_ask,
        bid_size,
        ask_size,
        spread,
        observed_at,
        observed_at,
    )


# -- orderbook_snapshots ------------------------------------------------------

ORDERBOOK_COLS = (
    "token_id",
    "condition_id",
    "side",
    "price",
    "size",
    "event_time",
    "observed_at",
    "source",
    "sequence",
)


def orderbook_rows(
    *,
    token_id: str,
    condition_id: str,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    observed_at: datetime,
    source: str = "rest",
    sequence: int = 0,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for p, s in bids:
        rows.append(
            (token_id, condition_id, "bid", float(p), float(s), observed_at, observed_at, source, sequence)
        )
    for p, s in asks:
        rows.append(
            (token_id, condition_id, "ask", float(p), float(s), observed_at, observed_at, source, sequence)
        )
    return rows


# -- trades_raw ---------------------------------------------------------------

TRADES_COLS = (
    "trade_id",
    "token_id",
    "condition_id",
    "price",
    "size",
    "side",
    "aggressor_side",
    "crossed_spread",
    "event_time",
    "observed_at",
)


def trade_row(
    *,
    trade_id: str,
    token_id: str,
    condition_id: str,
    price: float,
    size: float,
    side: str,
    aggressor_side: str | None,
    crossed_spread: bool | None,
    event_time: datetime,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        trade_id,
        token_id,
        condition_id,
        float(price),
        float(size),
        side.lower(),
        aggressor_side.lower() if aggressor_side else None,
        None if crossed_spread is None else (1 if crossed_spread else 0),
        event_time,
        observed_at,
    )


# -- market_classifications --------------------------------------------------

CLASSIFICATIONS_COLS = (
    "condition_id",
    "market_type",
    "confidence",
    "needs_review",
    "reviewer_flag",
    "classifier",
    "asset",
    "strike",
    "range_low",
    "range_high",
    "direction",
    "event_family",
    "resolution_date",
    "reasons",
    "event_time",
    "observed_at",
)


def classification_row(
    *,
    condition_id: str,
    market_type: str,
    confidence: float,
    needs_review: bool,
    asset: str | None,
    strike: float | None,
    range_low: float | None,
    range_high: float | None,
    direction: str | None,
    event_family: str | None,
    resolution_date: datetime | None,
    reasons: list[str],
    observed_at: datetime,
    classifier: str = "regex_v1",
    reviewer_flag: str = "",
) -> tuple[Any, ...]:
    """Shape a :class:`model.ClassificationResult` into a ``market_classifications`` row.

    Low-confidence / MISC rows get ``reviewer_flag='pending'`` so the review
    queue UI can surface them; approved / rejected flows land in M1.1.
    """
    flag = reviewer_flag or ("pending" if needs_review else "")
    return (
        condition_id,
        market_type,
        float(confidence),
        1 if needs_review else 0,
        flag,
        classifier,
        asset or "",
        strike,
        range_low,
        range_high,
        direction or "",
        event_family or "",
        resolution_date,
        "|".join(reasons),
        observed_at,
        observed_at,
    )


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
