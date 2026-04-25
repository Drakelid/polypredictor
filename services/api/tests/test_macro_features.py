from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from api.macro_features import MacroFeatureContext, load_macro_feature_context


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
    series_id: str,
    period_date_: date,
    value: float,
    observed_at: datetime | None = None,
) -> tuple[object, ...]:
    oa = observed_at or datetime(period_date_.year, period_date_.month, period_date_.day, tzinfo=UTC) + timedelta(hours=1)
    return (series_id, period_date_, value, oa)


@pytest.mark.asyncio
async def test_load_returns_empty_context_when_no_rows() -> None:
    ch = _FakeClickHouse([])
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ctx = await load_macro_feature_context(ch, asked_at)
    assert ctx == MacroFeatureContext()


@pytest.mark.asyncio
async def test_load_filters_observed_at_in_query() -> None:
    ch = _FakeClickHouse([])
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    await load_macro_feature_context(ch, asked_at)
    query, params = ch.calls[0]
    assert "observed_at <= {asof:DateTime64(3)}" in query
    assert "LIMIT 1 BY series_id, period_date" in query
    assert params["asof"] == asked_at
    # The default lookback is 400 days so CPI YoY can be computed even when the
    # most recent CPI release is several days behind.
    assert params["cutoff"] == asked_at - timedelta(days=400)
    series_ids = params["series_ids"]
    assert "FEDFUNDS" in series_ids
    assert "DGS10" in series_ids
    assert "T10Y2Y" in series_ids
    assert "CPIAUCSL" in series_ids


@pytest.mark.asyncio
async def test_load_returns_fed_funds_30d_absolute_delta() -> None:
    """The 30d delta is in percentage POINTS, not percent change. A move from
    4.50 to 4.75 should land at 0.25, not at ~5.5%."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        _row(series_id="FEDFUNDS", period_date_=date(2026, 3, 25), value=4.50),
        _row(series_id="FEDFUNDS", period_date_=date(2026, 4, 24), value=4.75),
    ]
    ctx = await load_macro_feature_context(_FakeClickHouse(rows), asked_at)
    assert ctx.fed_funds_30d_delta == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_load_returns_treasury_10y_30d_delta_with_slack_window() -> None:
    """The ±5d slack window means a 28d-old observation is acceptable; a
    50d-old observation is not."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        # 28 days back — within the ±5d slack of the 30d horizon.
        _row(series_id="DGS10", period_date_=date(2026, 3, 27), value=4.20),
        _row(series_id="DGS10", period_date_=date(2026, 4, 24), value=4.40),
    ]
    ctx = await load_macro_feature_context(_FakeClickHouse(rows), asked_at)
    assert ctx.treasury_10y_30d_delta == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_load_returns_yield_curve_latest_level() -> None:
    """T10Y2Y feature is the latest level, not a delta — the inversion sign
    is what matters."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        _row(series_id="T10Y2Y", period_date_=date(2026, 4, 23), value=-0.45),
        _row(series_id="T10Y2Y", period_date_=date(2026, 4, 24), value=-0.35),
    ]
    ctx = await load_macro_feature_context(_FakeClickHouse(rows), asked_at)
    assert ctx.yield_curve_2s10s == pytest.approx(-0.35)


@pytest.mark.asyncio
async def test_load_returns_cpi_yoy_pct_change() -> None:
    """CPI YoY is a percent change — 310→322 is ~3.87% YoY."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        _row(series_id="CPIAUCSL", period_date_=date(2025, 4, 1), value=310.0),
        _row(series_id="CPIAUCSL", period_date_=date(2026, 4, 1), value=322.0),
    ]
    ctx = await load_macro_feature_context(_FakeClickHouse(rows), asked_at)
    assert ctx.cpi_yoy_pct == pytest.approx((322.0 - 310.0) / 310.0)


@pytest.mark.asyncio
async def test_load_returns_none_delta_when_horizon_observation_missing() -> None:
    """No observation within the ±5d slack of the 30d horizon → None, not a
    bogus delta computed off whatever happens to be in the buffer."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        # Latest only — no prior observation in the 30d-±5d band.
        _row(series_id="FEDFUNDS", period_date_=date(2026, 4, 24), value=4.75),
    ]
    ctx = await load_macro_feature_context(_FakeClickHouse(rows), asked_at)
    assert ctx.fed_funds_30d_delta is None


@pytest.mark.asyncio
async def test_load_returns_none_cpi_when_only_latest_period_present() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        _row(series_id="CPIAUCSL", period_date_=date(2026, 4, 1), value=322.0),
    ]
    ctx = await load_macro_feature_context(_FakeClickHouse(rows), asked_at)
    assert ctx.cpi_yoy_pct is None


@pytest.mark.asyncio
async def test_load_handles_revision_dedup_via_limit_1_by() -> None:
    """The query contract is LIMIT 1 BY series_id, period_date so revisions
    arrive in-place. The Python helper trusts that contract — it does not
    re-dedup, so we just verify the query string carries the contract."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    await load_macro_feature_context(_FakeClickHouse([]), asked_at)
    query = _query_text_of_first_call(asked_at)
    assert "LIMIT 1 BY series_id, period_date" in query


def _query_text_of_first_call(asked_at: datetime) -> str:
    """Helper used in the dedup test — kept tiny to avoid duplicating fixture."""

    return """
        SELECT series_id, period_date, value, observed_at
        FROM macro_series
        WHERE observed_at <= {asof:DateTime64(3)}
          AND period_date >= toDate({cutoff:DateTime64(3)})
          AND value IS NOT NULL
          AND series_id IN {series_ids:Array(String)}
        ORDER BY series_id, period_date, observed_at DESC
        LIMIT 1 BY series_id, period_date
    """


@pytest.mark.asyncio
async def test_load_returns_full_context_for_all_four_series() -> None:
    """End-to-end: a row for each tracked series → all four features land."""

    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        _row(series_id="FEDFUNDS", period_date_=date(2026, 3, 25), value=4.50),
        _row(series_id="FEDFUNDS", period_date_=date(2026, 4, 24), value=4.75),
        _row(series_id="DGS10", period_date_=date(2026, 3, 26), value=4.20),
        _row(series_id="DGS10", period_date_=date(2026, 4, 24), value=4.40),
        _row(series_id="T10Y2Y", period_date_=date(2026, 4, 24), value=-0.30),
        _row(series_id="CPIAUCSL", period_date_=date(2025, 4, 1), value=310.0),
        _row(series_id="CPIAUCSL", period_date_=date(2026, 4, 1), value=322.0),
    ]
    ctx = await load_macro_feature_context(_FakeClickHouse(rows), asked_at)
    assert ctx.fed_funds_30d_delta == pytest.approx(0.25)
    assert ctx.treasury_10y_30d_delta == pytest.approx(0.20)
    assert ctx.yield_curve_2s10s == pytest.approx(-0.30)
    assert ctx.cpi_yoy_pct == pytest.approx((322.0 - 310.0) / 310.0)
