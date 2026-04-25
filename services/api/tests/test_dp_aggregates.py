from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest
from api.dp_aggregates import (
    DpAggregateRow,
    ResolvedLabelCall,
    build_dp_aggregate_rows,
)


def test_build_dp_aggregate_rows_applies_thresholds_and_noise() -> None:
    calls = [
        ResolvedLabelCall(
            user_id=f"user-{idx % 3}",
            outcome="YES",
            model_prob_at_call=0.74,
            resolved_outcome="YES" if idx < 4 else "NO",
            created_at=datetime(2026, 4, 20, tzinfo=UTC),
        )
        for idx in range(6)
    ] + [
        ResolvedLabelCall(
            user_id="user-9",
            outcome="YES",
            model_prob_at_call=0.56,
            resolved_outcome="YES",
            created_at=datetime(2026, 4, 20, tzinfo=UTC),
        )
    ]

    rows = build_dp_aggregate_rows(
        calls,
        epsilon=1.0,
        min_users=2,
        min_calls=3,
        lookback_start=datetime(2026, 4, 1, tzinfo=UTC),
        lookback_end=datetime(2026, 4, 24, tzinfo=UTC),
        observed_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        rng=random.Random(7),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.bucket_label == "70-80%"
    assert row.source_user_count == 3
    assert row.raw_call_count == 6
    assert row.raw_hit_rate == pytest.approx(4 / 6)
    assert row.noisy_call_count >= 0
    assert 0 <= row.noisy_hit_rate <= 1


def test_dp_aggregate_row_does_not_expose_individual_call_fields() -> None:
    row = DpAggregateRow(
        cohort_kind="confidence_bucket",
        bucket_label="70-80%",
        source_user_count=5,
        raw_call_count=21,
        noisy_call_count=20.3,
        raw_hit_rate=0.57,
        noisy_hit_rate=0.54,
        epsilon=1.0,
        min_users=5,
        min_calls=20,
        lookback_start=datetime(2026, 1, 1, tzinfo=UTC),
        lookback_end=datetime(2026, 4, 24, tzinfo=UTC),
        observed_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
    )

    public_keys = set(row.__dict__.keys())
    assert "user_id" not in public_keys
    assert "condition_id" not in public_keys
    assert "call_id" not in public_keys
