from __future__ import annotations

import pytest
from ingest.workers.concentration import (
    WHALE_THRESHOLD_DEFAULT,
    concentration_for_outcome,
    gini_coefficient,
)


def test_gini_returns_none_for_empty() -> None:
    assert gini_coefficient([]) is None


def test_gini_returns_none_for_single_positive_holder() -> None:
    # Single holder: Gini is undefined for our PIT purpose — not enough signal.
    assert gini_coefficient([100.0]) is None


def test_gini_uniform_is_zero() -> None:
    assert gini_coefficient([10.0, 10.0, 10.0, 10.0]) == pytest.approx(0.0)


def test_gini_increases_with_concentration() -> None:
    uniform = gini_coefficient([100.0, 100.0, 100.0, 100.0, 100.0])
    skewed = gini_coefficient([500.0, 100.0, 100.0, 100.0, 100.0])
    very_skewed = gini_coefficient([900.0, 25.0, 25.0, 25.0, 25.0])
    assert uniform is not None and skewed is not None and very_skewed is not None
    assert uniform < skewed < very_skewed


def test_gini_all_one_wallet_approaches_upper_bound() -> None:
    # For n holders, Gini is bounded by (n-1)/n when one wallet owns
    # essentially everything. With n=20, the bound is 0.95.
    sizes = [1000.0] + [0.001] * 19
    value = gini_coefficient(sizes)
    assert value is not None
    assert value > 0.9


def test_concentration_for_outcome_whale_flag_fires_above_threshold() -> None:
    # One wallet owns 60% — above 40% default.
    result = concentration_for_outcome([600.0, 100.0, 100.0, 100.0, 100.0])
    assert result.whale_flag is True
    assert result.top1_pct == pytest.approx(0.6)
    assert result.holders_count == 5


def test_concentration_for_outcome_whale_flag_quiet_below_threshold() -> None:
    result = concentration_for_outcome([300.0, 300.0, 300.0, 100.0])
    assert result.whale_flag is False
    assert result.top1_pct == pytest.approx(0.3)


def test_concentration_for_outcome_handles_custom_threshold() -> None:
    sizes = [500.0, 200.0, 200.0, 100.0]
    relaxed = concentration_for_outcome(sizes, whale_threshold=0.7)
    strict = concentration_for_outcome(sizes, whale_threshold=0.4)
    assert relaxed.whale_flag is False
    assert strict.whale_flag is True  # top1 = 0.5 > 0.4


def test_concentration_for_outcome_empty_returns_nulls() -> None:
    result = concentration_for_outcome([])
    assert result.gini is None
    assert result.top1_pct is None
    assert result.top5_pct is None
    assert result.whale_flag is False
    assert result.holders_count == 0


def test_whale_threshold_default_is_forty_pct() -> None:
    assert WHALE_THRESHOLD_DEFAULT == 0.40


def test_top5_pct_sums_correctly_when_more_than_five_holders() -> None:
    sizes = [100.0, 80.0, 60.0, 40.0, 20.0, 5.0, 5.0]  # sum 310
    result = concentration_for_outcome(sizes)
    assert result.top5_pct == pytest.approx((100 + 80 + 60 + 40 + 20) / 310)
