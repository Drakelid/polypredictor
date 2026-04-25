from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.writers import (
    MODEL_DISABLE_LOG_COLS,
    MODEL_DRIFT_METRICS_COLS,
    model_disable_log_row,
    model_drift_metric_row,
)


def test_model_drift_metric_row_normalizes_regime_and_nullables() -> None:
    observed = datetime(2026, 4, 24, 12, tzinfo=UTC)
    row = model_drift_metric_row(
        window_label="30d",
        market_type="threshold",
        ttr_bucket="1d_7d",
        regime=None,
        sample_count=42,
        brier=0.123,
        ece=0.045,
        coverage=None,
        avg_predicted=0.5,
        avg_outcome=0.55,
        baseline_brier=0.18,
        brier_skill=0.12,
        observed_at=observed,
    )
    by = dict(zip(MODEL_DRIFT_METRICS_COLS, row, strict=True))
    assert by["window_label"] == "30d"
    assert by["regime"] == ""  # None projects to empty LowCardinality
    assert by["coverage"] is None
    assert by["sample_count"] == 42
    assert by["brier"] == pytest.approx(0.123)
    assert by["brier_skill"] == pytest.approx(0.12)
    assert by["event_time"] == observed
    assert by["observed_at"] == observed


def test_model_disable_log_row_validates_action() -> None:
    observed = datetime(2026, 4, 24, 12, tzinfo=UTC)
    row = model_disable_log_row(
        market_type="threshold",
        action="disabled",
        consecutive_days=8,
        threshold_days=7,
        reason="8 consecutive days of negative 30d Brier skill",
        observed_at=observed,
    )
    by = dict(zip(MODEL_DISABLE_LOG_COLS, row, strict=True))
    assert by["action"] == "disabled"
    assert by["consecutive_days"] == 8
    assert by["threshold_days"] == 7

    with pytest.raises(ValueError, match="action must be"):
        model_disable_log_row(
            market_type="threshold",
            action="bogus",
            consecutive_days=1,
            threshold_days=7,
            reason="x",
            observed_at=observed,
        )
