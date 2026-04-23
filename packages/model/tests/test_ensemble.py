from __future__ import annotations

import pytest
from model import (
    EnsembleRegistry,
    EnsembleSample,
    IsotonicCalibrator,
    MarketType,
    fit_per_type_ensembles,
)


def _sample(
    *,
    market_type: MarketType,
    outcome: int,
    asked_at: float,
    p_base: float = 0.5,
    market_mid: float | None = 0.5,
    spread: float | None = 0.02,
    book_imbalance_1pct: float | None = None,
    book_imbalance_5pct: float | None = None,
) -> EnsembleSample:
    return EnsembleSample(
        market_type=market_type,
        outcome=outcome,
        asked_at=asked_at,
        p_base=p_base,
        market_mid=market_mid,
        spread=spread,
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


def test_isotonic_calibrator_is_monotone() -> None:
    calibrator = IsotonicCalibrator.fit(
        [0.10, 0.20, 0.30, 0.40, 0.50],
        [0, 1, 0, 1, 1],
    )

    outputs = [calibrator.predict(x) for x in [0.05, 0.15, 0.25, 0.35, 0.45, 0.55]]

    assert outputs == sorted(outputs)
    assert all(0.0 <= value <= 1.0 for value in outputs)


def test_fit_per_type_ensembles_returns_separate_models_and_roundtrips() -> None:
    samples: list[EnsembleSample] = []
    for idx in range(30):
        p_base = 0.25 + (idx / 60.0)
        samples.append(
            _sample(
                market_type=MarketType.THRESHOLD,
                outcome=1 if idx >= 15 else 0,
                asked_at=float(idx),
                p_base=p_base,
                market_mid=p_base,
                book_imbalance_1pct=(idx - 15) / 20.0,
                book_imbalance_5pct=(idx - 15) / 25.0,
            )
        )
        samples.append(
            _sample(
                market_type=MarketType.DISCRETE_EVENT,
                outcome=1 if idx % 3 == 0 else 0,
                asked_at=float(idx),
                p_base=0.45,
                market_mid=0.45,
                book_imbalance_1pct=0.1 if idx % 3 == 0 else -0.1,
                book_imbalance_5pct=0.15 if idx % 3 == 0 else -0.05,
            )
        )

    registry = fit_per_type_ensembles(
        samples,
        min_samples_leaf=3,
        min_calibration_size=6,
        n_estimators=10,
    )

    assert registry.model_for_type(MarketType.THRESHOLD) is not None
    assert registry.model_for_type(MarketType.DISCRETE_EVENT) is not None

    probe = _sample(
        market_type=MarketType.THRESHOLD,
        outcome=1,
        asked_at=999.0,
        p_base=0.72,
        market_mid=0.68,
        book_imbalance_1pct=0.3,
        book_imbalance_5pct=0.2,
    )
    before = registry.predict_probability(probe)
    roundtrip = EnsembleRegistry.from_json(registry.to_json())
    after = roundtrip.predict_probability(probe)

    assert before is not None
    assert after == pytest.approx(before)


def test_market_mid_weight_cap_is_respected() -> None:
    samples = [
        _sample(
            market_type=MarketType.THRESHOLD,
            outcome=1 if idx >= 20 else 0,
            asked_at=float(idx),
            p_base=0.5,
            market_mid=0.2 + idx * 0.015,
            book_imbalance_1pct=0.0,
            book_imbalance_5pct=0.0,
        )
        for idx in range(40)
    ]

    registry = fit_per_type_ensembles(
        samples,
        market_mid_weight_cap=0.15,
        n_estimators=0,
        min_calibration_size=5,
    )
    model = registry.model_for_type(MarketType.THRESHOLD)

    assert model is not None
    assert abs(model.linear_weights["market_mid_logit"]) <= 0.150001


def test_boosted_stumps_learn_microstructure_signal_when_base_is_flat() -> None:
    samples = []
    for idx in range(48):
        imbalance = -0.6 + idx * (1.2 / 47.0)
        samples.append(
            _sample(
                market_type=MarketType.THRESHOLD,
                outcome=1 if imbalance > 0 else 0,
                asked_at=float(idx),
                p_base=0.5,
                market_mid=0.5,
                book_imbalance_1pct=imbalance,
                book_imbalance_5pct=imbalance * 0.8,
                spread=0.01,
            )
        )

    registry = fit_per_type_ensembles(
        samples,
        n_estimators=12,
        learning_rate=0.3,
        min_samples_leaf=4,
        min_calibration_size=8,
    )
    model = registry.model_for_type(MarketType.THRESHOLD)

    assert model is not None
    assert model.stumps

    low = model.predict_raw_probability(
        _sample(
            market_type=MarketType.THRESHOLD,
            outcome=0,
            asked_at=100.0,
            p_base=0.5,
            market_mid=0.5,
            book_imbalance_1pct=-0.4,
            book_imbalance_5pct=-0.3,
        )
    )
    high = model.predict_raw_probability(
        _sample(
            market_type=MarketType.THRESHOLD,
            outcome=1,
            asked_at=101.0,
            p_base=0.5,
            market_mid=0.5,
            book_imbalance_1pct=0.4,
            book_imbalance_5pct=0.3,
        )
    )

    assert low is not None and high is not None
    assert high > low


def test_explain_prediction_returns_ranked_feature_contributions() -> None:
    samples = []
    for idx in range(48):
        imbalance = -0.6 + idx * (1.2 / 47.0)
        samples.append(
            _sample(
                market_type=MarketType.THRESHOLD,
                outcome=1 if imbalance > 0 else 0,
                asked_at=float(idx),
                p_base=0.5,
                market_mid=0.5,
                book_imbalance_1pct=imbalance,
                book_imbalance_5pct=imbalance * 0.8,
                spread=0.01,
            )
        )

    registry = fit_per_type_ensembles(
        samples,
        n_estimators=12,
        learning_rate=0.3,
        min_samples_leaf=4,
        min_calibration_size=8,
    )
    probe = _sample(
        market_type=MarketType.THRESHOLD,
        outcome=1,
        asked_at=100.0,
        p_base=0.5,
        market_mid=0.5,
        book_imbalance_1pct=0.4,
        book_imbalance_5pct=0.3,
    )

    explanation = registry.explain_prediction(probe)
    probability = registry.predict_probability(probe)

    assert explanation is not None
    assert probability is not None
    assert explanation.calibrated_probability == pytest.approx(probability)
    assert explanation.contributions
    scores = [abs(item.score_contribution) for item in explanation.contributions]
    assert scores == sorted(scores, reverse=True)
    assert any(item.feature_name == "book_imbalance_1pct" for item in explanation.contributions)
