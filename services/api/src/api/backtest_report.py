"""Backtest report surface for the dashboard (M6.4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean

from clickhouse_connect.driver.asyncclient import AsyncClient

from .backtest_walk_forward import CorpusSummary, replay_walk_forward_samples
from .tuning import TuningProfile


@dataclass(frozen=True)
class CalibrationPoint:
    bucket_mid: float
    avg_predicted: float
    hit_rate: float
    count: int


@dataclass(frozen=True)
class BacktestDrilldownRow:
    condition_id: str
    market_type: str
    regime: str | None
    asked_at: datetime
    resolved_at: datetime
    predicted_prob: float
    outcome: int
    brier_contribution: float
    time_to_resolution_s: float
    band_lo: float | None
    band_hi: float | None
    band_hit: bool | None


@dataclass(frozen=True)
class BacktestUiReport:
    lookback_days: int
    horizon_hours: int
    total_samples: int
    calibration_points: list[CalibrationPoint]
    rows: list[BacktestDrilldownRow]
    corpus: CorpusSummary
    tuning_comparison: BacktestTuningComparison | None


@dataclass(frozen=True)
class BacktestTuningComparison:
    profile_name: str
    profile_preset: str
    total_samples: int
    default_brier: float
    tuned_brier: float
    brier_delta: float
    default_calibration_points: list[CalibrationPoint]
    tuned_calibration_points: list[CalibrationPoint]


def calibration_points_for_rows(
    rows: list[BacktestDrilldownRow],
    *,
    bins: int = 10,
) -> list[CalibrationPoint]:
    bucket_predictions: list[list[float]] = [[] for _ in range(bins)]
    bucket_outcomes: list[list[int]] = [[] for _ in range(bins)]
    for row in rows:
        clipped = min(max(row.predicted_prob, 0.0), 1.0 - 1e-9)
        idx = min(int(clipped * bins), bins - 1)
        bucket_predictions[idx].append(clipped)
        bucket_outcomes[idx].append(row.outcome)
    points: list[CalibrationPoint] = []
    for idx, (predictions, outcomes) in enumerate(
        zip(bucket_predictions, bucket_outcomes, strict=True)
    ):
        if not predictions:
            continue
        points.append(
            CalibrationPoint(
                bucket_mid=(idx + 0.5) / bins,
                avg_predicted=sum(predictions) / len(predictions),
                hit_rate=sum(outcomes) / len(outcomes),
                count=len(predictions),
            )
        )
    return points


async def build_backtest_ui_report(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_days: int = 90,
    horizon_hours: int = 24,
    limit: int = 500,
    low_volume_threshold_usdc: float = 1_000.0,
    tuning_profile: TuningProfile | None = None,
) -> BacktestUiReport:
    replay_rows, corpus, _ = await replay_walk_forward_samples(
        ch,
        horizons_hours=[horizon_hours],
        resolved_from=asked_at - timedelta(days=lookback_days),
        resolved_to=asked_at,
        limit=limit,
        low_volume_threshold_usdc=low_volume_threshold_usdc,
    )
    rows = sorted(
        [
            BacktestDrilldownRow(
                condition_id=row.condition_id,
                market_type=row.market_type.value,
                regime=row.regime,
                asked_at=row.asked_at,
                resolved_at=row.resolved_at,
                predicted_prob=row.predicted_prob,
                outcome=row.outcome,
                brier_contribution=(row.predicted_prob - row.outcome) ** 2,
                time_to_resolution_s=row.time_to_resolution_s,
                band_lo=row.band_lo,
                band_hi=row.band_hi,
                band_hit=(
                    row.band_lo <= float(row.outcome) <= row.band_hi
                    if row.band_lo is not None and row.band_hi is not None
                    else None
                ),
            )
            for row in replay_rows
        ],
        key=lambda row: row.resolved_at,
        reverse=True,
    )
    tuning_comparison = None
    if tuning_profile is not None:
        tuned_replay_rows, _, _ = await replay_walk_forward_samples(
            ch,
            horizons_hours=[horizon_hours],
            resolved_from=asked_at - timedelta(days=lookback_days),
            resolved_to=asked_at,
            limit=limit,
            low_volume_threshold_usdc=low_volume_threshold_usdc,
            tuning_profile=tuning_profile,
        )
        default_by_key = {
            (row.condition_id, row.asked_at, row.resolved_at): row
            for row in rows
        }
        tuned_rows = sorted(
            [
                BacktestDrilldownRow(
                    condition_id=row.condition_id,
                    market_type=row.market_type.value,
                    regime=row.regime,
                    asked_at=row.asked_at,
                    resolved_at=row.resolved_at,
                    predicted_prob=row.predicted_prob,
                    outcome=row.outcome,
                    brier_contribution=(row.predicted_prob - row.outcome) ** 2,
                    time_to_resolution_s=row.time_to_resolution_s,
                    band_lo=row.band_lo,
                    band_hi=row.band_hi,
                    band_hit=(
                        row.band_lo <= float(row.outcome) <= row.band_hi
                        if row.band_lo is not None and row.band_hi is not None
                        else None
                    ),
                )
                for row in tuned_replay_rows
                if (row.condition_id, row.asked_at, row.resolved_at) in default_by_key
            ],
            key=lambda row: row.resolved_at,
            reverse=True,
        )
        default_rows = [
            default_by_key[(row.condition_id, row.asked_at, row.resolved_at)]
            for row in tuned_rows
        ]
        if default_rows and tuned_rows:
            default_brier = mean(row.brier_contribution for row in default_rows)
            tuned_brier = mean(row.brier_contribution for row in tuned_rows)
            tuning_comparison = BacktestTuningComparison(
                profile_name=tuning_profile.name,
                profile_preset=tuning_profile.preset,
                total_samples=len(tuned_rows),
                default_brier=default_brier,
                tuned_brier=tuned_brier,
                brier_delta=tuned_brier - default_brier,
                default_calibration_points=calibration_points_for_rows(default_rows),
                tuned_calibration_points=calibration_points_for_rows(tuned_rows),
            )
    return BacktestUiReport(
        lookback_days=lookback_days,
        horizon_hours=horizon_hours,
        total_samples=len(rows),
        calibration_points=calibration_points_for_rows(rows),
        rows=rows,
        corpus=corpus,
        tuning_comparison=tuning_comparison,
    )
