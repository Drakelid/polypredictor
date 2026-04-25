"""Resolution-risk training corpus builder (M4.4).

This turns the raw append-only resolution history into a persisted labeled
corpus for the future UMA/dispute-aware resolution-risk model. The builder:

1. Loads each market's full resolution path from ``market_resolutions``.
2. Reconstructs the market text known at the first resolution observation.
3. Derives labels for explicit disputes and "unexpected" resolution paths
   (invalid, revised, manual, or disputed).
4. Stores the resulting corpus rows in ``resolution_risk_corpus``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient
from ingest.writers import (
    RESOLUTION_RISK_CORPUS_COLS,
    resolution_risk_corpus_row,
)

from . import asof as asof_q
from .clickhouse import get_async_client
from .resolution_risk import resolution_risk_asof
from .settings import get_settings


@dataclass(frozen=True)
class ResolutionHistoryRow:
    condition_id: str
    resolved_outcome: str
    resolution_source: str
    is_revision: bool
    prior_observed_at: datetime | None
    dispute_status: str | None
    event_time: datetime
    observed_at: datetime


@dataclass(frozen=True)
class ResolutionPathSummary:
    condition_id: str
    first_resolved_outcome: str
    final_resolved_outcome: str
    final_resolution_source: str
    first_resolution_event_time: datetime
    first_resolution_observed_at: datetime
    resolution_count: int
    revision_count: int
    invalid_count: int
    manual_count: int
    uma_count: int
    had_dispute: bool
    unexpected_resolution: bool
    dispute_statuses: list[str]
    unexpected_reasons: list[str]


@dataclass(frozen=True)
class ResolutionRiskCorpusReport:
    asked_at: datetime
    lookback_days: int
    limit: int
    rows_written: int
    disputed_markets: int
    unexpected_markets: int


def summarize_resolution_path(rows: list[ResolutionHistoryRow]) -> ResolutionPathSummary:
    if not rows:
        raise ValueError("resolution path cannot be empty")
    ordered = sorted(rows, key=lambda row: row.observed_at)
    first_row = ordered[0]
    final_row = ordered[-1]
    dispute_statuses = sorted(
        {
            str(row.dispute_status).strip()
            for row in ordered
            if str(row.dispute_status or "").strip()
        }
    )
    revision_count = sum(
        1
        for row in ordered
        if row.is_revision
        or row.prior_observed_at is not None
        or row.resolved_outcome == "REVISED"
    )
    invalid_count = sum(1 for row in ordered if row.resolved_outcome == "INVALID")
    manual_count = sum(1 for row in ordered if row.resolution_source == "manual")
    uma_count = sum(1 for row in ordered if row.resolution_source == "uma")

    unexpected_reasons: list[str] = []
    had_dispute = bool(dispute_statuses) or revision_count > 0
    if dispute_statuses:
        unexpected_reasons.append("explicit dispute status")
    if revision_count > 0:
        unexpected_reasons.append("resolution revised")
    if invalid_count > 0:
        unexpected_reasons.append("invalid resolution path")
    if manual_count > 0:
        unexpected_reasons.append("manual resolution override")
    unexpected_resolution = bool(unexpected_reasons)

    return ResolutionPathSummary(
        condition_id=first_row.condition_id,
        first_resolved_outcome=first_row.resolved_outcome,
        final_resolved_outcome=final_row.resolved_outcome,
        final_resolution_source=final_row.resolution_source,
        first_resolution_event_time=first_row.event_time,
        first_resolution_observed_at=first_row.observed_at,
        resolution_count=len(ordered),
        revision_count=revision_count,
        invalid_count=invalid_count,
        manual_count=manual_count,
        uma_count=uma_count,
        had_dispute=had_dispute,
        unexpected_resolution=unexpected_resolution,
        dispute_statuses=dispute_statuses,
        unexpected_reasons=unexpected_reasons,
    )


async def load_resolution_history(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_days: int,
    limit: int,
) -> dict[str, list[ResolutionHistoryRow]]:
    result = await ch.query(
        """
        SELECT condition_id, resolved_outcome, resolution_source, is_revision,
               prior_observed_at, dispute_status, event_time, observed_at
        FROM market_resolutions
        WHERE observed_at <= {asof:DateTime64(3)}
          AND event_time >= {start:DateTime64(3)}
        ORDER BY condition_id ASC, observed_at ASC
        """,
        parameters={
            "asof": asked_at,
            "start": asked_at - timedelta(days=lookback_days),
        },
    )
    grouped: dict[str, list[ResolutionHistoryRow]] = {}
    for row in result.result_rows:
        history = grouped.setdefault(str(row[0]), [])
        history.append(
            ResolutionHistoryRow(
                condition_id=str(row[0]),
                resolved_outcome=str(row[1]),
                resolution_source=str(row[2] or ""),
                is_revision=bool(row[3]),
                prior_observed_at=row[4],  # type: ignore[arg-type]
                dispute_status=str(row[5]).strip() or None if row[5] is not None else None,
                event_time=row[6],  # type: ignore[arg-type]
                observed_at=row[7],  # type: ignore[arg-type]
            )
        )
    if limit > 0:
        items = list(grouped.items())[:limit]
        return dict(items)
    return grouped


async def build_resolution_risk_corpus_rows(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_days: int,
    limit: int,
) -> tuple[list[tuple[object, ...]], int, int]:
    history_by_condition = await load_resolution_history(
        ch,
        asked_at=asked_at,
        lookback_days=lookback_days,
        limit=limit,
    )
    rows: list[tuple[object, ...]] = []
    disputed_markets = 0
    unexpected_markets = 0
    for condition_id, history in history_by_condition.items():
        summary = summarize_resolution_path(history)
        if summary.had_dispute:
            disputed_markets += 1
        if summary.unexpected_resolution:
            unexpected_markets += 1
        snapshot = await asof_q.latest_market_snapshot_asof(
            ch,
            condition_id,
            summary.first_resolution_observed_at,
        )
        risk_row = await resolution_risk_asof(
            ch,
            condition_id,
            summary.first_resolution_observed_at,
        )
        rows.append(
            resolution_risk_corpus_row(
                condition_id=condition_id,
                question=snapshot.question if snapshot is not None else "",
                description=snapshot.description if snapshot is not None else "",
                snapshot_resolution_source=(
                    snapshot.resolution_source if snapshot is not None else ""
                ),
                first_resolved_outcome=summary.first_resolved_outcome,
                final_resolved_outcome=summary.final_resolved_outcome,
                final_resolution_source=summary.final_resolution_source,
                resolution_count=summary.resolution_count,
                revision_count=summary.revision_count,
                invalid_count=summary.invalid_count,
                manual_count=summary.manual_count,
                uma_count=summary.uma_count,
                had_dispute=summary.had_dispute,
                unexpected_resolution=summary.unexpected_resolution,
                dispute_statuses=summary.dispute_statuses,
                unexpected_reasons=summary.unexpected_reasons,
                risk_score=risk_row.risk_score if risk_row is not None else None,
                risk_level=risk_row.risk_level if risk_row is not None else "",
                risk_flagged=risk_row.is_flagged if risk_row is not None else False,
                risk_classifier=risk_row.classifier if risk_row is not None else "",
                risk_reasons=risk_row.reasons if risk_row is not None else [],
                first_resolution_event_time=summary.first_resolution_event_time,
                first_resolution_observed_at=summary.first_resolution_observed_at,
                observed_at=asked_at,
            )
        )
    return rows, disputed_markets, unexpected_markets


async def run_resolution_risk_corpus_refresh(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
) -> ResolutionRiskCorpusReport:
    settings = get_settings()
    observed_at = asked_at or datetime.now(UTC)
    rows, disputed_markets, unexpected_markets = await build_resolution_risk_corpus_rows(
        ch,
        asked_at=observed_at,
        lookback_days=settings.resolution_risk_corpus_lookback_days,
        limit=settings.resolution_risk_corpus_limit,
    )
    if rows:
        await ch.insert(
            "resolution_risk_corpus",
            rows,
            column_names=RESOLUTION_RISK_CORPUS_COLS,
        )
    return ResolutionRiskCorpusReport(
        asked_at=observed_at,
        lookback_days=settings.resolution_risk_corpus_lookback_days,
        limit=settings.resolution_risk_corpus_limit,
        rows_written=len(rows),
        disputed_markets=disputed_markets,
        unexpected_markets=unexpected_markets,
    )


async def _main() -> None:
    async with get_async_client() as ch:
        report = await run_resolution_risk_corpus_refresh(ch)
    print(
        json.dumps(
            asdict(report),
            default=lambda value: value.isoformat() if isinstance(value, datetime) else value,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(_main())
