from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from api.onchain_metrics import latest_onchain_metric_asof, list_onchain_metrics


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


def _row(
    *,
    source: str = "glassnode",
    metric_id: str = "transactions/transfers_volume_to_exchanges_sum",
    metric_title: str = "BTC inflow",
    asset: str = "BTC",
    chain: str = "bitcoin",
    category: str = "flow",
    period_date: date | datetime | None = None,
    value: float | None = 1234.5,
    units: str = "BTC",
    event_time: datetime | None = None,
    observed_at: datetime | None = None,
) -> tuple[object, ...]:
    period = period_date or date(2026, 4, 24)
    et = event_time or datetime(2026, 4, 24, tzinfo=UTC)
    oa = observed_at or datetime(2026, 4, 25, 1, 0, tzinfo=UTC)
    return (
        source,
        metric_id,
        metric_title,
        asset,
        chain,
        category,
        period,
        value,
        units,
        et,
        oa,
    )


@pytest.mark.asyncio
async def test_latest_onchain_metric_asof_returns_typed_row() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_row()])

    metric = await latest_onchain_metric_asof(
        ch,
        source="glassnode",
        metric_id="transactions/transfers_volume_to_exchanges_sum",
        asset="BTC",
        asked_at=asked_at,
    )

    assert metric is not None
    assert metric.source == "glassnode"
    assert metric.asset == "BTC"
    assert metric.value == pytest.approx(1234.5)
    assert metric.period_date == datetime(2026, 4, 24)
    assert ch.calls[0][1]["asof"] == asked_at
    assert ch.calls[0][1]["src"] == "glassnode"
    assert ch.calls[0][1]["mid"] == "transactions/transfers_volume_to_exchanges_sum"
    assert ch.calls[0][1]["ast"] == "BTC"


@pytest.mark.asyncio
async def test_latest_onchain_metric_asof_returns_none_when_empty() -> None:
    ch = _FakeClickHouse([])
    metric = await latest_onchain_metric_asof(
        ch,
        source="glassnode",
        metric_id="missing",
        asset="BTC",
        asked_at=datetime(2026, 4, 25, tzinfo=UTC),
    )
    assert metric is None


@pytest.mark.asyncio
async def test_latest_onchain_metric_asof_passes_observed_at_filter() -> None:
    """The query must filter on ``observed_at <= asked_at`` so backtests cannot
    see data that wasn't published yet."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_row()])
    await latest_onchain_metric_asof(
        ch, source="glassnode", metric_id="m", asset="BTC", asked_at=asked_at
    )
    query, _ = ch.calls[0]
    assert "observed_at <= {asof:DateTime64(3)}" in query
    # Order must prefer the latest period_date and then the latest revision.
    assert "ORDER BY period_date DESC, observed_at DESC" in query


@pytest.mark.asyncio
async def test_list_onchain_metrics_orders_by_period_date_desc() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            _row(period_date=date(2026, 4, 22), value=100.0),
            _row(period_date=date(2026, 4, 24), value=200.0),
            _row(period_date=date(2026, 4, 23), value=150.0),
        ]
    )

    rows = await list_onchain_metrics(ch, asked_at=asked_at, lookback_hours=24 * 7)

    assert [r.value for r in rows] == [200.0, 150.0, 100.0]


@pytest.mark.asyncio
async def test_list_onchain_metrics_normalizes_filter_casing() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_row()])

    await list_onchain_metrics(
        ch,
        asked_at=asked_at,
        sources=["glassnode"],
        assets=["btc", "eth"],
        categories=["Flow", "SUPPLY"],
        metric_ids=["transactions/transfers_volume_to_exchanges_sum"],
    )

    _, params = ch.calls[0]
    assert params["assets"] == ["BTC", "ETH"]
    assert params["categories"] == ["flow", "supply"]
    assert params["sources"] == ["glassnode"]
    assert params["metric_ids"] == [
        "transactions/transfers_volume_to_exchanges_sum"
    ]


@pytest.mark.asyncio
async def test_list_onchain_metrics_applies_lookback_cutoff() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_row()])

    await list_onchain_metrics(ch, asked_at=asked_at, lookback_hours=48)

    _, params = ch.calls[0]
    expected_cutoff = asked_at - timedelta(hours=48)
    assert params["cutoff"] == expected_cutoff
    assert params["asof"] == asked_at


@pytest.mark.asyncio
async def test_list_onchain_metrics_dedups_revisions_per_natural_key() -> None:
    """The query must keep only the latest observed_at per natural key so a
    revised row replaces the original instead of double-counting."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_row()])

    await list_onchain_metrics(ch, asked_at=asked_at)

    query, _ = ch.calls[0]
    assert "LIMIT 1 BY source, metric_id, asset, period_date" in query
