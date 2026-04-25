"""Executable M7 exit-criteria audit helpers.

This module turns the remaining M7 checks into measurable evidence:

* auto-WSS journal round-trip coverage from fill -> auto-log -> resolution score
* tuned walk-forward replay parity against the live detail path at the same PIT timestamps

Like the earlier milestone audits, it reports evidence but does not self-certify
the exit criteria as passed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from asyncpg import Pool
from clickhouse_connect.driver.asyncclient import AsyncClient

from .backtest_walk_forward import replay_walk_forward_samples
from .clickhouse import get_async_client
from .markets import model_for_market
from .postgres import get_async_pool
from .settings import get_settings
from .tuning import get_active_profile


@dataclass(frozen=True)
class JournalRoundTripAudit:
    total_auto_wss_calls: int
    resolved_auto_wss_calls: int
    unresolved_auto_wss_calls: int
    scored_auto_wss_calls: int
    missing_model_snapshot_calls: int
    missing_resolution_score_calls: int
    meets_round_trip: bool


@dataclass(frozen=True)
class TuningReproducibilityAudit:
    profile_name: str
    profile_preset: str
    total_replay_samples: int
    compared_samples: int
    mismatched_samples: int
    max_abs_probability_delta: float
    max_abs_band_delta: float
    tolerance: float
    within_tolerance: bool


@dataclass(frozen=True)
class M7AuditReport:
    as_of: datetime
    lookback_days: int
    horizon_hours: int
    resolved_limit: int
    journal_round_trip: JournalRoundTripAudit
    tuning_reproducibility: TuningReproducibilityAudit


async def journal_round_trip_audit(
    pool: Pool,
) -> JournalRoundTripAudit:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                count(*) FILTER (WHERE source = 'auto_wss') AS total_auto_wss_calls,
                count(*) FILTER (
                    WHERE source = 'auto_wss'
                      AND resolved_outcome IN ('YES', 'NO')
                ) AS resolved_auto_wss_calls,
                count(*) FILTER (
                    WHERE source = 'auto_wss'
                      AND resolved_outcome IS NULL
                ) AS unresolved_auto_wss_calls,
                count(*) FILTER (
                    WHERE source = 'auto_wss'
                      AND resolved_outcome IN ('YES', 'NO')
                      AND pnl_usdc IS NOT NULL
                      AND brier_contribution IS NOT NULL
                ) AS scored_auto_wss_calls,
                count(*) FILTER (
                    WHERE source = 'auto_wss'
                      AND (
                        model_prob_at_call IS NULL
                        OR model_band_lo_at_call IS NULL
                        OR model_band_hi_at_call IS NULL
                        OR market_mid_at_call IS NULL
                      )
                ) AS missing_model_snapshot_calls,
                count(*) FILTER (
                    WHERE source = 'auto_wss'
                      AND resolved_outcome IN ('YES', 'NO')
                      AND (
                        pnl_usdc IS NULL
                        OR brier_contribution IS NULL
                      )
                ) AS missing_resolution_score_calls
            FROM journal_calls
            """
        )
    assert row is not None
    total_auto_wss_calls = int(row["total_auto_wss_calls"] or 0)
    resolved_auto_wss_calls = int(row["resolved_auto_wss_calls"] or 0)
    unresolved_auto_wss_calls = int(row["unresolved_auto_wss_calls"] or 0)
    scored_auto_wss_calls = int(row["scored_auto_wss_calls"] or 0)
    missing_model_snapshot_calls = int(row["missing_model_snapshot_calls"] or 0)
    missing_resolution_score_calls = int(row["missing_resolution_score_calls"] or 0)
    return JournalRoundTripAudit(
        total_auto_wss_calls=total_auto_wss_calls,
        resolved_auto_wss_calls=resolved_auto_wss_calls,
        unresolved_auto_wss_calls=unresolved_auto_wss_calls,
        scored_auto_wss_calls=scored_auto_wss_calls,
        missing_model_snapshot_calls=missing_model_snapshot_calls,
        missing_resolution_score_calls=missing_resolution_score_calls,
        meets_round_trip=(
            resolved_auto_wss_calls > 0
            and resolved_auto_wss_calls == scored_auto_wss_calls
            and missing_model_snapshot_calls == 0
            and missing_resolution_score_calls == 0
        ),
    )


async def tuning_reproducibility_audit(
    ch: AsyncClient,
    pool: Pool,
    *,
    asked_at: datetime,
    lookback_days: int = 90,
    horizon_hours: int = 24,
    resolved_limit: int = 500,
    tolerance: float = 1e-9,
) -> TuningReproducibilityAudit:
    settings = get_settings()
    profile = await get_active_profile(pool=pool, settings=settings)
    replay_rows, _, _ = await replay_walk_forward_samples(
        ch,
        horizons_hours=[horizon_hours],
        resolved_from=asked_at - timedelta(days=lookback_days),
        resolved_to=asked_at,
        limit=resolved_limit,
        tuning_profile=profile,
    )
    compared = 0
    mismatched = 0
    max_abs_prob_delta = 0.0
    max_abs_band_delta = 0.0

    for row in replay_rows:
        detail = await model_for_market(
            ch,
            condition_id=row.condition_id,
            asked_at=row.asked_at,
            tuning_profile=profile,
        )
        if detail is None or detail.model_prob is None:
            mismatched += 1
            continue
        compared += 1
        prob_delta = abs(float(detail.model_prob) - float(row.predicted_prob))
        band_delta = max(
            abs((detail.band_lo or 0.0) - (row.band_lo or 0.0)),
            abs((detail.band_hi or 0.0) - (row.band_hi or 0.0)),
        )
        max_abs_prob_delta = max(max_abs_prob_delta, prob_delta)
        max_abs_band_delta = max(max_abs_band_delta, band_delta)
        if prob_delta > tolerance or band_delta > tolerance:
            mismatched += 1

    return TuningReproducibilityAudit(
        profile_name=profile.name,
        profile_preset=profile.preset,
        total_replay_samples=len(replay_rows),
        compared_samples=compared,
        mismatched_samples=mismatched,
        max_abs_probability_delta=max_abs_prob_delta,
        max_abs_band_delta=max_abs_band_delta,
        tolerance=tolerance,
        within_tolerance=(compared > 0 and mismatched == 0),
    )


async def run_m7_audit(
    ch: AsyncClient,
    pg: Pool,
    *,
    asked_at: datetime | None = None,
    lookback_days: int = 90,
    horizon_hours: int = 24,
    resolved_limit: int = 500,
) -> M7AuditReport:
    as_of = asked_at or datetime.now(tz=UTC)
    journal = await journal_round_trip_audit(pg)
    tuning = await tuning_reproducibility_audit(
        ch,
        pg,
        asked_at=as_of,
        lookback_days=lookback_days,
        horizon_hours=horizon_hours,
        resolved_limit=resolved_limit,
    )
    return M7AuditReport(
        as_of=as_of,
        lookback_days=lookback_days,
        horizon_hours=horizon_hours,
        resolved_limit=resolved_limit,
        journal_round_trip=journal,
        tuning_reproducibility=tuning,
    )


def _report_to_json(report: M7AuditReport) -> str:
    payload = asdict(report)
    tuning = payload.get("tuning_reproducibility")
    if isinstance(tuning, dict):
        for key in ("max_abs_probability_delta", "max_abs_band_delta", "tolerance"):
            value = tuning.get(key)
            if isinstance(value, float):
                tuning[key] = round(value, 12)
    return json.dumps(payload, indent=2, default=str)


async def _main() -> None:
    ch = await get_async_client()
    pg = await get_async_pool()
    try:
        report = await run_m7_audit(ch, pg)
        print(_report_to_json(report))
    finally:
        await ch.close()
        await pg.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
