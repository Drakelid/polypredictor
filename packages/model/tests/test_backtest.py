from __future__ import annotations

import math

import pytest
from model import (
    MarketType,
    WalkForwardSample,
    expected_calibration_error,
    kl_divergence,
    population_stability_index,
    walk_forward_evaluate,
)


def _sample(
    *,
    p: float,
    y: int,
    type_: MarketType = MarketType.THRESHOLD,
    ttr_s: float = 3_600.0,
    band: tuple[float, float] | None = None,
    regime: str | None = None,
    asked_at: float = 0.0,
) -> WalkForwardSample:
    band_lo, band_hi = (band[0], band[1]) if band is not None else (None, None)
    return WalkForwardSample(
        market_type=type_,
        asked_at=asked_at,
        resolved_at=asked_at + ttr_s,
        predicted_prob=p,
        outcome=y,
        time_to_resolution_s=ttr_s,
        band_lo=band_lo,
        band_hi=band_hi,
        regime=regime,
    )


def test_walk_forward_evaluate_empty_returns_zeroed_report() -> None:
    report = walk_forward_evaluate([])
    assert report.strata == []
    assert report.by_type == []
    assert report.by_regime == []
    assert report.overall.sample_count == 0
    assert report.overall.brier == pytest.approx(0.0)


def test_walk_forward_brier_computes_mean_squared_error() -> None:
    report = walk_forward_evaluate(
        [_sample(p=0.7, y=1), _sample(p=0.4, y=0), _sample(p=0.9, y=0)]
    )
    expected = ((0.7 - 1) ** 2 + (0.4 - 0) ** 2 + (0.9 - 0) ** 2) / 3
    assert report.overall.brier == pytest.approx(expected)


def test_walk_forward_stratifies_by_market_type_and_ttr_bucket() -> None:
    samples = [
        _sample(p=0.6, y=1, type_=MarketType.THRESHOLD, ttr_s=3_600.0),     # lt_1d
        _sample(p=0.6, y=1, type_=MarketType.THRESHOLD, ttr_s=2 * 86_400),  # 1d_7d
        _sample(p=0.4, y=0, type_=MarketType.DISCRETE_EVENT, ttr_s=10 * 86_400),  # 7d_30d
        _sample(p=0.5, y=1, type_=MarketType.DISCRETE_EVENT, ttr_s=60 * 86_400),  # gt_30d
    ]
    report = walk_forward_evaluate(samples)
    keys = {(s.market_type, s.ttr_bucket) for s in report.strata}
    assert keys == {
        ("threshold", "lt_1d"),
        ("threshold", "1d_7d"),
        ("discrete_event", "7d_30d"),
        ("discrete_event", "gt_30d"),
    }
    by_type = {r.market_type: r for r in report.by_type}
    assert by_type["threshold"].sample_count == 2
    assert by_type["discrete_event"].sample_count == 2


def test_walk_forward_coverage_only_counts_banded_samples() -> None:
    samples = [
        _sample(p=0.5, y=1, band=(0.0, 1.0)),  # covers
        _sample(p=0.5, y=0, band=(0.0, 1.0)),  # covers
        _sample(p=0.5, y=1, band=(0.6, 0.9)),  # outcome=1 outside [0.6, 0.9] — miss
        _sample(p=0.5, y=1),  # no band — excluded from coverage
    ]
    report = walk_forward_evaluate(samples)
    # 3 banded samples, 2 hit → coverage = 2/3
    assert report.overall.coverage == pytest.approx(2 / 3)


def test_walk_forward_groups_by_regime() -> None:
    samples = [
        _sample(p=0.6, y=1, regime="bull"),
        _sample(p=0.4, y=0, regime="bull"),
        _sample(p=0.5, y=1, regime="chop"),
    ]
    report = walk_forward_evaluate(samples)
    by_regime = {r.regime: r for r in report.by_regime}
    assert set(by_regime.keys()) == {"bull", "chop"}
    assert by_regime["bull"].sample_count == 2
    assert by_regime["chop"].sample_count == 1


def test_expected_calibration_error_perfect_calibration_is_zero() -> None:
    # 50% predictions paired with a 50/50 outcome split → ECE 0.
    samples = [_sample(p=0.5, y=1), _sample(p=0.5, y=0)]
    assert expected_calibration_error(samples) == pytest.approx(0.0)


def test_expected_calibration_error_miscalibrated_predictions_are_positive() -> None:
    # 90% predictions but 0% realized — large ECE.
    samples = [_sample(p=0.9, y=0) for _ in range(10)]
    ece = expected_calibration_error(samples)
    assert ece > 0.85


def test_psi_zero_for_identical_distributions() -> None:
    rng = list(range(100))
    psi = population_stability_index(rng, list(rng))
    assert psi == pytest.approx(0.0, abs=1e-9)


def test_psi_grows_when_current_drifts_away_from_reference() -> None:
    reference = [float(x) for x in range(100)]
    same = list(reference)
    drifted = [value + 50 for value in reference]
    assert population_stability_index(reference, same) < population_stability_index(
        reference, drifted
    )


def test_psi_returns_zero_for_empty_inputs() -> None:
    assert population_stability_index([], [1.0, 2.0]) == pytest.approx(0.0)
    assert population_stability_index([1.0, 2.0], []) == pytest.approx(0.0)


def test_kl_divergence_zero_for_identical_distributions() -> None:
    reference = [float(x) for x in range(50)]
    assert kl_divergence(reference, list(reference)) == pytest.approx(0.0, abs=1e-9)


def test_kl_divergence_finite_when_buckets_go_empty() -> None:
    # Current concentrates on a sub-range of reference — some reference
    # buckets are empty in current, but epsilon-floor keeps KL finite.
    reference = [float(x) for x in range(100)]
    current = [float(x) for x in range(10)]
    value = kl_divergence(reference, current)
    assert math.isfinite(value)
    assert value > 0
