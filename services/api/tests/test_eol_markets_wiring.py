"""Integration-style tests for the M8.3 EOL convergence wiring in markets.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.markets import (
    _apply_eol_convergence_adjustment,
    _total_life_s,
)


def test_total_life_s_basic() -> None:
    end_date = datetime(2026, 4, 30, 12, tzinfo=UTC)
    first_obs = datetime(2026, 4, 23, 12, tzinfo=UTC)
    assert _total_life_s(end_date, first_obs) == 7 * 24 * 3600


def test_total_life_s_naive_datetimes_get_utc() -> None:
    # Defensive: Polymarket sometimes hands us naive datetimes.
    end_date = datetime(2026, 4, 30, 12)
    first_obs = datetime(2026, 4, 23, 12)
    assert _total_life_s(end_date, first_obs) == 7 * 24 * 3600


def test_total_life_s_none_when_inputs_missing() -> None:
    assert _total_life_s(None, datetime(2026, 4, 23, tzinfo=UTC)) is None
    assert _total_life_s(datetime(2026, 4, 23, tzinfo=UTC), None) is None


def test_total_life_s_none_when_first_observed_after_end_date() -> None:
    # Pathological: data ingest started after the market's nominal end_date.
    end_date = datetime(2026, 4, 23, 12, tzinfo=UTC)
    first_obs = datetime(2026, 4, 30, 12, tzinfo=UTC)
    assert _total_life_s(end_date, first_obs) is None


# ---------------------------------------------------------------------------
# _apply_eol_convergence_adjustment integration
# ---------------------------------------------------------------------------


def test_adjustment_outside_ramp_returns_inputs_unchanged() -> None:
    reasons: list[str] = []
    total_life = 7 * 24 * 3600
    remaining = 6 * 24 * 3600  # 86% remaining → outside the 5% ramp
    out_prob, weight, phantom = _apply_eol_convergence_adjustment(
        model_prob=0.7,
        market_mid=0.4,
        time_to_resolution_s=remaining,
        total_life_s=total_life,
        reasons=reasons,
    )
    assert out_prob == 0.7
    assert weight == 0.0
    assert phantom is False
    assert reasons == []


def test_adjustment_inside_ramp_blends_and_records_reason() -> None:
    reasons: list[str] = []
    total_life = 100 * 3600
    remaining = 2.5 * 3600  # midpoint of 5% ramp → weight = 0.5
    out_prob, weight, phantom = _apply_eol_convergence_adjustment(
        model_prob=0.8,
        market_mid=0.3,
        time_to_resolution_s=remaining,
        total_life_s=total_life,
        reasons=reasons,
    )
    assert weight == pytest.approx(0.5)
    assert out_prob == pytest.approx(0.55)
    assert phantom is False
    assert any("end-of-life convergence" in r for r in reasons)


def test_adjustment_in_final_hour_sets_phantom_and_records_reason() -> None:
    reasons: list[str] = []
    total_life = 7 * 24 * 3600
    remaining = 30 * 60  # 30 minutes
    out_prob, weight, phantom = _apply_eol_convergence_adjustment(
        model_prob=0.8,
        market_mid=0.3,
        time_to_resolution_s=remaining,
        total_life_s=total_life,
        reasons=reasons,
    )
    assert phantom is True
    assert weight is not None and weight > 0.0
    # Convergence ramp engaged AND phantom-edge gate engaged → two reasons.
    assert sum("end-of-life convergence" in r for r in reasons) == 1
    assert sum("phantom-edge gate" in r for r in reasons) == 1


def test_adjustment_with_unknown_total_life_only_emits_phantom_in_final_hour() -> None:
    # When MIN(observed_at) is missing (new market we just discovered) we
    # have no total_life so the ramp can't compute a weight. The phantom
    # gate, which keys only on time_to_resolution_s, still fires.
    reasons: list[str] = []
    out_prob, weight, phantom = _apply_eol_convergence_adjustment(
        model_prob=0.8,
        market_mid=0.3,
        time_to_resolution_s=30 * 60,
        total_life_s=None,
        reasons=reasons,
    )
    assert weight is None
    assert out_prob == 0.8  # no blend without weight
    assert phantom is True
    # No "end-of-life convergence" reason because the ramp wasn't computable;
    # only the phantom-edge reason should be recorded.
    assert not any("end-of-life convergence" in r for r in reasons)
    assert any("phantom-edge gate" in r for r in reasons)


def test_adjustment_with_unknown_ttr_does_nothing() -> None:
    # No end_date → no time_to_resolution → no ramp, no phantom gate.
    reasons: list[str] = []
    out_prob, weight, phantom = _apply_eol_convergence_adjustment(
        model_prob=0.8,
        market_mid=0.3,
        time_to_resolution_s=None,
        total_life_s=7 * 24 * 3600,
        reasons=reasons,
    )
    assert weight is None
    assert out_prob == 0.8
    assert phantom is False
    assert reasons == []


def test_adjustment_at_resolution_pulls_to_market_mid_and_phantom() -> None:
    reasons: list[str] = []
    out_prob, weight, phantom = _apply_eol_convergence_adjustment(
        model_prob=0.8,
        market_mid=0.3,
        time_to_resolution_s=0.0,
        total_life_s=24 * 3600,
        reasons=reasons,
    )
    assert weight == 1.0
    assert out_prob == 0.3
    assert phantom is True


def test_adjustment_does_not_blend_when_market_mid_missing() -> None:
    reasons: list[str] = []
    out_prob, weight, phantom = _apply_eol_convergence_adjustment(
        model_prob=0.8,
        market_mid=None,
        time_to_resolution_s=0.0,
        total_life_s=24 * 3600,
        reasons=reasons,
    )
    # weight is non-zero (we're at resolution) but with no market_mid we
    # can't blend, so model_prob passes through unchanged.
    assert weight == 1.0
    assert out_prob == 0.8
    assert phantom is True
