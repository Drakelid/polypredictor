"""Executable M2 exit-criteria audit helpers.

This module turns the remaining M2 checks into measurable evidence:

* active typed-market render coverage for model + band + drivers
* empirical conformal coverage on a held-out resolved set across type/TTR cells
* journal resolved-call count for the manual v0 flow

Like :mod:`api.m1_audit`, it reports evidence but does not self-certify the
exit criteria as passed.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from asyncpg import Pool
from clickhouse_connect.driver.asyncclient import AsyncClient
from model import MarketType, classify, ttr_bucket

from .classifications import ClassificationRow, classifications_batch_asof
from .clickhouse import get_async_client
from .journal import JournalSummary
from .journal import summary as journal_summary
from .markets import _latest_markets, model_for_market
from .postgres import get_async_pool


@dataclass(frozen=True)
class TypedRenderCoverage:
    total_typed: int
    with_model_prob: int
    with_band: int
    with_driver_summaries: int
    with_feature_attributions: int
    fully_rendered: int
    fully_rendered_ratio: float | None
    by_type: dict[str, int]
    by_type_fully_rendered: dict[str, int]


@dataclass(frozen=True)
class ConformalCoverageCell:
    market_type: str
    ttr_bucket: str
    samples: int
    empirical_coverage: float
    target_coverage: float
    within_target_band: bool


@dataclass(frozen=True)
class ConformalCoverageAudit:
    total_resolved_markets: int
    total_scored_samples: int
    skipped_samples: int
    horizons_hours: list[int]
    by_cell: list[ConformalCoverageCell]


@dataclass(frozen=True)
class JournalResolvedAudit:
    total_calls: int
    resolved_calls: int
    unresolved_calls: int
    meets_minimum: bool


@dataclass(frozen=True)
class M2AuditReport:
    as_of: datetime
    active_limit: int
    resolved_limit: int
    typed_render_coverage: TypedRenderCoverage
    conformal_coverage: ConformalCoverageAudit
    journal: JournalResolvedAudit


def _effective_classification(
    snap: dict[str, object],
    classification: ClassificationRow | None,
    *,
    asked_at: datetime,
) -> ClassificationRow:
    if classification is not None:
        return classification
    result = classify(
        question=str(snap["question"]),
        description=None,
        tags=list(snap.get("tags") or []),
        end_date=snap.get("end_date"),  # type: ignore[arg-type]
        multi_outcome_sibling_count=0,
    )
    return ClassificationRow(
        condition_id=str(snap["condition_id"]),
        market_type=result.market_type,
        confidence=result.confidence,
        needs_review=result.needs_review,
        reviewer_flag="pending" if result.needs_review else "",
        classifier="regex_v1",
        features=result.features,
        reasons=result.reasons,
        event_time=asked_at,
        observed_at=asked_at,
    )


async def typed_render_coverage(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    limit: int = 5000,
) -> TypedRenderCoverage:
    snaps = await _latest_markets(ch, asked_at, limit=limit)
    classifications = await classifications_batch_asof(
        ch, [str(snap["condition_id"]) for snap in snaps], asked_at
    )
    total_typed = 0
    with_model_prob = 0
    with_band = 0
    with_driver_summaries = 0
    with_feature_attributions = 0
    fully_rendered = 0
    by_type: Counter[str] = Counter()
    by_type_fully_rendered: Counter[str] = Counter()

    for snap in snaps:
        condition_id = str(snap["condition_id"])
        classification = _effective_classification(
            snap,
            classifications.get(condition_id),
            asked_at=asked_at,
        )
        if classification.market_type is MarketType.MISC:
            continue
        total_typed += 1
        by_type[classification.market_type.value] += 1

        detail = await model_for_market(ch, condition_id=condition_id, asked_at=asked_at)
        if detail is None:
            continue
        has_model_prob = detail.model_prob is not None
        has_band = detail.band_lo is not None and detail.band_hi is not None
        has_driver_summaries = len(detail.driver_summaries) > 0
        has_feature_attributions = len(detail.feature_attributions) > 0
        if has_model_prob:
            with_model_prob += 1
        if has_band:
            with_band += 1
        if has_driver_summaries:
            with_driver_summaries += 1
        if has_feature_attributions:
            with_feature_attributions += 1
        if has_model_prob and has_band and has_driver_summaries and has_feature_attributions:
            fully_rendered += 1
            by_type_fully_rendered[classification.market_type.value] += 1

    return TypedRenderCoverage(
        total_typed=total_typed,
        with_model_prob=with_model_prob,
        with_band=with_band,
        with_driver_summaries=with_driver_summaries,
        with_feature_attributions=with_feature_attributions,
        fully_rendered=fully_rendered,
        fully_rendered_ratio=(fully_rendered / total_typed) if total_typed else None,
        by_type=dict(sorted(by_type.items())),
        by_type_fully_rendered=dict(sorted(by_type_fully_rendered.items())),
    )


async def latest_binary_resolutions(
    ch: AsyncClient,
    *,
    resolved_from: datetime | None = None,
    resolved_to: datetime | None = None,
    limit: int = 500,
) -> list[tuple[str, str, datetime]]:
    conditions: list[str] = ["resolved_outcome IN ('YES', 'NO')"]
    parameters: dict[str, object] = {"limit": limit}
    if resolved_from is not None:
        conditions.append("event_time >= {resolved_from:DateTime64(3)}")
        parameters["resolved_from"] = resolved_from
    if resolved_to is not None:
        conditions.append("event_time <= {resolved_to:DateTime64(3)}")
        parameters["resolved_to"] = resolved_to
    where = " AND ".join(conditions)
    result = await ch.query(
        f"""
        SELECT condition_id, resolved_outcome, event_time
        FROM (
            SELECT condition_id, resolved_outcome, event_time
            FROM market_resolutions
            WHERE {where}
            ORDER BY condition_id, observed_at ASC
            LIMIT 1 BY condition_id
        )
        ORDER BY event_time DESC
        LIMIT {{limit:UInt32}}
        """,
        parameters=parameters,
    )
    return [(str(row[0]), str(row[1]), row[2]) for row in result.result_rows]


async def empirical_conformal_coverage(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    resolved_limit: int = 500,
    resolved_lookback_days: int = 90,
    horizons_hours: tuple[int, ...] = (3, 12, 72, 240),
) -> ConformalCoverageAudit:
    resolutions = await latest_binary_resolutions(
        ch,
        resolved_from=asked_at - timedelta(days=resolved_lookback_days),
        resolved_to=asked_at,
        limit=resolved_limit,
    )
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    target_coverage: dict[tuple[str, str], float] = {}
    scored = 0
    skipped = 0
    for condition_id, resolved_outcome, resolved_at in resolutions:
        outcome = 1 if resolved_outcome == "YES" else 0
        for horizon_hours in horizons_hours:
            sample_asked_at = resolved_at - timedelta(hours=horizon_hours)
            detail = await model_for_market(ch, condition_id=condition_id, asked_at=sample_asked_at)
            if (
                detail is None
                or detail.model_prob is None
                or detail.band_lo is None
                or detail.band_hi is None
                or detail.band_coverage is None
            ):
                skipped += 1
                continue
            cell = (detail.market_type, ttr_bucket(horizon_hours * 3600.0))
            grouped[cell].append(1 if detail.band_lo <= outcome <= detail.band_hi else 0)
            target_coverage[cell] = detail.band_coverage
            scored += 1
    by_cell = [
        ConformalCoverageCell(
            market_type=market_type,
            ttr_bucket=bucket,
            samples=len(results),
            empirical_coverage=(sum(results) / len(results)),
            target_coverage=target_coverage[(market_type, bucket)],
            within_target_band=0.78 <= (sum(results) / len(results)) <= 0.82,
        )
        for (market_type, bucket), results in sorted(grouped.items())
        if results
    ]
    return ConformalCoverageAudit(
        total_resolved_markets=len(resolutions),
        total_scored_samples=scored,
        skipped_samples=skipped,
        horizons_hours=list(horizons_hours),
        by_cell=by_cell,
    )


async def journal_resolved_audit(
    pool: Pool,
    ch: AsyncClient,
    *,
    settings: object,
) -> JournalResolvedAudit:
    report: JournalSummary = await journal_summary(pool=pool, ch=ch, settings=settings)  # type: ignore[arg-type]
    return JournalResolvedAudit(
        total_calls=report.total_calls,
        resolved_calls=report.resolved_calls,
        unresolved_calls=report.unresolved_calls,
        meets_minimum=report.resolved_calls >= 20,
    )


async def run_m2_audit(
    ch: AsyncClient,
    pg: Pool,
    *,
    asked_at: datetime | None = None,
    active_limit: int = 5000,
    resolved_limit: int = 500,
) -> M2AuditReport:
    from .settings import get_settings

    as_of = asked_at or datetime.now(tz=UTC)
    settings = get_settings()
    render = await typed_render_coverage(ch, asked_at=as_of, limit=active_limit)
    conformal = await empirical_conformal_coverage(
        ch,
        asked_at=as_of,
        resolved_limit=resolved_limit,
    )
    journal = await journal_resolved_audit(pg, ch, settings=settings)
    return M2AuditReport(
        as_of=as_of,
        active_limit=active_limit,
        resolved_limit=resolved_limit,
        typed_render_coverage=render,
        conformal_coverage=conformal,
        journal=journal,
    )


def _report_to_json(report: M2AuditReport) -> str:
    payload = asdict(report)
    render = payload.get("typed_render_coverage")
    if isinstance(render, dict):
        ratio = render.get("fully_rendered_ratio")
        if isinstance(ratio, float):
            render["fully_rendered_ratio"] = round(ratio, 6)
    conformal = payload.get("conformal_coverage")
    if isinstance(conformal, dict):
        for cell in conformal.get("by_cell", []):
            for key in ("empirical_coverage", "target_coverage"):
                value = cell.get(key)
                if isinstance(value, float):
                    cell[key] = round(value, 6)
    return json.dumps(payload, indent=2, default=str)


async def _main() -> None:
    ch = await get_async_client()
    pg = await get_async_pool()
    try:
        report = await run_m2_audit(ch, pg)
        print(_report_to_json(report))
    finally:
        await ch.close()
        await pg.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
