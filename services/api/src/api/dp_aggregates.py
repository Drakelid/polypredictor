"""Differentially private aggregate labels for opted-in users (M7.4)."""

from __future__ import annotations

import asyncio
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from asyncpg import Pool

from .postgres import get_async_pool
from .settings import Settings, get_settings


@dataclass(frozen=True)
class ResolvedLabelCall:
    user_id: str
    outcome: str
    model_prob_at_call: float
    resolved_outcome: str
    created_at: datetime


@dataclass(frozen=True)
class DpAggregateRow:
    cohort_kind: str
    bucket_label: str
    source_user_count: int
    raw_call_count: int
    noisy_call_count: float
    raw_hit_rate: float
    noisy_hit_rate: float
    epsilon: float
    min_users: int
    min_calls: int
    lookback_start: datetime
    lookback_end: datetime
    observed_at: datetime


async def load_opted_in_resolved_calls(
    *,
    pool: Pool,
    lookback_start: datetime,
    lookback_end: datetime,
) -> list[ResolvedLabelCall]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT jc.user_id, jc.outcome, jc.model_prob_at_call, jc.resolved_outcome, jc.created_at
            FROM journal_calls jc
            JOIN user_privacy_preferences upp
              ON upp.user_id = jc.user_id
            WHERE upp.cross_user_learning_opt_in = TRUE
              AND jc.resolved_outcome IN ('YES', 'NO')
              AND jc.created_at >= $1
              AND jc.created_at <= $2
            ORDER BY jc.created_at DESC
            """,
            lookback_start,
            lookback_end,
        )
    return [
        ResolvedLabelCall(
            user_id=str(row["user_id"]),
            outcome=str(row["outcome"]),
            model_prob_at_call=float(row["model_prob_at_call"]),
            resolved_outcome=str(row["resolved_outcome"]),
            created_at=_coerce_utc(row["created_at"]),
        )
        for row in rows
    ]


def build_dp_aggregate_rows(
    calls: list[ResolvedLabelCall],
    *,
    epsilon: float,
    min_users: int,
    min_calls: int,
    lookback_start: datetime,
    lookback_end: datetime,
    observed_at: datetime,
    rng: random.Random | None = None,
) -> list[DpAggregateRow]:
    if epsilon <= 0:
        raise ValueError("epsilon must be positive")
    buckets: dict[str, list[ResolvedLabelCall]] = {}
    for call in calls:
        label = _confidence_bucket_label(_call_confidence(call))
        buckets.setdefault(label, []).append(call)
    noise_rng = rng or random.Random()
    out: list[DpAggregateRow] = []
    for label, group in sorted(buckets.items()):
        source_user_count = len({call.user_id for call in group})
        raw_call_count = len(group)
        if source_user_count < min_users or raw_call_count < min_calls:
            continue
        raw_hits = sum(1 for call in group if _call_hit(call))
        noisy_call_count = max(0.0, raw_call_count + _laplace_noise(noise_rng, 1.0 / epsilon))
        noisy_hits = min(
            noisy_call_count,
            max(0.0, raw_hits + _laplace_noise(noise_rng, 1.0 / epsilon)),
        )
        raw_hit_rate = raw_hits / raw_call_count if raw_call_count else 0.0
        noisy_hit_rate = noisy_hits / noisy_call_count if noisy_call_count > 0 else 0.0
        out.append(
            DpAggregateRow(
                cohort_kind="confidence_bucket",
                bucket_label=label,
                source_user_count=source_user_count,
                raw_call_count=raw_call_count,
                noisy_call_count=noisy_call_count,
                raw_hit_rate=raw_hit_rate,
                noisy_hit_rate=noisy_hit_rate,
                epsilon=epsilon,
                min_users=min_users,
                min_calls=min_calls,
                lookback_start=lookback_start,
                lookback_end=lookback_end,
                observed_at=observed_at,
            )
        )
    return out


async def write_dp_aggregate_rows(
    *,
    pool: Pool,
    rows: list[DpAggregateRow],
) -> int:
    if not rows:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO dp_label_aggregates (
                cohort_kind,
                bucket_label,
                source_user_count,
                raw_call_count,
                noisy_call_count,
                raw_hit_rate,
                noisy_hit_rate,
                epsilon,
                min_users,
                min_calls,
                lookback_start,
                lookback_end,
                observed_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            [
                (
                    row.cohort_kind,
                    row.bucket_label,
                    row.source_user_count,
                    row.raw_call_count,
                    row.noisy_call_count,
                    row.raw_hit_rate,
                    row.noisy_hit_rate,
                    row.epsilon,
                    row.min_users,
                    row.min_calls,
                    row.lookback_start,
                    row.lookback_end,
                    row.observed_at,
                )
                for row in rows
            ],
        )
    return len(rows)


async def latest_dp_aggregates(
    *,
    pool: Pool,
) -> list[DpAggregateRow]:
    async with pool.acquire() as conn:
        observed_at = await conn.fetchval(
            "SELECT MAX(observed_at) FROM dp_label_aggregates"
        )
        if observed_at is None:
            return []
        rows = await conn.fetch(
            """
            SELECT cohort_kind, bucket_label, source_user_count, raw_call_count,
                   noisy_call_count, raw_hit_rate, noisy_hit_rate, epsilon,
                   min_users, min_calls, lookback_start, lookback_end, observed_at
            FROM dp_label_aggregates
            WHERE observed_at = $1
            ORDER BY cohort_kind, bucket_label
            """,
            observed_at,
        )
    return [
        DpAggregateRow(
            cohort_kind=str(row["cohort_kind"]),
            bucket_label=str(row["bucket_label"]),
            source_user_count=int(row["source_user_count"]),
            raw_call_count=int(row["raw_call_count"]),
            noisy_call_count=float(row["noisy_call_count"]),
            raw_hit_rate=float(row["raw_hit_rate"]),
            noisy_hit_rate=float(row["noisy_hit_rate"]),
            epsilon=float(row["epsilon"]),
            min_users=int(row["min_users"]),
            min_calls=int(row["min_calls"]),
            lookback_start=_coerce_utc(row["lookback_start"]),
            lookback_end=_coerce_utc(row["lookback_end"]),
            observed_at=_coerce_utc(row["observed_at"]),
        )
        for row in rows
    ]


async def run_dp_aggregation(
    *,
    pool: Pool,
    settings: Settings,
    observed_at: datetime | None = None,
    rng: random.Random | None = None,
) -> int:
    as_of = observed_at or datetime.now(tz=UTC)
    lookback_start = as_of - timedelta(days=settings.dp_aggregate_lookback_days)
    calls = await load_opted_in_resolved_calls(
        pool=pool,
        lookback_start=lookback_start,
        lookback_end=as_of,
    )
    rows = build_dp_aggregate_rows(
        calls,
        epsilon=settings.dp_aggregate_epsilon,
        min_users=settings.dp_aggregate_min_users,
        min_calls=settings.dp_aggregate_min_calls,
        lookback_start=lookback_start,
        lookback_end=as_of,
        observed_at=as_of,
        rng=rng,
    )
    return await write_dp_aggregate_rows(pool=pool, rows=rows)


def _call_confidence(call: ResolvedLabelCall) -> float:
    return (
        call.model_prob_at_call
        if call.outcome == "YES"
        else 1.0 - call.model_prob_at_call
    )


def _call_hit(call: ResolvedLabelCall) -> bool:
    return (
        (call.outcome == "YES" and call.resolved_outcome == "YES")
        or (call.outcome == "NO" and call.resolved_outcome == "NO")
    )


def _confidence_bucket_label(confidence: float) -> str:
    lo = int(confidence * 10) / 10
    hi = lo + 0.1
    return f"{int(lo * 100)}-{int(min(hi, 1.0) * 100)}%"


def _laplace_noise(rng: random.Random, scale: float) -> float:
    u = rng.random() - 0.5
    return -scale * math.copysign(math.log(1 - 2 * abs(u)), u)


def _coerce_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _rows_to_json(rows: list[DpAggregateRow]) -> str:
    return json.dumps({"rows": [asdict(row) for row in rows]}, default=str, indent=2)


async def _main() -> None:
    settings = get_settings()
    pool = await get_async_pool()
    try:
        count = await run_dp_aggregation(pool=pool, settings=settings)
        latest = await latest_dp_aggregates(pool=pool)
        print(json.dumps({"rows_written": count}, indent=2))
        if latest:
            print(_rows_to_json(latest))
    finally:
        await pool.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
