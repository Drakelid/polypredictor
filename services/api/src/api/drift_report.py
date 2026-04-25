"""As-of reads over persisted drift-monitor snapshots (M6.4 / M6.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class ModelDriftMetric:
    window_label: str
    market_type: str
    ttr_bucket: str
    regime: str | None
    sample_count: int
    brier: float
    ece: float
    coverage: float | None
    avg_predicted: float
    avg_outcome: float
    baseline_brier: float | None
    brier_skill: float | None
    observed_at: datetime


@dataclass(frozen=True)
class FeatureDriftMetric:
    feature_name: str
    reference_count: int
    current_count: int
    psi: float
    kl_divergence: float
    psi_threshold: float
    is_alert: bool
    observed_at: datetime


@dataclass(frozen=True)
class DriftSnapshot:
    observed_at: datetime
    model_metrics: list[ModelDriftMetric]
    feature_metrics: list[FeatureDriftMetric]


def _row_to_model_metric(row: tuple[object, ...]) -> ModelDriftMetric:
    regime = str(row[3]).strip() or None
    return ModelDriftMetric(
        window_label=str(row[0]),
        market_type=str(row[1]),
        ttr_bucket=str(row[2]),
        regime=regime,
        sample_count=int(row[4]),
        brier=float(row[5]),
        ece=float(row[6]),
        coverage=float(row[7]) if row[7] is not None else None,
        avg_predicted=float(row[8]),
        avg_outcome=float(row[9]),
        baseline_brier=float(row[10]) if row[10] is not None else None,
        brier_skill=float(row[11]) if row[11] is not None else None,
        observed_at=row[12],  # type: ignore[arg-type]
    )


def _row_to_feature_metric(row: tuple[object, ...]) -> FeatureDriftMetric:
    return FeatureDriftMetric(
        feature_name=str(row[0]),
        reference_count=int(row[1]),
        current_count=int(row[2]),
        psi=float(row[3]),
        kl_divergence=float(row[4]),
        psi_threshold=float(row[5]),
        is_alert=bool(row[6]),
        observed_at=row[7],  # type: ignore[arg-type]
    )


async def latest_drift_snapshot_asof(
    ch: AsyncClient,
    *,
    asked_at: datetime,
) -> DriftSnapshot | None:
    model_observed = await ch.query(
        """
        SELECT max(observed_at)
        FROM model_drift_metrics
        WHERE observed_at <= {asof:DateTime64(3)}
        """,
        parameters={"asof": asked_at},
    )
    model_rows = model_observed.result_rows
    if not model_rows or model_rows[0][0] is None:
        return None
    observed_at = model_rows[0][0]
    model_result = await ch.query(
        """
        SELECT
            window_label,
            market_type,
            ttr_bucket,
            regime,
            sample_count,
            brier,
            ece,
            coverage,
            avg_predicted,
            avg_outcome,
            baseline_brier,
            brier_skill,
            observed_at
        FROM model_drift_metrics
        WHERE observed_at = {observed_at:DateTime64(3)}
        ORDER BY window_label, market_type, ttr_bucket, regime
        """,
        parameters={"observed_at": observed_at},
    )
    feature_result = await ch.query(
        """
        SELECT
            feature_name,
            reference_count,
            current_count,
            psi,
            kl_divergence,
            psi_threshold,
            is_alert,
            observed_at
        FROM feature_drift_metrics
        WHERE observed_at = {observed_at:DateTime64(3)}
        ORDER BY is_alert DESC, psi DESC, feature_name
        """,
        parameters={"observed_at": observed_at},
    )
    return DriftSnapshot(
        observed_at=observed_at,  # type: ignore[arg-type]
        model_metrics=[_row_to_model_metric(row) for row in model_result.result_rows],
        feature_metrics=[_row_to_feature_metric(row) for row in feature_result.result_rows],
    )
