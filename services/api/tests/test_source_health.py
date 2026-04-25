from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api.source_health import (
    source_health_summary_asof,
    source_health_timeseries,
)


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self.rows)


def _summary_row(
    *,
    source: str = "polymarket.gamma",
    total: int = 1000,
    ok: int = 990,
    error: int = 5,
    rate_limited: int = 3,
    timeout: int = 2,
    p50: float | None = 120.0,
    p95: float | None = 450.0,
    last: datetime | None = None,
) -> tuple[object, ...]:
    return (
        source,
        total,
        ok,
        error,
        rate_limited,
        timeout,
        p50,
        p95,
        last or datetime(2026, 4, 25, 12, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_summary_asof_computes_failure_rate_excluding_rate_limited() -> None:
    """The PRD §8 guardrail is "source-failure rate < 1%". Rate-limited
    responses are headroom signal not a failure — they must NOT inflate the
    failure rate, otherwise rolling-out a polite client would look like an
    incident."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [_summary_row(total=1000, ok=990, error=5, rate_limited=3, timeout=2)]
    summaries = await source_health_summary_asof(
        _FakeClickHouse(rows), asked_at=asked_at, lookback_hours=24
    )
    assert len(summaries) == 1
    s = summaries[0]
    # error + timeout = 7 / 1000 = 0.007
    assert s.failure_rate == pytest.approx(0.007)
    # rate_limited reported separately
    assert s.rate_limited_rate == pytest.approx(0.003)
    assert s.p50_latency_ms == pytest.approx(120.0)
    assert s.p95_latency_ms == pytest.approx(450.0)


@pytest.mark.asyncio
async def test_summary_asof_handles_zero_request_total() -> None:
    """A source with no recent activity returns 0/0 rates rather than dividing
    by zero — guard against the obvious bug."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [_summary_row(total=0, ok=0, error=0, rate_limited=0, timeout=0)]
    summaries = await source_health_summary_asof(
        _FakeClickHouse(rows), asked_at=asked_at, lookback_hours=24
    )
    assert summaries[0].failure_rate == 0.0
    assert summaries[0].rate_limited_rate == 0.0


@pytest.mark.asyncio
async def test_summary_asof_filters_observed_at_pit() -> None:
    """Reads must filter observed_at <= asked_at so a backtest dashboard
    showing source-health "as of yesterday" doesn't see today's data."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_summary_row()])
    await source_health_summary_asof(ch, asked_at=asked_at, lookback_hours=24)
    query, params = ch.calls[0]
    assert "observed_at <= {asof:DateTime64(3)}" in query
    assert params["asof"] == asked_at
    assert params["cutoff"] == asked_at - timedelta(hours=24)


@pytest.mark.asyncio
async def test_summary_asof_passes_source_filter() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])
    await source_health_summary_asof(
        ch, asked_at=asked_at, sources=["polymarket.gamma", "polymarket.clob"]
    )
    _, params = ch.calls[0]
    assert params["sources"] == ["polymarket.gamma", "polymarket.clob"]


@pytest.mark.asyncio
async def test_summary_asof_handles_null_quantiles() -> None:
    """ClickHouse returns NULL for quantiles when no rows match the inner
    filter (e.g. all requests failed → quantile over status='ok' is NULL).
    The reader must not crash."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [_summary_row(p50=None, p95=None)]
    summaries = await source_health_summary_asof(
        _FakeClickHouse(rows), asked_at=asked_at
    )
    assert summaries[0].p50_latency_ms is None
    assert summaries[0].p95_latency_ms is None


@pytest.mark.asyncio
async def test_timeseries_returns_typed_buckets() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        ("polymarket.gamma", datetime(2026, 4, 25, 11, tzinfo=UTC), 50, 1, 0),
        ("polymarket.gamma", datetime(2026, 4, 25, 12, tzinfo=UTC), 60, 0, 2),
    ]
    buckets = await source_health_timeseries(
        _FakeClickHouse(rows),
        asked_at=asked_at,
        lookback_hours=2,
        bucket_minutes=60,
    )
    assert len(buckets) == 2
    assert buckets[0].total_requests == 50
    assert buckets[0].failure_count == 1
    assert buckets[1].rate_limited_count == 2


@pytest.mark.asyncio
async def test_timeseries_rejects_non_positive_bucket_minutes() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    with pytest.raises(ValueError):
        await source_health_timeseries(
            _FakeClickHouse([]), asked_at=asked_at, bucket_minutes=0
        )


@pytest.mark.asyncio
async def test_timeseries_passes_bucket_seconds_to_query() -> None:
    """``toStartOfInterval`` works in seconds, so the helper must convert
    minutes → seconds before binding the parameter."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])
    await source_health_timeseries(
        ch, asked_at=asked_at, lookback_hours=2, bucket_minutes=15
    )
    _, params = ch.calls[0]
    assert params["bucket_seconds"] == 15 * 60
