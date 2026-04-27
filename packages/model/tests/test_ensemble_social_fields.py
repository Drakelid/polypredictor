from __future__ import annotations

import json

from model import (
    BOOSTER_FEATURE_NAMES,
    EnsembleRegistry,
    EnsembleSample,
    MarketType,
)


def _sample_with_social(**overrides: float) -> EnsembleSample:
    base = dict(
        market_type=MarketType.THRESHOLD,
        p_base=0.5,
        market_mid=0.5,
        spread=0.02,
        social_post_count_24h=12.0,
        social_reach_volume_24h=540.0,
        social_novelty_score_latest=0.7,
        social_sentiment_dispersion_24h=0.35,
        social_tone_shift_24h_vs_7d=0.25,
    )
    base.update(overrides)
    return EnsembleSample(**base)


def test_ensemble_sample_carries_social_features() -> None:
    sample = _sample_with_social()
    assert sample.social_post_count_24h == 12.0
    assert sample.social_reach_volume_24h == 540.0
    assert sample.social_novelty_score_latest == 0.7
    assert sample.social_sentiment_dispersion_24h == 0.35
    assert sample.social_tone_shift_24h_vs_7d == 0.25


def test_social_feature_names_are_in_booster_feature_names() -> None:
    expected = {
        "social_post_count_24h",
        "social_reach_volume_24h",
        "social_novelty_score_latest",
        "social_sentiment_dispersion_24h",
        "social_tone_shift_24h_vs_7d",
    }
    assert expected.issubset(set(BOOSTER_FEATURE_NAMES))


def test_social_features_default_to_none() -> None:
    """Existing call sites that don't supply social features still work —
    the booster path treats None as the training mean (zero contribution)."""
    sample = EnsembleSample(market_type=MarketType.THRESHOLD, p_base=0.5)
    assert sample.social_post_count_24h is None
    assert sample.social_reach_volume_24h is None
    assert sample.social_novelty_score_latest is None
    assert sample.social_sentiment_dispersion_24h is None
    assert sample.social_tone_shift_24h_vs_7d is None


def test_legacy_registry_with_no_social_stumps_still_predicts() -> None:
    """Registries serialized before social features were added must still
    deserialize and serve predictions on samples that DO carry social
    features. The booster simply trains no stumps for the new names until
    the next retrain run picks them up."""
    payload = {
        "models": {
            "threshold": {
                "market_type": "threshold",
                "linear_means": {},
                "linear_scales": {},
                "booster_means": {"spread": 0.02},
                "booster_scales": {"spread": 0.005},
                "linear_intercept": 0.0,
                "linear_weights": {"p_base_logit": 1.0},
                "stumps": [
                    {
                        "feature_name": "spread",
                        "threshold": 0.0,
                        "left_value": -0.05,
                        "right_value": 0.05,
                        "left_fraction": 0.5,
                    }
                ],
                "calibrator": {"upper_bounds": [1.0], "values": [0.5]},
                "market_mid_weight_cap": 0.35,
            }
        }
    }
    registry = EnsembleRegistry.from_json(json.dumps(payload))
    sample = _sample_with_social()
    prob = registry.predict_probability(sample)
    assert prob is not None
    assert 0.0 <= prob <= 1.0
