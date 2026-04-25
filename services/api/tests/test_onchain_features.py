from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from api.onchain_features import (
    OnchainAssetFeatures,
    OnchainFeatureContext,
    load_onchain_feature_context,
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


def _row(
    *,
    metric_id: str,
    asset: str,
    period_date_: date,
    value: float,
    observed_at: datetime | None = None,
    source: str = "glassnode",
    category: str = "flow",
) -> tuple[object, ...]:
    oa = observed_at or datetime(period_date_.year, period_date_.month, period_date_.day, tzinfo=UTC) + timedelta(hours=1)
    return (source, metric_id, asset, category, period_date_, value, oa)


def test_for_asset_returns_empty_features_when_unknown() -> None:
    ctx = OnchainFeatureContext()
    features = ctx.for_asset("BTC")
    assert features == OnchainAssetFeatures()
    assert ctx.for_asset(None) == OnchainAssetFeatures()
    assert ctx.for_asset("") == OnchainAssetFeatures()


@pytest.mark.asyncio
async def test_load_returns_empty_context_when_no_rows() -> None:
    ch = _FakeClickHouse([])
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ctx = await load_onchain_feature_context(ch, asked_at)
    assert ctx.by_asset == {}
    assert ctx.stablecoin_supply_delta_pct is None
    assert ctx.for_asset("BTC") == OnchainAssetFeatures()


@pytest.mark.asyncio
async def test_load_filters_observed_at_in_query() -> None:
    ch = _FakeClickHouse([])
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    await load_onchain_feature_context(ch, asked_at)
    query, params = ch.calls[0]
    assert "observed_at <= {asof:DateTime64(3)}" in query
    assert "LIMIT 1 BY source, metric_id, asset, period_date" in query
    assert params["asof"] == asked_at
    assert params["cutoff"] == asked_at - timedelta(days=30)


@pytest.mark.asyncio
async def test_load_returns_zscore_for_inflow_with_enough_history() -> None:
    """5+ historical points + a latest point should produce a z-score; the
    test fixture deliberately puts the latest 1000 BTC inflow ~3 stds above
    a stable historical mean of 200 so the sign is unambiguous."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = []
    historical = [180.0, 200.0, 210.0, 195.0, 215.0, 205.0]
    for i, value in enumerate(historical):
        rows.append(
            _row(
                metric_id="transactions/transfers_volume_to_exchanges_sum",
                asset="BTC",
                period_date_=date(2026, 4, 18 + i),
                value=value,
            )
        )
    # Latest period date == 2026-04-24; spike to 1000 BTC inflow.
    rows.append(
        _row(
            metric_id="transactions/transfers_volume_to_exchanges_sum",
            asset="BTC",
            period_date_=date(2026, 4, 24),
            value=1000.0,
        )
    )
    ch = _FakeClickHouse(rows)
    ctx = await load_onchain_feature_context(ch, asked_at)
    btc = ctx.for_asset("BTC")
    assert btc.exchange_inflow_z is not None
    assert btc.exchange_inflow_z > 5.0  # well above any reasonable threshold


@pytest.mark.asyncio
async def test_load_returns_none_zscore_when_history_too_short() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        _row(
            metric_id="transactions/transfers_volume_to_exchanges_sum",
            asset="BTC",
            period_date_=date(2026, 4, 23),
            value=200.0,
        ),
        _row(
            metric_id="transactions/transfers_volume_to_exchanges_sum",
            asset="BTC",
            period_date_=date(2026, 4, 24),
            value=1000.0,
        ),
    ]
    ch = _FakeClickHouse(rows)
    ctx = await load_onchain_feature_context(ch, asked_at)
    assert ctx.for_asset("BTC").exchange_inflow_z is None


@pytest.mark.asyncio
async def test_load_returns_whale_count_delta_pct_at_horizon() -> None:
    """The whale-count delta pct compares the latest value to the closest
    observation ~7 days before. A 100→120 jump should land at +0.20."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        _row(
            metric_id="addresses/min_1k_count",
            asset="BTC",
            period_date_=date(2026, 4, 17),
            value=100.0,
            category="whale",
        ),
        _row(
            metric_id="addresses/min_1k_count",
            asset="BTC",
            period_date_=date(2026, 4, 24),
            value=120.0,
            category="whale",
        ),
    ]
    ch = _FakeClickHouse(rows)
    ctx = await load_onchain_feature_context(ch, asked_at)
    btc = ctx.for_asset("BTC")
    assert btc.whale_count_delta_pct == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_load_returns_stablecoin_supply_delta_summed_across_assets() -> None:
    """The stablecoin supply pct delta is summed across USDT+USDC so the
    booster sees one asset-agnostic growth scalar, not two correlated ones."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        # 2026-04-17: USDT 100B, USDC 50B -> total 150B
        _row(
            metric_id="supply/current",
            asset="USDT",
            period_date_=date(2026, 4, 17),
            value=100_000_000_000.0,
            category="supply",
        ),
        _row(
            metric_id="supply/current",
            asset="USDC",
            period_date_=date(2026, 4, 17),
            value=50_000_000_000.0,
            category="supply",
        ),
        # 2026-04-24: USDT 110B, USDC 55B -> total 165B (+10%)
        _row(
            metric_id="supply/current",
            asset="USDT",
            period_date_=date(2026, 4, 24),
            value=110_000_000_000.0,
            category="supply",
        ),
        _row(
            metric_id="supply/current",
            asset="USDC",
            period_date_=date(2026, 4, 24),
            value=55_000_000_000.0,
            category="supply",
        ),
    ]
    ch = _FakeClickHouse(rows)
    ctx = await load_onchain_feature_context(ch, asked_at)
    assert ctx.stablecoin_supply_delta_pct == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_load_returns_none_stablecoin_delta_when_only_latest_present() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        _row(
            metric_id="supply/current",
            asset="USDT",
            period_date_=date(2026, 4, 24),
            value=100_000_000_000.0,
            category="supply",
        ),
    ]
    ch = _FakeClickHouse(rows)
    ctx = await load_onchain_feature_context(ch, asked_at)
    assert ctx.stablecoin_supply_delta_pct is None


@pytest.mark.asyncio
async def test_load_keeps_assets_separate_in_zscore_calculation() -> None:
    """A latest BTC value within its own historical distribution should not
    register as an outlier just because ETH had different magnitudes."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = []
    # BTC: tight cluster ~200 with latest at 210 -> small z-score.
    btc_history = [195.0, 200.0, 205.0, 198.0, 202.0, 199.0]
    for i, value in enumerate(btc_history):
        rows.append(
            _row(
                metric_id="transactions/transfers_volume_to_exchanges_sum",
                asset="BTC",
                period_date_=date(2026, 4, 18 + i),
                value=value,
            )
        )
    rows.append(
        _row(
            metric_id="transactions/transfers_volume_to_exchanges_sum",
            asset="BTC",
            period_date_=date(2026, 4, 24),
            value=210.0,
        )
    )
    # ETH: 5000 baseline; latest 50000 is a huge outlier within ETH.
    eth_history = [4500.0, 5000.0, 5200.0, 4900.0, 5100.0, 4800.0]
    for i, value in enumerate(eth_history):
        rows.append(
            _row(
                metric_id="transactions/transfers_volume_to_exchanges_sum",
                asset="ETH",
                period_date_=date(2026, 4, 18 + i),
                value=value,
            )
        )
    rows.append(
        _row(
            metric_id="transactions/transfers_volume_to_exchanges_sum",
            asset="ETH",
            period_date_=date(2026, 4, 24),
            value=50_000.0,
        )
    )
    ch = _FakeClickHouse(rows)
    ctx = await load_onchain_feature_context(ch, asked_at)
    btc = ctx.for_asset("BTC")
    eth = ctx.for_asset("ETH")
    assert btc.exchange_inflow_z is not None
    assert eth.exchange_inflow_z is not None
    assert abs(btc.exchange_inflow_z) < 5.0
    assert eth.exchange_inflow_z > 50.0
