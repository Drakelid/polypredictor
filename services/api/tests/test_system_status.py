from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from api.settings import Settings
from api.source_health import SourceHealthSummary
from api.system_status import _source_component, build_system_status


class _QueryResult:
    def __init__(self) -> None:
        self.result_rows: list[tuple[object, ...]] = []


class _FakeClickHouse:
    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        if query == "SELECT 1":
            return _QueryResult()
        return _QueryResult()


class _FakeConn:
    async def fetchval(self, query: str) -> int:
        assert query == "SELECT 1"
        return 1


class _Acquire:
    async def __aenter__(self) -> _FakeConn:
        return _FakeConn()

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakePool:
    def acquire(self) -> _Acquire:
        return _Acquire()


def test_source_component_degrades_on_failure_rate() -> None:
    checked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    component = _source_component(
        SourceHealthSummary(
            source="polymarket.gamma",
            total_requests=100,
            ok_count=95,
            error_count=5,
            rate_limited_count=0,
            timeout_count=0,
            failure_rate=0.05,
            rate_limited_rate=0,
            p50_latency_ms=100,
            p95_latency_ms=300,
            last_observed_at=checked_at,
        ),
        checked_at=checked_at,
        max_failure_rate=0.01,
        max_staleness_minutes=30,
    )
    assert component.state == "degraded"
    assert "guardrail" in component.detail


def test_source_component_degrades_when_stale() -> None:
    checked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    component = _source_component(
        SourceHealthSummary(
            source="polymarket.clob",
            total_requests=100,
            ok_count=100,
            error_count=0,
            rate_limited_count=0,
            timeout_count=0,
            failure_rate=0,
            rate_limited_rate=0,
            p50_latency_ms=100,
            p95_latency_ms=300,
            last_observed_at=checked_at - timedelta(minutes=45),
        ),
        checked_at=checked_at,
        max_failure_rate=0.01,
        max_staleness_minutes=30,
    )
    assert component.state == "degraded"
    assert "stale" in component.detail


@pytest.mark.asyncio
async def test_build_system_status_operational_without_source_rows() -> None:
    status = await build_system_status(
        ch=_FakeClickHouse(),
        pool=_FakePool(),
        settings=Settings(),
        checked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert status.state == "operational"
    assert [component.name for component in status.components] == [
        "postgres",
        "clickhouse",
    ]
