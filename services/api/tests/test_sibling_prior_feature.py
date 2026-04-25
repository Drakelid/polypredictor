from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.markets import _ensemble_sample_for_row
from model import (
    BaselineOutput,
    BaselineSource,
    ClassificationResult,
    MarketFeatures,
    MarketType,
    PipelineResult,
    SiblingPrior,
)


def test_ensemble_sample_threads_sibling_implied_prior() -> None:
    classification = ClassificationResult(
        market_type=MarketType.THRESHOLD,
        confidence=0.9,
        features=MarketFeatures(asset="BTC", strike=150_000, direction="above"),
        reasons=["threshold"],
    )
    sample = _ensemble_sample_for_row(
        classification=classification,
        pipeline_result=PipelineResult(
            classification=classification,
            baseline=BaselineOutput(
                probability=0.41,
                source=BaselineSource.BS_ONE_TOUCH,
                reasons=["baseline"],
                uncertainty_multiplier=1.0,
            ),
            displayed_probability=0.41,
            displayed_source=BaselineSource.BS_ONE_TOUCH,
        ),
        market_mid=0.39,
        sibling_prior=SiblingPrior(
            lower_bound=0.28,
            upper_bound=0.55,
            implied_prior=0.415,
            support_conditions=("looser", "stricter"),
        ),
        feature_snapshot=type(
            "FeatureSnapshot",
            (),
            {
                "spread": 0.02,
                "book_imbalance_1pct": 0.1,
                "book_imbalance_5pct": 0.2,
                "momentum_1h": 0.01,
                "momentum_24h": 0.02,
                "momentum_7d": 0.03,
                "realized_vol_24h": 0.5,
                "informed_taker_flow_24h": 0.0,
                "passive_maker_flow_24h": 0.0,
                "decayed_directional_flow_24h": 0.0,
                "observed_at": datetime(2026, 4, 23, tzinfo=UTC),
            },
        )(),
        smart_money=None,
        concentration=None,
        concentration_threshold=0.6,
        asked_at=datetime(2026, 4, 23, tzinfo=UTC),
    )

    assert sample is not None
    assert sample.sibling_implied_prior == pytest.approx(0.415)
