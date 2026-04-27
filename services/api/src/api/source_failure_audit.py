"""M5 exit-criteria audit: P0/P1 source failure-rate check.

Reads the ``ingest_health`` ClickHouse table over a trailing window and
computes per-source failure rates.  The M5 exit criterion is:

    every P0/P1 source must have a failure rate < 1% (0.01) over the
    last 24 h of live operation.

The P0/P1 source list matches PRD §5.2 and can be overridden via the
``p0_p1_sources_file`` setting.  When no config file is provided the
module falls back to the hardcoded default list below.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from clickhouse_connect.driver.asyncclient import AsyncClient

from .clickhouse import get_async_client
from .settings import Settings, get_settings

# Default P0/P1 source names (must match what ingest workers write as ``source``
# in ``ingest_health``).
DEFAULT_P0_P1_SOURCES: list[str] = [
    "gamma",
    "clob",
    "clob_book",
    "wss_market",
    "prices_history_backfill",
    "trades_sweeper",
    "smart_money_refresh",
    "holders_poll",
    "arb_checker",
    "external_divergence",
    "microstructure_signals",
    "rss",
    "reddit",
    "x",
    "regime_tagger",
    "deribit_iv_surface",
    "spot_price_validation",
    "perp_funding_basis",
    "macro_series",
    "onchain_metrics",
]


@dataclass(frozen=True)
class SourceFailureRow:
    source: str
    total_requests: int
    error_requests: int
    failure_rate: float
    is_p0_p1: bool
    meets_sla: bool  # failure_rate < threshold
    priority: str  # "P0/P1" or "other"


@dataclass(frozen=True)
class SourceFailureAuditReport:
    as_of: datetime
    window_hours: int
    failure_rate_threshold: float
    total_sources: int
    p0_p1_sources_total: int
    p0_p1_sources_meeting_sla: int
    p0_p1_sources_failing_sla: int
    all_p0_p1_meet_sla: bool
    sources: list[SourceFailureRow]


def _load_p0_p1_list(config_file: str | None) -> list[str]:
    """Load the P0/P1 source list from a JSON file, or use the default."""
    if not config_file:
        return list(DEFAULT_P0_P1_SOURCES)
    try:
        with open(config_file, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, list):
            return [str(s) for s in raw if s]
    except Exception:
        pass
    return list(DEFAULT_P0_P1_SOURCES)


async def run_source_failure_audit(
    ch: AsyncClient,
    *,
    window_hours: int = 24,
    failure_rate_threshold: float = 0.01,
    p0_p1_sources: list[str] | None = None,
    asked_at: datetime | None = None,
) -> SourceFailureAuditReport:
    """Compute per-source failure rates from ``ingest_health``.

    Parameters
    ----------
    ch:
        ClickHouse async client.
    window_hours:
        How many trailing hours of ``ingest_health`` rows to aggregate.
    failure_rate_threshold:
        The maximum acceptable failure rate for P0/P1 sources (default 0.01).
    p0_p1_sources:
        Override the P0/P1 source list.  ``None`` uses the default.
    asked_at:
        Point-in-time for the query window (default: now).
    """
    as_of = asked_at or datetime.now(tz=UTC)
    window_start = as_of - timedelta(hours=window_hours)
    tracked = set(p0_p1_sources or DEFAULT_P0_P1_SOURCES)

    query = """
        SELECT
            source,
            countIf(status IN ('ok', 'rate_limited')) AS ok_requests,
            countIf(status = 'error') AS error_requests,
            count() AS total_requests
        FROM ingest_health
        WHERE observed_at BETWEEN {start:DateTime64(3)} AND {asof:DateTime64(3)}
        GROUP BY source
        ORDER BY source
    """
    try:
        result = await ch.query(
            query,
            parameters={"start": window_start, "asof": as_of},
        )
        rows_raw = result.result_rows
    except Exception:
        rows_raw = []

    source_rows: list[SourceFailureRow] = []
    for row in rows_raw:
        source = str(row[0])
        # ok_requests = row[1]  # available if needed
        error_requests = int(row[2])
        total_requests = int(row[3])
        failure_rate = error_requests / total_requests if total_requests > 0 else 0.0
        is_p0_p1 = source in tracked
        meets_sla = failure_rate < failure_rate_threshold
        source_rows.append(
            SourceFailureRow(
                source=source,
                total_requests=total_requests,
                error_requests=error_requests,
                failure_rate=round(failure_rate, 6),
                is_p0_p1=is_p0_p1,
                meets_sla=meets_sla,
                priority="P0/P1" if is_p0_p1 else "other",
            )
        )

    p0_p1_rows = [r for r in source_rows if r.is_p0_p1]
    meeting = sum(1 for r in p0_p1_rows if r.meets_sla)
    failing = len(p0_p1_rows) - meeting

    return SourceFailureAuditReport(
        as_of=as_of,
        window_hours=window_hours,
        failure_rate_threshold=failure_rate_threshold,
        total_sources=len(source_rows),
        p0_p1_sources_total=len(p0_p1_rows),
        p0_p1_sources_meeting_sla=meeting,
        p0_p1_sources_failing_sla=failing,
        all_p0_p1_meet_sla=failing == 0 and len(p0_p1_rows) > 0,
        sources=source_rows,
    )


async def _main() -> None:
    settings = get_settings()
    p0_p1 = _load_p0_p1_list(getattr(settings, "p0_p1_sources_file", None))
    ch = await get_async_client()
    try:
        report = await run_source_failure_audit(ch, p0_p1_sources=p0_p1)
        import json as _json

        def _serial(obj: object) -> object:
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, float) and math.isnan(obj):
                return None
            raise TypeError(f"not serialisable: {type(obj)}")

        print(_json.dumps(asdict(report), indent=2, default=_serial))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
