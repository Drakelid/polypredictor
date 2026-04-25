from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from api import cost_watch
from api.source_health import SourceHealthSummary


def _summary(source: str, total: int) -> SourceHealthSummary:
    return SourceHealthSummary(
        source=source,
        total_requests=total,
        ok_count=total,
        error_count=0,
        rate_limited_count=0,
        timeout_count=0,
        failure_rate=0.0,
        rate_limited_rate=0.0,
        p50_latency_ms=120.0,
        p95_latency_ms=400.0,
        last_observed_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )


def _settings(**overrides: float | int) -> SimpleNamespace:
    base = dict(
        cost_per_1k_requests_x_api=0.0,
        cost_per_1k_requests_glassnode=0.0,
        cost_per_1k_requests_dune=0.0,
        cost_per_1k_requests_cme_fedwatch=0.0,
        cost_per_1k_requests_deribit=0.0,
        cost_per_1k_requests_polymarket=0.0,
        cost_watch_lookback_hours=24,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_cost_watch_report_aggregates_glassnode_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summaries = [
        _summary("glassnode", 12_000),
        _summary("polymarket.gamma", 50_000),
        _summary("fred", 3_000),  # free
    ]

    async def _fake_summary(
        ch: object,
        *,
        asked_at: datetime,
        lookback_hours: int,
        sources: list[str] | None = None,
    ) -> list[SourceHealthSummary]:
        assert lookback_hours == 24
        return summaries

    monkeypatch.setattr(cost_watch, "source_health_summary_asof", _fake_summary)
    monkeypatch.setattr(
        cost_watch,
        "get_settings",
        lambda: _settings(cost_per_1k_requests_glassnode=0.50),
    )

    report = await cost_watch.cost_watch_report(
        ch=object(),
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    glassnode = next(p for p in report.providers if p.provider == "glassnode")
    polymarket = next(p for p in report.providers if p.provider == "polymarket")
    assert glassnode.request_count == 12_000
    assert glassnode.cost_per_1k_requests == pytest.approx(0.50)
    # 12k requests * $0.50 / 1k = $6.00
    assert glassnode.cost_usd == pytest.approx(6.0)
    assert polymarket.request_count == 50_000
    # Polymarket priced at $0 → no cost.
    assert polymarket.cost_usd == pytest.approx(0.0)
    assert report.total_cost_usd == pytest.approx(6.0)
    assert report.unmapped_sources == []


@pytest.mark.asyncio
async def test_cost_watch_report_collapses_polymarket_subdomains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summaries = [
        _summary("polymarket.gamma", 1_000),
        _summary("polymarket.clob", 2_000),
        _summary("polymarket.data", 4_000),
    ]

    async def _fake_summary(
        ch: object,
        *,
        asked_at: datetime,
        lookback_hours: int,
        sources: list[str] | None = None,
    ) -> list[SourceHealthSummary]:
        return summaries

    monkeypatch.setattr(cost_watch, "source_health_summary_asof", _fake_summary)
    monkeypatch.setattr(
        cost_watch,
        "get_settings",
        lambda: _settings(cost_per_1k_requests_polymarket=2.0),
    )

    report = await cost_watch.cost_watch_report(
        ch=object(),
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert len(report.providers) == 1
    polymarket = report.providers[0]
    assert polymarket.provider == "polymarket"
    assert polymarket.request_count == 7_000
    assert set(polymarket.sources) == {
        "polymarket.gamma",
        "polymarket.clob",
        "polymarket.data",
    }
    # 7k requests * $2 / 1k = $14
    assert polymarket.cost_usd == pytest.approx(14.0)


@pytest.mark.asyncio
async def test_cost_watch_report_surfaces_unmapped_paid_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summaries = [
        _summary("dune", 500),
        _summary("brand.new.unconfigured", 100),
    ]

    async def _fake_summary(
        ch: object,
        *,
        asked_at: datetime,
        lookback_hours: int,
        sources: list[str] | None = None,
    ) -> list[SourceHealthSummary]:
        return summaries

    monkeypatch.setattr(cost_watch, "source_health_summary_asof", _fake_summary)
    monkeypatch.setattr(
        cost_watch,
        "get_settings",
        lambda: _settings(cost_per_1k_requests_dune=1.0),
    )

    report = await cost_watch.cost_watch_report(
        ch=object(),
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert report.unmapped_sources == ["brand.new.unconfigured"]
    dune = next(p for p in report.providers if p.provider == "dune")
    assert dune.cost_usd == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_cost_watch_report_treats_rss_and_reddit_as_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summaries = [
        _summary("rss:bloomberg_markets", 500),
        _summary("rss:coindesk", 800),
        _summary("reddit", 200),
    ]

    async def _fake_summary(
        ch: object,
        *,
        asked_at: datetime,
        lookback_hours: int,
        sources: list[str] | None = None,
    ) -> list[SourceHealthSummary]:
        return summaries

    monkeypatch.setattr(cost_watch, "source_health_summary_asof", _fake_summary)
    monkeypatch.setattr(cost_watch, "get_settings", lambda: _settings())

    report = await cost_watch.cost_watch_report(
        ch=object(),
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert report.providers == []
    assert report.unmapped_sources == []
    assert report.total_cost_usd == 0.0


@pytest.mark.asyncio
async def test_cost_watch_report_rejects_non_positive_lookback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cost_watch, "get_settings", lambda: _settings())
    with pytest.raises(ValueError):
        await cost_watch.cost_watch_report(
            ch=object(),
            asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
            lookback_hours=0,
        )
