"""Operational status roll-up for beta readiness.

The public ``/healthz`` endpoint stays intentionally small for load balancers.
This module powers the human-facing status page and uptime monitor: it checks
the backing stores and folds in the same per-source ingest-health guardrail used
elsewhere in the operator dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from clickhouse_connect.driver.asyncclient import AsyncClient

from . import source_health as source_health_q
from .settings import Settings

ComponentState = Literal["operational", "degraded", "down", "unknown"]


@dataclass(frozen=True)
class StatusComponent:
    name: str
    state: ComponentState
    detail: str
    last_observed_at: datetime | None = None


@dataclass(frozen=True)
class SystemStatus:
    state: ComponentState
    checked_at: datetime
    components: list[StatusComponent]


async def _check_clickhouse(ch: AsyncClient) -> StatusComponent:
    try:
        await ch.query("SELECT 1")
    except Exception as exc:
        return StatusComponent(
            name="clickhouse",
            state="down",
            detail=f"query failed: {type(exc).__name__}",
        )
    return StatusComponent(
        name="clickhouse",
        state="operational",
        detail="query ok",
        last_observed_at=datetime.now(tz=UTC),
    )


async def _check_postgres(pool: Any) -> StatusComponent:
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        return StatusComponent(
            name="postgres",
            state="down",
            detail=f"query failed: {type(exc).__name__}",
        )
    return StatusComponent(
        name="postgres",
        state="operational",
        detail="query ok",
        last_observed_at=datetime.now(tz=UTC),
    )


def _source_component(
    summary: source_health_q.SourceHealthSummary,
    *,
    checked_at: datetime,
    max_failure_rate: float,
    max_staleness_minutes: int,
) -> StatusComponent:
    state: ComponentState = "operational"
    detail_parts = [
        f"{summary.total_requests} requests",
        f"{summary.failure_rate:.2%} failures",
        f"{summary.rate_limited_rate:.2%} rate limited",
    ]
    if summary.failure_rate > max_failure_rate:
        state = "degraded"
        detail_parts.append(f"failure-rate guardrail {max_failure_rate:.2%} breached")
    last_observed_at = _coerce_utc(summary.last_observed_at)
    if last_observed_at is None:
        state = "unknown"
        detail_parts.append("no observations in window")
    else:
        staleness = _coerce_utc(checked_at) - last_observed_at
        if staleness > timedelta(minutes=max_staleness_minutes):
            state = "degraded"
            detail_parts.append(f"stale for {int(staleness.total_seconds() // 60)}m")
    return StatusComponent(
        name=f"source:{summary.source}",
        state=state,
        detail=", ".join(detail_parts),
        last_observed_at=last_observed_at,
    )


def _coerce_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _overall_state(components: list[StatusComponent]) -> ComponentState:
    if any(c.state == "down" for c in components):
        return "down"
    if any(c.state == "degraded" for c in components):
        return "degraded"
    if components and all(c.state == "unknown" for c in components):
        return "unknown"
    return "operational"


async def build_system_status(
    *,
    ch: AsyncClient,
    pool: Any,
    settings: Settings,
    checked_at: datetime | None = None,
) -> SystemStatus:
    now = checked_at or datetime.now(tz=UTC)
    components = [
        await _check_postgres(pool),
        await _check_clickhouse(ch),
    ]
    summaries = await source_health_q.source_health_summary_asof(
        ch,
        asked_at=now,
        lookback_hours=settings.status_source_lookback_hours,
    )
    components.extend(
        _source_component(
            summary,
            checked_at=now,
            max_failure_rate=settings.status_source_failure_rate_threshold,
            max_staleness_minutes=settings.status_source_staleness_minutes,
        )
        for summary in summaries
    )
    return SystemStatus(
        state=_overall_state(components),
        checked_at=now,
        components=components,
    )
