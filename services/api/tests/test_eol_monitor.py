"""Tests for the M8.3 EOL monitor checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from api import eol_monitor
from api.eol_monitor import (
    EolMonitorReport,
    LastHourFpResult,
    PhantomEdgeResult,
    RampUpResult,
    check_phantom_edge_density,
    check_ramp_up_on_live_markets,
    run_eol_fp_retrospective,
    run_eol_monitor,
)


_NOW = datetime(2026, 4, 28, 12, tzinfo=UTC)


class _QueryResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    """Sequential-response stub matching the order eol_monitor issues queries."""

    def __init__(self, responses: list[list[tuple[Any, ...]]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> _QueryResult:
        idx = len(self.calls)
        self.calls.append((query, parameters))
        if idx >= len(self._responses):
            return _QueryResult([])
        return _QueryResult(self._responses[idx])


# ---------------------------------------------------------------------------
# check_ramp_up_on_live_markets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ramp_up_skips_inactive_markets() -> None:
    # Two markets, both with end_date 1 day away: one active, one closed.
    end_date = _NOW + timedelta(hours=1)
    first_obs = _NOW - timedelta(days=10)
    rows = [
        ("active-cond", end_date, first_obs, 1, 0, 0),
        ("closed-cond", end_date, first_obs, 1, 1, 0),
        ("archived-cond", end_date, first_obs, 1, 0, 1),
        ("inactive-cond", end_date, first_obs, 0, 0, 0),
    ]
    ch = _FakeClickHouse([rows])

    result = await check_ramp_up_on_live_markets(ch, _NOW)

    # Only the active market is counted.
    assert result.markets_checked == 1
    assert result.markets_in_ramp == 1
    assert result.markets_in_phantom_window == 1
    assert result.sample_market_ids == ["active-cond"]


@pytest.mark.asyncio
async def test_ramp_up_excludes_markets_outside_final_fraction() -> None:
    # 10-day market: 9 days remain → outside the 5% ramp.
    rows = [
        (
            "fresh-cond",
            _NOW + timedelta(days=9),
            _NOW - timedelta(days=1),
            1,
            0,
            0,
        ),
    ]
    ch = _FakeClickHouse([rows])

    result = await check_ramp_up_on_live_markets(ch, _NOW)

    assert result.markets_checked == 1  # we did look at it
    assert result.markets_in_ramp == 0  # but it's not in the ramp
    assert result.markets_in_phantom_window == 0
    assert result.mean_convergence_weight == 0.0
    assert result.sample_market_ids == []


@pytest.mark.asyncio
async def test_ramp_up_skips_markets_past_end_date() -> None:
    # end_date already passed → not a live market.
    rows = [
        (
            "past-cond",
            _NOW - timedelta(hours=1),
            _NOW - timedelta(days=10),
            1,
            0,
            0,
        ),
    ]
    ch = _FakeClickHouse([rows])

    result = await check_ramp_up_on_live_markets(ch, _NOW)

    assert result.markets_checked == 0
    assert result.markets_in_ramp == 0


@pytest.mark.asyncio
async def test_ramp_up_orders_samples_by_descending_weight() -> None:
    # Two markets at different ramp depths; 100h total life, 5h ramp window.
    rows = [
        # Inside ramp: 1h remaining → weight ≈ 0.8
        ("near-cond", _NOW + timedelta(hours=1), _NOW - timedelta(hours=99), 1, 0, 0),
        # Inside ramp: 4h remaining → weight ≈ 0.2
        ("far-cond", _NOW + timedelta(hours=4), _NOW - timedelta(hours=96), 1, 0, 0),
    ]
    ch = _FakeClickHouse([rows])

    result = await check_ramp_up_on_live_markets(ch, _NOW)

    assert result.markets_in_ramp == 2
    assert result.sample_market_ids[0] == "near-cond"
    assert result.sample_market_ids[1] == "far-cond"
    assert result.max_convergence_weight > result.mean_convergence_weight


@pytest.mark.asyncio
async def test_ramp_up_naive_datetimes_treated_as_utc() -> None:
    # ClickHouse may hand back naive datetimes; they must be normalised.
    end_date = (_NOW + timedelta(minutes=30)).replace(tzinfo=None)
    first_obs = (_NOW - timedelta(days=10)).replace(tzinfo=None)
    rows = [("naive-cond", end_date, first_obs, 1, 0, 0)]
    ch = _FakeClickHouse([rows])

    result = await check_ramp_up_on_live_markets(ch, _NOW)

    assert result.markets_in_ramp == 1


@pytest.mark.asyncio
async def test_ramp_up_query_failure_returns_empty_report() -> None:
    class _ExplodingClient:
        async def query(self, *args: Any, **kwargs: Any) -> _QueryResult:
            raise RuntimeError("clickhouse down")

    result = await check_ramp_up_on_live_markets(_ExplodingClient(), _NOW)

    assert result == RampUpResult(
        markets_checked=0,
        markets_in_ramp=0,
        markets_in_phantom_window=0,
        mean_convergence_weight=0.0,
        max_convergence_weight=0.0,
        sample_market_ids=[],
    )


# ---------------------------------------------------------------------------
# check_phantom_edge_density
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phantom_edge_density_counts_alerts_and_dedups_samples() -> None:
    rows = [
        ("cond-1",),
        ("cond-1",),
        ("cond-2",),
        ("cond-3",),
    ]
    ch = _FakeClickHouse([rows])

    result = await check_phantom_edge_density(ch, _NOW, lookback_days=10)

    assert result.alerts_in_final_hour == 4
    assert result.alerts_per_day == pytest.approx(0.4)
    # Distinct, in first-seen order.
    assert result.sample_condition_ids == ["cond-1", "cond-2", "cond-3"]


@pytest.mark.asyncio
async def test_phantom_edge_density_passes_window_seconds() -> None:
    ch = _FakeClickHouse([[]])

    await check_phantom_edge_density(ch, _NOW, lookback_days=30, window_hours=2)

    _, params = ch.calls[0]
    assert params is not None
    assert params["window_s"] == 2 * 3600


@pytest.mark.asyncio
async def test_phantom_edge_density_rejects_non_positive_lookback() -> None:
    ch = _FakeClickHouse([[]])
    with pytest.raises(ValueError):
        await check_phantom_edge_density(ch, _NOW, lookback_days=0)


@pytest.mark.asyncio
async def test_phantom_edge_density_query_failure_returns_zero() -> None:
    class _ExplodingClient:
        async def query(self, *args: Any, **kwargs: Any) -> _QueryResult:
            raise RuntimeError("boom")

    result = await check_phantom_edge_density(
        _ExplodingClient(), _NOW, lookback_days=7
    )

    assert result == PhantomEdgeResult(
        alerts_in_final_hour=0,
        alerts_per_day=0.0,
        sample_condition_ids=[],
    )


# ---------------------------------------------------------------------------
# run_eol_fp_retrospective
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fp_retrospective_classifies_directional_alerts() -> None:
    # 3 TP, 7 FP → fp_rate = 0.7. min_samples=10 default; classified=10 → reported.
    rows = (
        [("c", "yes", "yes")] * 3
        + [("c", "yes", "no")] * 7
    )
    ch = _FakeClickHouse([rows])

    result = await run_eol_fp_retrospective(ch, _NOW, lookback_days=14)

    assert result.alerts_classified == 10
    assert result.true_positives == 3
    assert result.false_positives == 7
    assert result.fp_rate == pytest.approx(0.7)
    # 0.7 > 0.15 target → fails the gate.
    assert result.meets_target is False


@pytest.mark.asyncio
async def test_fp_retrospective_meets_target_below_threshold() -> None:
    # 11 TP, 1 FP → fp_rate ≈ 0.083 < 0.15 target.
    rows = (
        [("c", "yes", "yes")] * 11
        + [("c", "no", "yes")] * 1
    )
    ch = _FakeClickHouse([rows])

    result = await run_eol_fp_retrospective(ch, _NOW, lookback_days=14)

    assert result.fp_rate is not None
    assert result.fp_rate < 0.15
    assert result.meets_target is True


@pytest.mark.asyncio
async def test_fp_retrospective_below_min_samples_returns_none_rate() -> None:
    rows = [("c", "yes", "yes"), ("c", "yes", "no")]
    ch = _FakeClickHouse([rows])

    result = await run_eol_fp_retrospective(
        ch, _NOW, lookback_days=14, min_samples=10
    )

    assert result.alerts_classified == 2
    assert result.fp_rate is None
    assert result.meets_target is False


@pytest.mark.asyncio
async def test_fp_retrospective_normalizes_case() -> None:
    rows = [("c", "YES", "Yes"), ("c", "No", "NO")]
    ch = _FakeClickHouse([rows])

    result = await run_eol_fp_retrospective(
        ch, _NOW, lookback_days=14, min_samples=2
    )

    assert result.true_positives == 2
    assert result.false_positives == 0
    assert result.fp_rate == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_fp_retrospective_rejects_non_positive_lookback() -> None:
    ch = _FakeClickHouse([[]])
    with pytest.raises(ValueError):
        await run_eol_fp_retrospective(ch, _NOW, lookback_days=-1)


# ---------------------------------------------------------------------------
# run_eol_monitor composite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_eol_monitor_calls_all_three_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ramp = RampUpResult(
        markets_checked=10,
        markets_in_ramp=2,
        markets_in_phantom_window=1,
        mean_convergence_weight=0.5,
        max_convergence_weight=0.8,
        sample_market_ids=["c1"],
    )
    fake_phantom = PhantomEdgeResult(
        alerts_in_final_hour=3,
        alerts_per_day=0.1,
        sample_condition_ids=["c1"],
    )
    fake_fp = LastHourFpResult(
        alerts_classified=12,
        true_positives=10,
        false_positives=2,
        skipped_unresolved=0,
        fp_rate=0.1666,
        meets_target=False,
    )

    async def _fake_ramp(_ch: object, _asof: datetime) -> RampUpResult:
        return fake_ramp

    async def _fake_phantom(
        _ch: object, _asof: datetime, *, lookback_days: int
    ) -> PhantomEdgeResult:
        return fake_phantom

    async def _fake_fp(
        _ch: object, _asof: datetime, *, lookback_days: int
    ) -> LastHourFpResult:
        return fake_fp

    monkeypatch.setattr(eol_monitor, "check_ramp_up_on_live_markets", _fake_ramp)
    monkeypatch.setattr(eol_monitor, "check_phantom_edge_density", _fake_phantom)
    monkeypatch.setattr(eol_monitor, "run_eol_fp_retrospective", _fake_fp)

    report = await run_eol_monitor(object(), asked_at=_NOW, lookback_days=14)

    assert isinstance(report, EolMonitorReport)
    assert report.ramp_up is fake_ramp
    assert report.phantom_edge is fake_phantom
    assert report.last_hour_fp is fake_fp
    assert report.lookback_days == 14
    assert report.as_of == _NOW
