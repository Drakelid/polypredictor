"""Retention and cold-archive planning for ClickHouse time-series tables."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .clickhouse import get_async_client
from .settings import IngestSettings, get_settings

ARCHIVE_TABLES = [
    "markets_snapshots",
    "market_quotes",
    "orderbook_snapshots",
    "trades_raw",
    "prices_history",
    "holders_snapshots",
    "positions_smart_money",
    "external_events",
    "market_features",
    "signal_events",
    "market_resolution_risk",
    "deribit_iv_surface",
    "validated_spot_prices",
    "perp_funding_basis",
    "macro_series",
    "onchain_metrics",
]


@dataclass(frozen=True)
class ArchiveCandidate:
    table: str
    partition_id: str
    min_observed_at: datetime
    max_observed_at: datetime
    rows: int


@dataclass(frozen=True)
class ArchivePlan:
    generated_at: datetime
    archive_before: datetime
    retention_years: int
    destination_uri: str | None
    candidates: list[ArchiveCandidate]


def archive_cutoff(*, now: datetime, settings: IngestSettings) -> datetime:
    target_year = now.year - settings.retention_cold_archive_after_years
    try:
        return now.replace(year=target_year)
    except ValueError:
        return now.replace(year=target_year, day=28)


async def archive_plan_asof(
    ch: Any,
    *,
    settings: IngestSettings,
    now: datetime | None = None,
) -> ArchivePlan:
    generated_at = now or datetime.now(tz=UTC)
    archive_before = archive_cutoff(now=generated_at, settings=settings)
    query = """
        SELECT
          table_name,
          partition_id,
          min_observed_at,
          max_observed_at,
          row_count
        FROM
        (
          SELECT
            {table:String} AS table_name,
            toString(toYYYYMM(observed_at)) AS partition_id,
            min(observed_at) AS min_observed_at,
            max(observed_at) AS max_observed_at,
            count() AS row_count
          FROM {table:Identifier}
          WHERE observed_at < {archive_before:DateTime64(3)}
          GROUP BY partition_id
        )
        ORDER BY table_name, partition_id
    """
    candidates: list[ArchiveCandidate] = []
    for table in ARCHIVE_TABLES:
        result = await ch.query(
            query,
            parameters={"table": table, "archive_before": archive_before},
        )
        for row in result.result_rows:
            candidates.append(
                ArchiveCandidate(
                    table=str(row[0]),
                    partition_id=str(row[1]),
                    min_observed_at=row[2],
                    max_observed_at=row[3],
                    rows=int(row[4]),
                )
            )
    return ArchivePlan(
        generated_at=generated_at,
        archive_before=archive_before,
        retention_years=settings.retention_years,
        destination_uri=settings.retention_cold_archive_uri,
        candidates=candidates,
    )


def write_manifest(plan: ArchivePlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(plan)
    payload["generated_at"] = plan.generated_at.isoformat()
    payload["archive_before"] = plan.archive_before.isoformat()
    payload["candidates"] = [
        {
            **asdict(candidate),
            "min_observed_at": candidate.min_observed_at.isoformat(),
            "max_observed_at": candidate.max_observed_at.isoformat(),
        }
        for candidate in plan.candidates
    ]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


async def run_once(*, manifest_path: str | None = None) -> ArchivePlan:
    settings = get_settings()
    ch = await get_async_client()
    try:
        plan = await archive_plan_asof(ch, settings=settings)
    finally:
        await ch.close()
    if manifest_path:
        write_manifest(plan, Path(manifest_path))
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a cold-archive manifest.")
    parser.add_argument(
        "--manifest",
        default="artifacts/retention/archive-manifest.json",
        help="Path to write the JSON archive manifest.",
    )
    args = parser.parse_args()
    plan = asyncio.run(run_once(manifest_path=args.manifest))
    print(
        f"archive_candidates={len(plan.candidates)} "
        f"archive_before={plan.archive_before.isoformat()} "
        f"manifest={args.manifest}"
    )


if __name__ == "__main__":
    main()
