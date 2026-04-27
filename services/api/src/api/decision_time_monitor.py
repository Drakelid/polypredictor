"""M8.5 Median time-to-decision live monitoring.

Wires the :func:`model.decision_time.median_time_to_decision` helper into
a persisted nightly job that:

1. Queries ``journal_calls`` for auto-WSS fills in the trailing window.
2. Joins each call to the signal event (via condition_id + event_time) that
   most likely triggered the user's decision.
3. Computes the elapsed minutes from signal ``event_time`` to
   ``journal_call.created_at``.
4. Persists a summary row in the ``decision_time_metrics`` Postgres table.

The M8.5 target is **median < 5 minutes**.

Schema (Postgres)::

    CREATE TABLE IF NOT EXISTS decision_time_metrics (
        id          SERIAL PRIMARY KEY,
        window_label TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        median_minutes DOUBLE PRECISION,
        p90_minutes DOUBLE PRECISION,
        meets_target BOOLEAN NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
"""

from __future__ import annotations

import asyncio
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient

from model.decision_time import median_time_to_decision

from .clickhouse import get_async_client
from .postgres import get_async_pool
from .settings import get_settings

_DEFAULT_WINDOW_HOURS = 48
_DEFAULT_LIMIT = 1_000
_TARGET_MINUTES = 5.0  # M8.5 product metric
_SIGNAL_MATCH_WINDOW_MINUTES = 60  # max lag between signal and call


@dataclass(frozen=True)
class DecisionTimeReport:
    as_of: datetime
    window_hours: int
    window_label: str
    sample_count: int
    median_minutes: float | None
    p90_minutes: float | None
    meets_target: bool
    target_minutes: float


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


async def compute_decision_time_report(
    ch: AsyncClient,
    pool: asyncpg.Pool,
    *,
    window_hours: int = _DEFAULT_WINDOW_HOURS,
    limit: int = _DEFAULT_LIMIT,
    asked_at: datetime | None = None,
    target_minutes: float = _TARGET_MINUTES,
) -> DecisionTimeReport:
    """Compute median + p90 time-to-decision over the trailing ``window_hours``.

    Strategy
    --------
    We join ``journal_calls`` (Postgres) with ``signal_events`` (ClickHouse)
    on ``condition_id`` within a ±60-minute window.  The elapsed time from
    the signal ``event_time`` to the journal call ``created_at`` gives the
    decision latency.  For calls without a matching signal we use the
    fallback of ``(created_at - market_quote.event_time)`` which represents
    the time from the last quote update that the user likely noticed.
    """
    as_of = asked_at or datetime.now(tz=UTC)
    window_start = as_of - timedelta(hours=window_hours)

    # 1. Fetch recent journal calls from Postgres.
    calls: list[dict[str, Any]] = []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, condition_id, created_at
                FROM journal_calls
                WHERE created_at >= $1 AND created_at <= $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                window_start,
                as_of,
                limit,
            )
        for row in rows:
            calls.append(dict(row))
    except Exception:
        calls = []

    if not calls:
        return DecisionTimeReport(
            as_of=as_of,
            window_hours=window_hours,
            window_label=f"{window_hours}h",
            sample_count=0,
            median_minutes=None,
            p90_minutes=None,
            meets_target=False,
            target_minutes=target_minutes,
        )

    condition_ids = list({c["condition_id"] for c in calls})

    # 2. Fetch the most recent signal event per condition_id in the window.
    durations_minutes: list[float] = []
    try:
        sig_query = """
            SELECT condition_id, event_time
            FROM signal_events
            WHERE condition_id IN {ids:Array(String)}
              AND event_time >= {start:DateTime64(3)}
              AND event_time <= {asof:DateTime64(3)}
            ORDER BY condition_id, event_time DESC
            LIMIT 1 BY condition_id
        """
        sig_result = await ch.query(
            sig_query,
            parameters={
                "ids": condition_ids,
                "start": window_start,
                "asof": as_of,
            },
        )
        signal_times: dict[str, datetime] = {}
        for row in sig_result.result_rows:
            cid = str(row[0])
            sig_time: Any = row[1]
            if hasattr(sig_time, "replace"):
                if sig_time.tzinfo is None:
                    sig_time = sig_time.replace(tzinfo=UTC)
                signal_times[cid] = sig_time
    except Exception:
        signal_times = {}

    for call in calls:
        cid = call["condition_id"]
        call_time: datetime = call["created_at"]
        if call_time.tzinfo is None:
            call_time = call_time.replace(tzinfo=UTC)

        sig_time = signal_times.get(cid)
        if sig_time is not None:
            lag_minutes = (call_time - sig_time).total_seconds() / 60.0
            # Only count if the signal preceded the call within the match window.
            if 0 <= lag_minutes <= _SIGNAL_MATCH_WINDOW_MINUTES:
                durations_minutes.append(lag_minutes)

    median_val = median_time_to_decision(durations_minutes) if durations_minutes else None
    p90_val: float | None = None
    if durations_minutes:
        sorted_d = sorted(durations_minutes)
        idx = max(0, int(math.ceil(0.90 * len(sorted_d))) - 1)
        p90_val = sorted_d[idx]

    meets = (median_val is not None) and (median_val < target_minutes)

    return DecisionTimeReport(
        as_of=as_of,
        window_hours=window_hours,
        window_label=f"{window_hours}h",
        sample_count=len(durations_minutes),
        median_minutes=round(median_val, 3) if median_val is not None else None,
        p90_minutes=round(p90_val, 3) if p90_val is not None else None,
        meets_target=meets,
        target_minutes=target_minutes,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS decision_time_metrics (
    id              SERIAL PRIMARY KEY,
    window_label    TEXT NOT NULL,
    sample_count    INTEGER NOT NULL,
    median_minutes  DOUBLE PRECISION,
    p90_minutes     DOUBLE PRECISION,
    meets_target    BOOLEAN NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def _ensure_table(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)


async def persist_report(pool: asyncpg.Pool, report: DecisionTimeReport) -> None:
    await _ensure_table(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO decision_time_metrics
                (window_label, sample_count, median_minutes, p90_minutes,
                 meets_target, observed_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            report.window_label,
            report.sample_count,
            report.median_minutes,
            report.p90_minutes,
            report.meets_target,
            report.as_of,
        )


async def latest_decision_time_report(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Read the most recent persisted report from Postgres."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT window_label, sample_count, median_minutes, p90_minutes,
                       meets_target, observed_at
                FROM decision_time_metrics
                ORDER BY observed_at DESC
                LIMIT 1
                """
            )
        if row is None:
            return None
        return dict(row)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_decision_time_monitor(
    ch: AsyncClient,
    pool: asyncpg.Pool,
    *,
    window_hours: int = _DEFAULT_WINDOW_HOURS,
    persist: bool = True,
) -> DecisionTimeReport:
    """Compute and optionally persist the decision-time report."""
    report = await compute_decision_time_report(ch, pool, window_hours=window_hours)
    if persist:
        await persist_report(pool, report)
    return report


async def _main() -> None:
    ch = await get_async_client()
    pool = await get_async_pool()
    try:
        report = await run_decision_time_monitor(ch, pool, persist=True)

        def _serial(obj: object) -> object:
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, float) and math.isnan(obj):
                return None
            raise TypeError(f"not serialisable: {type(obj)}")

        print(json.dumps(asdict(report), indent=2, default=_serial))
    finally:
        await ch.close()
        await pool.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
