"""Aggregations over ``ingest_health`` for the operator dashboard (PRD §8).

The PRD calls for a < 1% per-source-per-day failure rate as a launch-gate
guardrail and for rate-limit headroom + cost-watch dashboards in M8 beta
readiness. The :class:`HealthSink` already logs every HTTP request from the
ingestion path, including ``status`` (ok/rate_limited/error/timeout),
``latency_ms``, and ``http_status``. This module turns those rows into the
roll-ups the dashboard needs.

Two aggregation shapes are supported:

* :func:`source_health_summary_asof` — one row per source covering a trailing
  window: total request count, success/error/rate_limited/timeout splits,
  failure rate as a fraction in [0, 1], and latency p50/p95.
* :func:`source_health_timeseries` — bucketed counts so the dashboard can
  draw a "last 24h" sparkline per source.

The reader is read-only — it never writes back, so it's safe to call from
public-facing endpoints. All results are PIT: every row filters
``observed_at <= asked_at``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class SourceHealthSummary:
    source: str
    total_requests: int
    ok_count: int
    error_count: int
    rate_limited_count: int
    timeout_count: int
    failure_rate: float  # (error + timeout) / total, ignoring rate_limited
    rate_limited_rate: float  # rate_limited / total
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    last_observed_at: datetime | None


@dataclass(frozen=True)
class SourceHealthBucket:
    source: str
    bucket_start: datetime
    total_requests: int
    failure_count: int
    rate_limited_count: int


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def source_health_summary_asof(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_hours: int = 24,
    sources: list[str] | None = None,
) -> list[SourceHealthSummary]:
    """One summary row per source over the trailing ``lookback_hours``.

    Failure rate is ``(error + timeout) / total``. Rate-limited responses are
    bucketed separately rather than counted as failures because they're a
    headroom signal, not a correctness signal — the client already retries
    after Retry-After.
    """
    cutoff = asked_at - timedelta(hours=lookback_hours)
    parts = [
        "SELECT",
        "  source,",
        "  count() AS total_requests,",
        "  countIf(status = 'ok') AS ok_count,",
        "  countIf(status = 'error') AS error_count,",
        "  countIf(status = 'rate_limited') AS rate_limited_count,",
        "  countIf(status = 'timeout') AS timeout_count,",
        "  quantileExactIf(0.50)(latency_ms, status = 'ok') AS p50_latency_ms,",
        "  quantileExactIf(0.95)(latency_ms, status = 'ok') AS p95_latency_ms,",
        "  max(observed_at) AS last_observed_at",
        "FROM ingest_health",
        "WHERE observed_at <= {asof:DateTime64(3)}",
        "  AND observed_at >= {cutoff:DateTime64(3)}",
    ]
    params: dict[str, object] = {"asof": asked_at, "cutoff": cutoff}
    if sources:
        parts.append("  AND source IN {sources:Array(String)}")
        params["sources"] = sources
    parts.extend(["GROUP BY source", "ORDER BY source"])
    query = "\n".join(parts)

    result = await ch.query(query, parameters=params)
    summaries: list[SourceHealthSummary] = []
    for row in result.result_rows:
        (
            source,
            total,
            ok,
            error,
            rate_limited,
            timeout,
            p50_latency,
            p95_latency,
            last_observed,
        ) = row
        failures = int(error) + int(timeout)
        total_int = int(total)
        summaries.append(
            SourceHealthSummary(
                source=str(source),
                total_requests=total_int,
                ok_count=int(ok),
                error_count=int(error),
                rate_limited_count=int(rate_limited),
                timeout_count=int(timeout),
                failure_rate=_safe_div(failures, total_int),
                rate_limited_rate=_safe_div(int(rate_limited), total_int),
                p50_latency_ms=(
                    float(p50_latency) if p50_latency is not None else None
                ),
                p95_latency_ms=(
                    float(p95_latency) if p95_latency is not None else None
                ),
                last_observed_at=(
                    _coerce_utc(last_observed)
                    if isinstance(last_observed, datetime)
                    else None
                ),
            )
        )
    return summaries


async def source_health_timeseries(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_hours: int = 24,
    bucket_minutes: int = 60,
    sources: list[str] | None = None,
) -> list[SourceHealthBucket]:
    """Bucketed timeseries: one row per (source, bucket) over the window.

    Buckets snap to ``bucket_minutes``-aligned starts so a 60-minute bucket
    rendered at 12:34 includes [12:00, 13:00). Failure count = error +
    timeout; rate_limited is reported separately so the UI can colour-code
    headroom pressure distinctly from real errors.
    """
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")
    cutoff = asked_at - timedelta(hours=lookback_hours)
    bucket_seconds = int(bucket_minutes * 60)
    parts = [
        "SELECT",
        "  source,",
        "  toStartOfInterval(observed_at, INTERVAL {bucket_seconds:UInt32} SECOND) AS bucket_start,",
        "  count() AS total_requests,",
        "  countIf(status IN ('error', 'timeout')) AS failure_count,",
        "  countIf(status = 'rate_limited') AS rate_limited_count",
        "FROM ingest_health",
        "WHERE observed_at <= {asof:DateTime64(3)}",
        "  AND observed_at >= {cutoff:DateTime64(3)}",
    ]
    params: dict[str, object] = {
        "asof": asked_at,
        "cutoff": cutoff,
        "bucket_seconds": bucket_seconds,
    }
    if sources:
        parts.append("  AND source IN {sources:Array(String)}")
        params["sources"] = sources
    parts.extend(
        [
            "GROUP BY source, bucket_start",
            "ORDER BY source, bucket_start",
        ]
    )
    query = "\n".join(parts)
    result = await ch.query(query, parameters=params)
    return [
        SourceHealthBucket(
            source=str(row[0]),
            bucket_start=row[1],
            total_requests=int(row[2]),
            failure_count=int(row[3]),
            rate_limited_count=int(row[4]),
        )
        for row in result.result_rows
    ]
