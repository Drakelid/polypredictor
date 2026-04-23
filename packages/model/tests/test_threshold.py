"""Threshold baseline tests — Black-Scholes sanity checks."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from model import (
    BaselineInputs,
    BaselineSource,
    MarketFeatures,
    MarketType,
    classify,
    threshold_baseline,
)


def _asked_at() -> float:
    return datetime(2026, 4, 22, tzinfo=UTC).timestamp()


def _inputs(
    *,
    spot: float | None,
    strike: float,
    direction: str,
    days_to_resolution: float,
    iv: float | None = 0.6,
    iv_strike: float | None = None,
    realized_vol: float | None = None,
) -> BaselineInputs:
    resolution = datetime(2026, 4, 22, tzinfo=UTC) + timedelta(days=days_to_resolution)
    features = MarketFeatures(
        asset="BTC",
        strike=strike,
        direction=direction,
        resolution_date=resolution,
    )
    return BaselineInputs(
        features=features,
        asked_at=_asked_at(),
        spot=spot,
        realized_vol=realized_vol,
        implied_vol_atm=iv,
        implied_vol_strike=iv_strike,
    )


def test_one_touch_above_at_the_money_long_horizon_is_near_one() -> None:
    # With spot near the strike and high vol + long horizon, touch probability
    # should approach 1.0. This is a sanity check that the formula isn't
    # dropping a term or swallowing a sign.
    inp = _inputs(spot=100_000, strike=100_001, direction="above", days_to_resolution=365, iv=0.6)
    out = threshold_baseline(inp, mode="one_touch")
    assert out.source is BaselineSource.BS_ONE_TOUCH
    assert out.probability is not None
    assert out.probability > 0.95


def test_one_touch_already_breached_returns_one() -> None:
    inp = _inputs(spot=160_000, strike=150_000, direction="above", days_to_resolution=30, iv=0.5)
    out = threshold_baseline(inp, mode="one_touch")
    assert out.probability == 1.0


def test_one_touch_far_above_strike_low_vol_is_small() -> None:
    # spot $60k, strike $150k, 10% IV, 30 days — probability of touching
    # should be essentially zero (roughly 10-sigma move).
    inp = _inputs(spot=60_000, strike=150_000, direction="above", days_to_resolution=30, iv=0.1)
    out = threshold_baseline(inp, mode="one_touch")
    assert out.probability is not None
    assert out.probability < 0.001


def test_terminal_atm_60_vol_one_year_near_half() -> None:
    # Risk-neutral terminal probability for ATM on GBM with sigma=0.6 and
    # T=1 is N(d2) where d2 = -0.3. P ≈ 0.382. We assert a tight band.
    inp = _inputs(spot=100_000, strike=100_000, direction="above", days_to_resolution=365, iv=0.6)
    out = threshold_baseline(inp, mode="terminal")
    assert out.source is BaselineSource.BS_TERMINAL
    assert out.probability is not None
    assert out.probability == pytest.approx(0.382, abs=0.01)


def test_terminal_above_and_below_sum_to_one_at_strike() -> None:
    above_inp = _inputs(spot=100_000, strike=120_000, direction="above", days_to_resolution=90, iv=0.5)
    below_inp = _inputs(spot=100_000, strike=120_000, direction="below", days_to_resolution=90, iv=0.5)
    above = threshold_baseline(above_inp, mode="terminal")
    below = threshold_baseline(below_inp, mode="terminal")
    assert above.probability is not None
    assert below.probability is not None
    assert (above.probability + below.probability) == pytest.approx(1.0, abs=1e-6)


def test_missing_spot_returns_none() -> None:
    inp = _inputs(spot=None, strike=150_000, direction="above", days_to_resolution=30)
    out = threshold_baseline(inp)
    assert out.probability is None
    assert "missing spot" in " ".join(out.reasons)


def test_missing_iv_returns_none() -> None:
    inp = _inputs(spot=100_000, strike=150_000, direction="above", days_to_resolution=30, iv=None)
    out = threshold_baseline(inp)
    assert out.probability is None


def test_expired_resolution_returns_none() -> None:
    inp = _inputs(spot=100_000, strike=150_000, direction="above", days_to_resolution=-1, iv=0.5)
    out = threshold_baseline(inp)
    assert out.probability is None


def test_strike_iv_preferred_over_atm_when_given() -> None:
    # ATM vol + strike vol produce different answers; baseline should take strike.
    features = MarketFeatures(
        asset="BTC",
        strike=150_000,
        direction="above",
        resolution_date=datetime(2026, 4, 22, tzinfo=UTC) + timedelta(days=90),
    )
    inputs_atm_only = BaselineInputs(
        features=features, asked_at=_asked_at(), spot=100_000, implied_vol_atm=0.6
    )
    inputs_with_strike_vol = BaselineInputs(
        features=features,
        asked_at=_asked_at(),
        spot=100_000,
        implied_vol_atm=0.6,
        implied_vol_strike=0.8,
    )
    atm = threshold_baseline(inputs_atm_only)
    skewed = threshold_baseline(inputs_with_strike_vol)
    assert atm.probability is not None and skewed.probability is not None
    # Higher vol at the strike → higher touch probability.
    assert skewed.probability > atm.probability
    # Falling back to ATM widens the band.
    assert atm.uncertainty_multiplier > skewed.uncertainty_multiplier


def test_classifier_to_threshold_integration() -> None:
    classification = classify(
        question="Will BTC reach $150k by July 22, 2026?",
        tags=["crypto"],
        end_date=datetime(2026, 7, 22, tzinfo=UTC),
    )
    assert classification.market_type is MarketType.THRESHOLD
    inp = BaselineInputs(
        features=classification.features,
        asked_at=_asked_at(),
        spot=100_000,
        implied_vol_atm=0.55,
    )
    out = threshold_baseline(inp)
    assert out.probability is not None
    assert 0.0 < out.probability < 1.0
    # Strike is ~50% above spot, 3 months horizon, 55% IV — probability should
    # be small but non-trivial (a few percent).
    assert out.probability < 0.25
    assert out.probability > 0.01


def test_monotone_in_vol() -> None:
    low_iv = _inputs(spot=100_000, strike=150_000, direction="above", days_to_resolution=90, iv=0.3)
    high_iv = _inputs(spot=100_000, strike=150_000, direction="above", days_to_resolution=90, iv=0.9)
    low = threshold_baseline(low_iv)
    high = threshold_baseline(high_iv)
    assert low.probability is not None and high.probability is not None
    assert high.probability > low.probability


def test_realized_vol_anchors_sigma_when_implied_vol_is_low() -> None:
    no_rv = _inputs(
        spot=100_000,
        strike=150_000,
        direction="above",
        days_to_resolution=90,
        iv=0.3,
    )
    with_rv = _inputs(
        spot=100_000,
        strike=150_000,
        direction="above",
        days_to_resolution=90,
        iv=0.3,
        realized_vol=0.9,
    )
    low = threshold_baseline(no_rv)
    anchored = threshold_baseline(with_rv)
    assert low.probability is not None and anchored.probability is not None
    assert anchored.probability > low.probability


def test_monotone_in_horizon() -> None:
    near = _inputs(spot=100_000, strike=150_000, direction="above", days_to_resolution=7, iv=0.5)
    far = _inputs(spot=100_000, strike=150_000, direction="above", days_to_resolution=365, iv=0.5)
    near_out = threshold_baseline(near)
    far_out = threshold_baseline(far)
    assert near_out.probability is not None and far_out.probability is not None
    assert far_out.probability > near_out.probability


def test_probability_in_valid_range() -> None:
    inp = _inputs(spot=100_000, strike=150_000, direction="above", days_to_resolution=30, iv=0.5)
    out = threshold_baseline(inp)
    assert out.probability is not None
    assert 0.0 <= out.probability <= 1.0
    # Sanity: the distance in log-space should still be non-trivial.
    assert math.isfinite(out.probability)
