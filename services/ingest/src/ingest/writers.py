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

from polymarket_client import Market, Position, PriceHistoryBucket

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


def market_row(m: Market, *, observed_at: datetime) -> tuple[Any, ...]:
    """Shape a :class:`Market` into a ``markets_snapshots`` row."""
    event_time = observed_at
    event_id = m.event_id or ""
    payload_hash = hashlib.sha256(
        json.dumps(
            {
                "cid": m.condition_id,
                "eid": event_id,
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


# -- prices_history -----------------------------------------------------------

PRICES_HISTORY_COLS = (
    "token_id",
    "condition_id",
    "interval_bucket",
    "bucket_start",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "event_time",
    "observed_at",
)


def price_history_rows(
    *,
    token_id: str,
    condition_id: str,
    interval_bucket: str,
    history: list[PriceHistoryBucket],
    observed_at: datetime,
) -> list[tuple[Any, ...]]:
    """Shape ``/prices-history`` buckets into ``prices_history`` rows.

    The public CLOB endpoint currently exposes bucket timestamps plus a single
    price field. Until Polymarket publishes full OHLCV, we canonicalize that
    price into open/high/low/close and leave volume at 0.0. This keeps the PIT
    history populated for charts and feature bootstrapping without inventing
    data we don't have.
    """
    rows: list[tuple[Any, ...]] = []
    for bucket in history:
        bucket_start = datetime.fromtimestamp(bucket.t, tz=UTC)
        price = float(bucket.p)
        rows.append(
            (
                token_id,
                condition_id,
                interval_bucket,
                bucket_start,
                price,
                price,
                price,
                price,
                0.0,
                bucket_start,
                observed_at,
            )
        )
    return rows


# -- positions_smart_money ----------------------------------------------------

SMART_MONEY_POSITIONS_COLS = (
    "proxy_wallet",
    "condition_id",
    "token_id",
    "outcome",
    "size",
    "avg_entry_price",
    "current_value_usdc",
    "leaderboard_rank",
    "leaderboard_pnl",
    "leaderboard_vol",
    "event_time",
    "observed_at",
)


def smart_money_position_row(
    *,
    position: Position,
    leaderboard_rank: int | None,
    leaderboard_pnl: float | None,
    leaderboard_vol: float | None,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        position.proxy_wallet,
        position.condition_id,
        position.token_id,
        position.outcome.upper(),
        float(position.size),
        float(position.avg_price),
        float(position.current_value),
        leaderboard_rank,
        leaderboard_pnl,
        leaderboard_vol,
        observed_at,
        observed_at,
    )


# -- market_features ----------------------------------------------------------

MARKET_FEATURES_COLS = (
    "condition_id",
    "token_id",
    "mid",
    "spread",
    "book_imbalance_1pct",
    "book_imbalance_5pct",
    "momentum_1h",
    "momentum_24h",
    "momentum_7d",
    "realized_vol_24h",
    "informed_taker_flow_24h",
    "passive_maker_flow_24h",
    "decayed_directional_flow_24h",
    "event_time",
    "observed_at",
)


def market_features_row(
    *,
    condition_id: str,
    token_id: str,
    mid: float,
    spread: float,
    book_imbalance_1pct: float | None,
    book_imbalance_5pct: float | None,
    momentum_1h: float | None,
    momentum_24h: float | None,
    momentum_7d: float | None,
    realized_vol_24h: float | None,
    informed_taker_flow_24h: float | None,
    passive_maker_flow_24h: float | None,
    decayed_directional_flow_24h: float | None,
    observed_at: datetime,
) -> tuple[Any, ...]:
    return (
        condition_id,
        token_id,
        float(mid),
        float(spread),
        book_imbalance_1pct,
        book_imbalance_5pct,
        momentum_1h,
        momentum_24h,
        momentum_7d,
        realized_vol_24h,
        informed_taker_flow_24h,
        passive_maker_flow_24h,
        decayed_directional_flow_24h,
        observed_at,
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
