from __future__ import annotations

import json

import pytest
from model import (
    EnsembleRegistry,
    EnsembleSample,
    GradientBoostStump,
    MarketType,
    fit_per_type_ensembles,
)


def _sample(
    *,
    outcome: int,
    asked_at: float,
    p_base: float = 0.5,
    market_mid: float = 0.5,
    book_imbalance_1pct: float = 0.0,
    book_imbalance_5pct: float = 0.0,
) -> EnsembleSample:
    return EnsembleSample(
        market_type=MarketType.THRESHOLD,
        outcome=outcome,
        asked_at=asked_at,
        p_base=p_base,
        market_mid=market_mid,
        spread=0.02,
        book_imbalance_1pct=book_imbalance_1pct,
        book_imbalance_5pct=book_imbalance_5pct,
        momentum_1h=book_imbalance_1pct,
        momentum_24h=book_imbalance_5pct,
        momentum_7d=book_imbalance_1pct,
        realized_vol_24h=0.4,
        informed_taker_flow_24h=book_imbalance_1pct,
        passive_maker_flow_24h=book_imbalance_5pct,
        decayed_directional_flow_24h=book_imbalance_1pct,
    )


def _fit_registry() -> EnsembleRegistry:
    samples: list[EnsembleSample] = []
    for idx in range(48):
        imbalance = -0.6 + idx * (1.2 / 47.0)
        samples.append(
            _sample(
                outcome=1 if imbalance > 0 else 0,
                asked_at=float(idx),
                p_base=0.5,
                market_mid=0.5,
                book_imbalance_1pct=imbalance,
                book_imbalance_5pct=imbalance * 0.8,
            )
        )
    return fit_per_type_ensembles(
        samples,
        n_estimators=10,
        learning_rate=0.3,
        min_samples_leaf=4,
        min_calibration_size=8,
    )


def test_gradient_boost_stump_records_left_fraction_at_fit_time() -> None:
    registry = _fit_registry()
    model = registry.model_for_type(MarketType.THRESHOLD)
    assert model is not None
    assert model.stumps
    for stump in model.stumps:
        # Every fitted stump now carries a non-default fraction; the floor /
        # ceiling come from the min_samples_leaf constraint in _fit_best_stump.
        assert 0.0 < stump.left_fraction < 1.0
        assert stump.left_fraction != 0.5  # extremely unlikely on real fits


def test_stump_shap_subtracts_expected_contribution() -> None:
    stump = GradientBoostStump(
        feature_name="book_imbalance_1pct",
        threshold=0.0,
        left_value=-0.20,
        right_value=0.30,
        left_fraction=0.6,
    )
    expected = 0.6 * -0.20 + 0.4 * 0.30
    assert stump.expected_contribution == pytest.approx(expected)
    assert stump.shap_contribution(-0.5) == pytest.approx(-0.20 - expected)
    assert stump.shap_contribution(0.5) == pytest.approx(0.30 - expected)


def test_stump_to_dict_round_trips_left_fraction() -> None:
    stump = GradientBoostStump(
        feature_name="x",
        threshold=0.5,
        left_value=-0.1,
        right_value=0.2,
        left_fraction=0.7,
    )
    payload = stump.to_dict()
    assert payload["left_fraction"] == pytest.approx(0.7)
    revived = GradientBoostStump.from_dict(payload)
    assert revived.left_fraction == pytest.approx(0.7)


def test_legacy_stump_payload_defaults_to_balanced_left_fraction() -> None:
    """Registries serialized before left_fraction was added must still
    deserialize so existing serve paths don't break."""
    legacy = {
        "feature_name": "book_imbalance_1pct",
        "threshold": 0.5,
        "left_value": -0.1,
        "right_value": 0.2,
    }
    revived = GradientBoostStump.from_dict(legacy)
    assert revived.left_fraction == pytest.approx(0.5)
    # Balanced assumption -> SHAP collapses to (contribution - midpoint).
    assert revived.shap_contribution(0.0) == pytest.approx(-0.1 - 0.05)


def test_explain_prediction_shap_sums_to_score_minus_expected() -> None:
    """Per-feature SHAP values from the booster sum to ``raw_score - E[raw_score]``.

    With the linear stack mean-zero by construction, the expected raw score
    collapses to ``intercept + sum(stump.expected_contribution)``.
    """
    registry = _fit_registry()
    model = registry.model_for_type(MarketType.THRESHOLD)
    assert model is not None

    probe = _sample(
        outcome=1,
        asked_at=999.0,
        p_base=0.5,
        market_mid=0.5,
        book_imbalance_1pct=0.4,
        book_imbalance_5pct=0.3,
    )
    explanation = model.explain_prediction_shap(probe)
    expected_score = model.linear_intercept + sum(
        stump.expected_contribution for stump in model.stumps
    )
    sum_shap = sum(item.score_contribution for item in explanation.contributions)
    assert sum_shap == pytest.approx(explanation.raw_score - expected_score, abs=1e-9)


def test_registry_explain_prediction_shap_falls_back_when_type_unknown() -> None:
    registry = _fit_registry()
    other = _sample(outcome=0, asked_at=0.0)
    # MarketType.MISC was never trained -> registry has no model -> None.
    other = EnsembleSample(
        market_type=MarketType.MISC,
        outcome=0,
        asked_at=0.0,
        p_base=0.5,
        market_mid=0.5,
        spread=0.02,
        book_imbalance_1pct=0.0,
        book_imbalance_5pct=0.0,
    )
    assert registry.explain_prediction_shap(other) is None


def test_registry_serialization_round_trips_shap() -> None:
    registry = _fit_registry()
    payload = registry.to_json()
    revived = EnsembleRegistry.from_json(payload)
    probe = _sample(
        outcome=1,
        asked_at=10.0,
        p_base=0.5,
        market_mid=0.5,
        book_imbalance_1pct=0.4,
        book_imbalance_5pct=0.3,
    )
    before = registry.explain_prediction_shap(probe)
    after = revived.explain_prediction_shap(probe)
    assert before is not None and after is not None
    before_map = {c.feature_name: c.score_contribution for c in before.contributions}
    after_map = {c.feature_name: c.score_contribution for c in after.contributions}
    assert set(before_map) == set(after_map)
    for name, score in before_map.items():
        assert after_map[name] == pytest.approx(score)


def test_explain_prediction_unchanged_for_legacy_callers() -> None:
    """The non-SHAP `explain_prediction` keeps its original semantics — raw
    contributions vs zero — so dashboards reading the old field don't drift."""
    registry = _fit_registry()
    model = registry.model_for_type(MarketType.THRESHOLD)
    assert model is not None
    probe = _sample(
        outcome=1,
        asked_at=10.0,
        p_base=0.5,
        market_mid=0.5,
        book_imbalance_1pct=0.4,
        book_imbalance_5pct=0.3,
    )
    raw = model.explain_prediction(probe)
    # Sum of raw contributions equals raw_score (intercept rolled in).
    sum_raw = sum(item.score_contribution for item in raw.contributions)
    assert sum_raw == pytest.approx(raw.raw_score, abs=1e-9)


def test_legacy_serialized_registry_loads_with_default_left_fraction() -> None:
    """Drop a known-good legacy payload (no left_fraction) into from_json
    and verify SHAP falls back to the balanced assumption gracefully."""
    payload = {
        "models": {
            "threshold": {
                "market_type": "threshold",
                "linear_means": {},
                "linear_scales": {},
                "booster_means": {"book_imbalance_1pct": 0.0},
                "booster_scales": {"book_imbalance_1pct": 1.0},
                "linear_intercept": 0.0,
                "linear_weights": {},
                "stumps": [
                    {
                        "feature_name": "book_imbalance_1pct",
                        "threshold": 0.0,
                        "left_value": -0.1,
                        "right_value": 0.2,
                    }
                ],
                "calibrator": {"upper_bounds": [1.0], "values": [0.5]},
                "market_mid_weight_cap": 0.35,
            }
        }
    }
    registry = EnsembleRegistry.from_json(json.dumps(payload))
    sample = EnsembleSample(
        market_type=MarketType.THRESHOLD,
        outcome=1,
        asked_at=0.0,
        p_base=0.5,
        market_mid=0.5,
        spread=0.02,
        book_imbalance_1pct=0.5,
    )
    explanation = registry.explain_prediction_shap(sample)
    assert explanation is not None
    # Balanced assumption: expected = 0.5 * -0.1 + 0.5 * 0.2 = 0.05; at
    # value 0.5 we hit the right leaf, so SHAP = 0.2 - 0.05 = 0.15.
    booster = next(
        c for c in explanation.contributions if c.feature_name == "book_imbalance_1pct"
    )
    assert booster.score_contribution == pytest.approx(0.15)
