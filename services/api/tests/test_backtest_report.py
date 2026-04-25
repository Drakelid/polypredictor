from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api import backtest_report
from api.backtest_report import build_backtest_ui_report, calibration_points_for_rows
from api.backtest_walk_forward import CorpusSummary, ReplaySampleRow
from api.tuning import TuningProfile
from model import MarketType


def test_calibration_points_for_rows_buckets_predictions() -> None:
    rows = [
        backtest_report.BacktestDrilldownRow(
            condition_id="cond-1",
            market_type="threshold",
            regime="bull_trend",
            asked_at=datetime(2026, 4, 20, 12, tzinfo=UTC),
            resolved_at=datetime(2026, 4, 21, 12, tzinfo=UTC),
            predicted_prob=0.21,
            outcome=0,
            brier_contribution=0.0441,
            time_to_resolution_s=86_400.0,
            band_lo=0.1,
            band_hi=0.4,
            band_hit=True,
        ),
        backtest_report.BacktestDrilldownRow(
            condition_id="cond-2",
            market_type="threshold",
            regime="bull_trend",
            asked_at=datetime(2026, 4, 20, 12, tzinfo=UTC),
            resolved_at=datetime(2026, 4, 21, 12, tzinfo=UTC),
            predicted_prob=0.24,
            outcome=1,
            brier_contribution=0.5776,
            time_to_resolution_s=86_400.0,
            band_lo=0.1,
            band_hi=0.4,
            band_hit=False,
        ),
    ]

    points = calibration_points_for_rows(rows)

    assert len(points) == 1
    assert points[0].bucket_mid == pytest.approx(0.25)
    assert points[0].avg_predicted == pytest.approx(0.225)
    assert points[0].hit_rate == pytest.approx(0.5)
    assert points[0].count == 2


@pytest.mark.asyncio
async def test_build_backtest_ui_report_returns_sorted_rows_and_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_replay_walk_forward_samples(
        ch: object,
        *,
        horizons_hours: tuple[int, ...] | list[int],
        resolved_from: datetime | None = None,
        resolved_to: datetime | None = None,
        limit: int = 500,
        low_volume_threshold_usdc: float = 1_000.0,
    ) -> tuple[list[ReplaySampleRow], CorpusSummary, list[object]]:
        assert horizons_hours == [24]
        assert limit == 50
        return (
            [
                ReplaySampleRow(
                    condition_id="cond-older",
                    market_type=MarketType.THRESHOLD,
                    regime="chop",
                    asked_at=datetime(2026, 4, 19, 12, tzinfo=UTC),
                    resolved_at=datetime(2026, 4, 20, 12, tzinfo=UTC),
                    predicted_prob=0.3,
                    outcome=0,
                    time_to_resolution_s=86_400.0,
                    band_lo=0.0,
                    band_hi=0.6,
                ),
                ReplaySampleRow(
                    condition_id="cond-newer",
                    market_type=MarketType.DISCRETE_EVENT,
                    regime="bull_trend",
                    asked_at=datetime(2026, 4, 20, 12, tzinfo=UTC),
                    resolved_at=datetime(2026, 4, 21, 12, tzinfo=UTC),
                    predicted_prob=0.8,
                    outcome=1,
                    time_to_resolution_s=86_400.0,
                    band_lo=0.5,
                    band_hi=1.0,
                ),
            ],
            CorpusSummary(
                total_resolved_markets=2,
                binary_markets=2,
                invalid_markets=0,
                disputed_markets=0,
                low_volume_markets=0,
                missing_snapshot_markets=0,
            ),
            [],
        )

    monkeypatch.setattr(
        backtest_report,
        "replay_walk_forward_samples",
        _fake_replay_walk_forward_samples,
    )

    report = await build_backtest_ui_report(
        object(),
        asked_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        lookback_days=90,
        horizon_hours=24,
        limit=50,
    )

    assert report.total_samples == 2
    assert report.rows[0].condition_id == "cond-newer"
    assert report.rows[1].condition_id == "cond-older"
    assert report.rows[0].band_hit is True
    assert report.rows[1].band_hit is True
    assert {row.market_type for row in report.rows} == {"discrete_event", "threshold"}
    assert report.calibration_points


@pytest.mark.asyncio
async def test_build_backtest_ui_report_includes_tuning_counterfactual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_replay_walk_forward_samples(
        ch: object,
        *,
        horizons_hours: tuple[int, ...] | list[int],
        resolved_from: datetime | None = None,
        resolved_to: datetime | None = None,
        limit: int = 500,
        low_volume_threshold_usdc: float = 1_000.0,
        tuning_profile: TuningProfile | None = None,
    ) -> tuple[list[ReplaySampleRow], CorpusSummary, list[object]]:
        del ch, resolved_from, resolved_to, low_volume_threshold_usdc
        assert horizons_hours == [24]
        assert limit == 50
        probability = 0.70 if tuning_profile is None else 0.80
        return (
            [
                ReplaySampleRow(
                    condition_id="cond-1",
                    market_type=MarketType.THRESHOLD,
                    regime="bull_trend",
                    asked_at=datetime(2026, 4, 20, 12, tzinfo=UTC),
                    resolved_at=datetime(2026, 4, 21, 12, tzinfo=UTC),
                    predicted_prob=probability,
                    outcome=1,
                    time_to_resolution_s=86_400.0,
                    band_lo=0.5,
                    band_hi=1.0,
                ),
            ],
            CorpusSummary(
                total_resolved_markets=1,
                binary_markets=1,
                invalid_markets=0,
                disputed_markets=0,
                low_volume_markets=0,
                missing_snapshot_markets=0,
            ),
            [],
        )

    monkeypatch.setattr(
        backtest_report,
        "replay_walk_forward_samples",
        _fake_replay_walk_forward_samples,
    )

    report = await build_backtest_ui_report(
        object(),
        asked_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        lookback_days=90,
        horizon_hours=24,
        limit=50,
        tuning_profile=TuningProfile(
            name="Aggressive",
            preset="aggressive",
            log_odds_shifts={},
            is_active=True,
            updated_at=None,
        ),
    )

    assert report.tuning_comparison is not None
    assert report.tuning_comparison.profile_name == "Aggressive"
    assert report.tuning_comparison.default_brier == pytest.approx(0.09)
    assert report.tuning_comparison.tuned_brier == pytest.approx(0.04)
    assert report.tuning_comparison.brier_delta == pytest.approx(-0.05)
