"""Executable M8 exit-criteria audit helpers.

This module turns two of the M8.5 checks into measurable evidence by
reading the persisted nightly drift snapshot:

* per-cell 30d rolling Brier skill >= ``settings.m8_brier_skill_threshold``
  (``0.05`` per the PRD §8 product metric).
* per-cell empirical conformal coverage inside the
  ``[0.78, 0.82]`` Mondrian target band.

The remaining M8.5 criteria — median time-to-decision and false-positive
alert rate — depend on product-side telemetry that is not persisted today
and are intentionally left out of this audit. Like the M1/M2/M3/M4/M7
audits, this module produces evidence but does not self-certify the exit
criteria as passed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from clickhouse_connect.driver.asyncclient import AsyncClient

from .clickhouse import get_async_client
from .drift_report import DriftSnapshot, ModelDriftMetric, latest_drift_snapshot_asof

DEFAULT_BRIER_SKILL_THRESHOLD: float = 0.05
DEFAULT_COVERAGE_LO: float = 0.78
DEFAULT_COVERAGE_HI: float = 0.82
DEFAULT_WINDOW_LABEL: str = "30d"


@dataclass(frozen=True)
class CellEvaluation:
    market_type: str
    ttr_bucket: str
    regime: str | None
    sample_count: int
    brier_skill: float | None
    coverage: float | None
    meets_skill_threshold: bool
    coverage_in_target_band: bool


@dataclass(frozen=True)
class BrierSkillAudit:
    window_label: str
    threshold: float
    cells_total: int
    cells_with_skill: int
    cells_meeting_threshold: int
    cells_below_threshold: int
    meets_in_every_cell: bool
    by_market_type_meeting: dict[str, int]
    by_market_type_total: dict[str, int]


@dataclass(frozen=True)
class ConformalCoverageAudit:
    window_label: str
    target_lo: float
    target_hi: float
    cells_total: int
    cells_with_coverage: int
    cells_in_target_band: int
    cells_outside_target_band: int
    meets_in_every_cell: bool
    by_market_type_in_band: dict[str, int]
    by_market_type_total: dict[str, int]


@dataclass(frozen=True)
class M8AuditReport:
    as_of: datetime
    snapshot_observed_at: datetime | None
    window_label: str
    brier_skill_threshold: float
    coverage_target_lo: float
    coverage_target_hi: float
    cells: list[CellEvaluation]
    brier_skill: BrierSkillAudit
    conformal_coverage: ConformalCoverageAudit


def _is_overall_aggregate(metric: ModelDriftMetric) -> bool:
    return (
        metric.market_type.lower() == "overall"
        or metric.ttr_bucket.lower() == "overall"
    )


def evaluate_cells(
    metrics: list[ModelDriftMetric],
    *,
    window_label: str = DEFAULT_WINDOW_LABEL,
    brier_skill_threshold: float = DEFAULT_BRIER_SKILL_THRESHOLD,
    coverage_lo: float = DEFAULT_COVERAGE_LO,
    coverage_hi: float = DEFAULT_COVERAGE_HI,
) -> list[CellEvaluation]:
    """Filter ``metrics`` to the per-cell stratum and evaluate each cell.

    The drift driver writes both an ``overall`` aggregate row and per-cell
    strata (one row per ``(market_type, ttr_bucket, regime)``). M8.5 is
    explicit that the criterion must hold *in every market-type cell, not
    just aggregate*, so the aggregate rows are excluded here.
    """
    cells: list[CellEvaluation] = []
    for metric in metrics:
        if metric.window_label != window_label:
            continue
        if _is_overall_aggregate(metric):
            continue
        meets_skill = (
            metric.brier_skill is not None
            and metric.brier_skill >= brier_skill_threshold
        )
        coverage_in_band = (
            metric.coverage is not None
            and coverage_lo <= metric.coverage <= coverage_hi
        )
        cells.append(
            CellEvaluation(
                market_type=metric.market_type,
                ttr_bucket=metric.ttr_bucket,
                regime=metric.regime,
                sample_count=metric.sample_count,
                brier_skill=metric.brier_skill,
                coverage=metric.coverage,
                meets_skill_threshold=meets_skill,
                coverage_in_target_band=coverage_in_band,
            )
        )
    return cells


def summarize_brier_skill(
    cells: list[CellEvaluation],
    *,
    window_label: str,
    threshold: float,
) -> BrierSkillAudit:
    cells_total = len(cells)
    by_total: dict[str, int] = {}
    by_meeting: dict[str, int] = {}
    cells_with_skill = 0
    meeting = 0
    for cell in cells:
        by_total[cell.market_type] = by_total.get(cell.market_type, 0) + 1
        if cell.brier_skill is not None:
            cells_with_skill += 1
        if cell.meets_skill_threshold:
            meeting += 1
            by_meeting[cell.market_type] = by_meeting.get(cell.market_type, 0) + 1
    cells_below = cells_total - meeting
    meets_in_every_cell = cells_total > 0 and cells_below == 0
    return BrierSkillAudit(
        window_label=window_label,
        threshold=threshold,
        cells_total=cells_total,
        cells_with_skill=cells_with_skill,
        cells_meeting_threshold=meeting,
        cells_below_threshold=cells_below,
        meets_in_every_cell=meets_in_every_cell,
        by_market_type_meeting=dict(sorted(by_meeting.items())),
        by_market_type_total=dict(sorted(by_total.items())),
    )


def summarize_conformal_coverage(
    cells: list[CellEvaluation],
    *,
    window_label: str,
    target_lo: float,
    target_hi: float,
) -> ConformalCoverageAudit:
    cells_total = len(cells)
    by_total: dict[str, int] = {}
    by_in_band: dict[str, int] = {}
    cells_with_coverage = 0
    in_band = 0
    for cell in cells:
        by_total[cell.market_type] = by_total.get(cell.market_type, 0) + 1
        if cell.coverage is not None:
            cells_with_coverage += 1
        if cell.coverage_in_target_band:
            in_band += 1
            by_in_band[cell.market_type] = by_in_band.get(cell.market_type, 0) + 1
    cells_outside = cells_total - in_band
    meets_in_every_cell = cells_total > 0 and cells_outside == 0
    return ConformalCoverageAudit(
        window_label=window_label,
        target_lo=target_lo,
        target_hi=target_hi,
        cells_total=cells_total,
        cells_with_coverage=cells_with_coverage,
        cells_in_target_band=in_band,
        cells_outside_target_band=cells_outside,
        meets_in_every_cell=meets_in_every_cell,
        by_market_type_in_band=dict(sorted(by_in_band.items())),
        by_market_type_total=dict(sorted(by_total.items())),
    )


def build_m8_audit_report(
    snapshot: DriftSnapshot | None,
    *,
    as_of: datetime,
    window_label: str = DEFAULT_WINDOW_LABEL,
    brier_skill_threshold: float = DEFAULT_BRIER_SKILL_THRESHOLD,
    coverage_lo: float = DEFAULT_COVERAGE_LO,
    coverage_hi: float = DEFAULT_COVERAGE_HI,
) -> M8AuditReport:
    metrics = list(snapshot.model_metrics) if snapshot is not None else []
    cells = evaluate_cells(
        metrics,
        window_label=window_label,
        brier_skill_threshold=brier_skill_threshold,
        coverage_lo=coverage_lo,
        coverage_hi=coverage_hi,
    )
    skill = summarize_brier_skill(
        cells,
        window_label=window_label,
        threshold=brier_skill_threshold,
    )
    coverage = summarize_conformal_coverage(
        cells,
        window_label=window_label,
        target_lo=coverage_lo,
        target_hi=coverage_hi,
    )
    return M8AuditReport(
        as_of=as_of,
        snapshot_observed_at=snapshot.observed_at if snapshot is not None else None,
        window_label=window_label,
        brier_skill_threshold=brier_skill_threshold,
        coverage_target_lo=coverage_lo,
        coverage_target_hi=coverage_hi,
        cells=cells,
        brier_skill=skill,
        conformal_coverage=coverage,
    )


async def run_m8_audit(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
    window_label: str = DEFAULT_WINDOW_LABEL,
    brier_skill_threshold: float = DEFAULT_BRIER_SKILL_THRESHOLD,
    coverage_lo: float = DEFAULT_COVERAGE_LO,
    coverage_hi: float = DEFAULT_COVERAGE_HI,
) -> M8AuditReport:
    as_of = asked_at or datetime.now(tz=UTC)
    snapshot = await latest_drift_snapshot_asof(ch, asked_at=as_of)
    return build_m8_audit_report(
        snapshot,
        as_of=as_of,
        window_label=window_label,
        brier_skill_threshold=brier_skill_threshold,
        coverage_lo=coverage_lo,
        coverage_hi=coverage_hi,
    )


def _report_to_json(report: M8AuditReport) -> str:
    payload = asdict(report)
    for cell in payload.get("cells", []):
        for key in ("brier_skill", "coverage"):
            value = cell.get(key)
            if isinstance(value, float):
                cell[key] = round(value, 6)
    return json.dumps(payload, indent=2, default=str)


async def _main() -> None:
    ch = await get_async_client()
    try:
        report = await run_m8_audit(ch)
        print(_report_to_json(report))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
