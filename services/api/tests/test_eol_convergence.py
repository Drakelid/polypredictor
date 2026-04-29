"""Tests for the M8.3 EOL convergence ramp helpers."""

from __future__ import annotations

import pytest

from api.eol_convergence import (
    PHANTOM_EDGE_WINDOW_S,
    RAMP_THRESHOLD_FRACTION,
    apply_eol_convergence,
    final_life_market_mid_weight,
    should_suppress_phantom_edge,
)


# ---------------------------------------------------------------------------
# final_life_market_mid_weight
# ---------------------------------------------------------------------------


def test_weight_zero_when_outside_final_fraction() -> None:
    # 7-day market with 6 days remaining → weight is 0 (outside last 5%).
    total = 7 * 24 * 3600
    remaining = 6 * 24 * 3600
    assert final_life_market_mid_weight(remaining, total) == 0.0


def test_weight_grows_linearly_inside_ramp() -> None:
    # 100h market, ramp window = 5h. At remaining=2.5h we're halfway: w=0.5.
    total = 100 * 3600
    remaining = 2.5 * 3600
    weight = final_life_market_mid_weight(remaining, total)
    assert weight is not None
    assert weight == pytest.approx(0.5, abs=1e-9)


def test_weight_one_at_resolution() -> None:
    total = 24 * 3600
    assert final_life_market_mid_weight(0.0, total) == 1.0


def test_weight_clamped_to_one_after_resolution() -> None:
    total = 24 * 3600
    assert final_life_market_mid_weight(-100.0, total) == 1.0


def test_weight_none_when_inputs_missing() -> None:
    assert final_life_market_mid_weight(None, 100.0) is None
    assert final_life_market_mid_weight(50.0, None) is None
    assert final_life_market_mid_weight(50.0, 0.0) is None
    assert final_life_market_mid_weight(50.0, -1.0) is None


def test_ramp_boundary_returns_zero_exactly() -> None:
    # At remaining == threshold the ramp hasn't started yet (boundary inclusive
    # of "outside the ramp"), so weight is 0.
    total = 100 * 3600
    threshold = total * RAMP_THRESHOLD_FRACTION
    assert final_life_market_mid_weight(threshold, total) == 0.0


def test_custom_ramp_threshold_fraction() -> None:
    # If the operator widens the ramp to 10% of life, the same remaining
    # time produces a different (smaller) weight.
    total = 100 * 3600
    remaining = 5 * 3600  # boundary of the default 5% ramp; midpoint of 10% ramp.
    weight = final_life_market_mid_weight(
        remaining, total, ramp_threshold_fraction=0.10
    )
    assert weight is not None
    assert weight == pytest.approx(0.5, abs=1e-9)


# ---------------------------------------------------------------------------
# apply_eol_convergence
# ---------------------------------------------------------------------------


def test_apply_returns_model_prob_when_weight_zero() -> None:
    assert apply_eol_convergence(0.7, 0.4, 0.0) == 0.7


def test_apply_returns_model_prob_when_weight_none() -> None:
    assert apply_eol_convergence(0.7, 0.4, None) == 0.7


def test_apply_full_weight_returns_market_mid() -> None:
    assert apply_eol_convergence(0.7, 0.4, 1.0) == 0.4


def test_apply_blends_linearly() -> None:
    # weight=0.5, model=0.8, market=0.3 → 0.55
    assert apply_eol_convergence(0.8, 0.3, 0.5) == pytest.approx(0.55)


def test_apply_clips_to_unit_interval() -> None:
    # Even pathological inputs (weight produced from clamped ramp) stay in [0,1].
    assert apply_eol_convergence(1.5, 0.5, 0.5) == 1.0
    assert apply_eol_convergence(-0.5, 0.5, 0.5) == 0.0


def test_apply_passthrough_when_market_mid_missing() -> None:
    # No market_mid → ramp is moot; the model_prob is the best we have.
    assert apply_eol_convergence(0.7, None, 0.5) == 0.7


def test_apply_returns_none_when_model_prob_missing() -> None:
    assert apply_eol_convergence(None, 0.4, 0.5) is None


# ---------------------------------------------------------------------------
# should_suppress_phantom_edge
# ---------------------------------------------------------------------------


def test_suppress_inside_phantom_window() -> None:
    assert should_suppress_phantom_edge(60.0) is True
    assert should_suppress_phantom_edge(PHANTOM_EDGE_WINDOW_S) is True


def test_no_suppress_outside_phantom_window() -> None:
    assert should_suppress_phantom_edge(PHANTOM_EDGE_WINDOW_S + 1.0) is False
    assert should_suppress_phantom_edge(24 * 3600.0) is False


def test_suppress_when_past_resolution() -> None:
    assert should_suppress_phantom_edge(-1.0) is True


def test_no_suppress_when_unknown() -> None:
    # No end_date → we can't say; default is to NOT suppress.
    assert should_suppress_phantom_edge(None) is False


def test_custom_threshold_overrides_default() -> None:
    # If the operator wants a 5-minute phantom window, only the very last
    # minutes get suppressed.
    assert should_suppress_phantom_edge(600.0, threshold_s=300.0) is False
    assert should_suppress_phantom_edge(120.0, threshold_s=300.0) is True
