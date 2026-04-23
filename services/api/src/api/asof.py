"""Point-in-time (as-of) query API.

Cornerstone of PRD §6.6. Every historical query filters by ``observed_at <= T``
so the system cannot read data that did not exist at the asked-about wall-clock
time. Edits and retractions are new rows; this layer picks the latest row per
natural key whose ``observed_at`` is on-or-before ``asked_at``.

Functions here are the **only** sanctioned read path for historical feature
assembly. Anything that goes around this API risks lookahead bias.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class MarketSnapshotRow:
    condition_id: str
    question: str
    active: bool
    closed: bool
    volume_usdc: float
    liquidity_usdc: float
    open_interest_usdc: float
    end_date: datetime | None
    token_ids: list[str]
    event_time: datetime
    observed_at: datetime


@dataclass(frozen=True)
class QuoteRow:
    token_id: str
    condition_id: str
    mid: float
    best_bid: float
    best_ask: float
    spread: float
    event_time: datetime
    observed_at: datetime


async def latest_market_snapshot_asof(
    ch: AsyncClient, condition_id: str, asked_at: datetime
) -> MarketSnapshotRow | None:
    """Return the latest ``markets_snapshots`` row for ``condition_id`` whose
    ``observed_at <= asked_at``. ``None`` if the market didn't exist yet.
    """
    query = """
        SELECT condition_id, question, active, closed, volume_usdc,
               liquidity_usdc, open_interest_usdc, end_date, token_ids,
               event_time, observed_at
        FROM markets_snapshots
        WHERE condition_id = {cond:String}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(query, parameters={"cond": condition_id, "asof": asked_at})
    rows = result.result_rows
    if not rows:
        return None
    r = rows[0]
    return MarketSnapshotRow(
        condition_id=r[0],
        question=r[1],
        active=bool(r[2]),
        closed=bool(r[3]),
        volume_usdc=float(r[4]),
        liquidity_usdc=float(r[5]),
        open_interest_usdc=float(r[6]),
        end_date=r[7],
        token_ids=list(r[8]),
        event_time=r[9],
        observed_at=r[10],
    )


async def latest_quote_asof(
    ch: AsyncClient, token_id: str, asked_at: datetime
) -> QuoteRow | None:
    """Latest quote known at ``asked_at`` for ``token_id``."""
    query = """
        SELECT token_id, condition_id, mid, best_bid, best_ask, spread,
               event_time, observed_at
        FROM market_quotes
        WHERE token_id = {tok:String}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(query, parameters={"tok": token_id, "asof": asked_at})
    rows = result.result_rows
    if not rows:
        return None
    r = rows[0]
    return QuoteRow(
        token_id=r[0],
        condition_id=r[1],
        mid=float(r[2]),
        best_bid=float(r[3]),
        best_ask=float(r[4]),
        spread=float(r[5]),
        event_time=r[6],
        observed_at=r[7],
    )


async def quotes_timeseries_asof(
    ch: AsyncClient,
    token_id: str,
    start: datetime,
    end: datetime,
    asked_at: datetime,
) -> list[QuoteRow]:
    """Return the quote time series for ``token_id`` between ``start`` and
    ``end`` (``event_time``), with all rows filtered to ``observed_at <= asked_at``.

    For each ``event_time`` we keep the latest ``observed_at`` — this yields the
    version of history the model would have seen at ``asked_at``.
    """
    query = """
        SELECT token_id, condition_id, mid, best_bid, best_ask, spread,
               event_time, observed_at
        FROM market_quotes
        WHERE token_id = {tok:String}
          AND event_time BETWEEN {start:DateTime64(3)} AND {end:DateTime64(3)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY event_time, observed_at DESC
        LIMIT 1 BY token_id, event_time
    """
    result = await ch.query(
        query,
        parameters={"tok": token_id, "start": start, "end": end, "asof": asked_at},
    )
    return [
        QuoteRow(
            token_id=r[0],
            condition_id=r[1],
            mid=float(r[2]),
            best_bid=float(r[3]),
            best_ask=float(r[4]),
            spread=float(r[5]),
            event_time=r[6],
            observed_at=r[7],
        )
        for r in result.result_rows
    ]


async def quotes_timeseries_pit(
    ch: AsyncClient,
    token_id: str,
    start: datetime,
    end: datetime,
) -> list[QuoteRow]:
    """Return historical quotes using only rows known at each quote timestamp.

    Unlike :func:`quotes_timeseries_asof`, this is for replaying the model over
    history. Each point keeps the latest row for that ``event_time`` whose
    ``observed_at <= event_time``, so later corrections cannot leak backwards.
    """
    query = """
        SELECT token_id, condition_id, mid, best_bid, best_ask, spread,
               event_time, observed_at
        FROM market_quotes
        WHERE token_id = {tok:String}
          AND event_time BETWEEN {start:DateTime64(3)} AND {end:DateTime64(3)}
          AND observed_at <= event_time
        ORDER BY event_time, observed_at DESC
        LIMIT 1 BY token_id, event_time
    """
    result = await ch.query(
        query,
        parameters={"tok": token_id, "start": start, "end": end},
    )
    return [
        QuoteRow(
            token_id=r[0],
            condition_id=r[1],
            mid=float(r[2]),
            best_bid=float(r[3]),
            best_ask=float(r[4]),
            spread=float(r[5]),
            event_time=r[6],
            observed_at=r[7],
        )
        for r in result.result_rows
    ]


async def resolution_asof(
    ch: AsyncClient, condition_id: str, asked_at: datetime
) -> tuple[str, datetime] | None:
    """Resolution known at ``asked_at``.

    UMA re-resolutions are stored as NEW rows; we return whatever the caller
    would have seen at the time. If the market had not yet resolved at
    ``asked_at``, returns ``None``.
    """
    query = """
        SELECT resolved_outcome, event_time
        FROM market_resolutions
        WHERE condition_id = {cond:String}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(query, parameters={"cond": condition_id, "asof": asked_at})
    rows = result.result_rows
    if not rows:
        return None
    return str(rows[0][0]), rows[0][1]
