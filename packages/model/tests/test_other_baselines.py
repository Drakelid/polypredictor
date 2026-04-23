"""Tests for range / multi-outcome / long-tail / discrete-event baselines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from model import (
    BaselineInputs,
    BaselineSource,
    MarketFeatures,
    discrete_event_baseline,
    long_tail_baseline,
    multi_outcome_baseline,
    range_baseline,
)
from model.baselines.range_ import _estimate_range_correlation, _joint_probability_from_correlation

_asked = datetime(2026, 4, 22, tzinfo=UTC).timestamp()


def test_range_baseline_inside_1sigma_range() -> None:
    features = MarketFeatures(
        asset="BTC",
        range_low=95_000,
        range_high=105_000,
        direction="between",
        resolution_date=datetime(2026, 4, 22, tzinfo=UTC) + timedelta(days=30),
    )
    inp = BaselineInputs(
        features=features, asked_at=_asked, spot=100_000, implied_vol_atm=0.4
    )
    out = range_baseline(inp)
    assert out.source is BaselineSource.RANGE_CONJUNCTION
    assert out.probability is not None
    # Narrow range (~5% each side) over 30 days at 40% vol → modest probability.
    assert 0.1 < out.probability < 0.6


def test_range_baseline_inverted_bounds_still_works() -> None:
    # Classifier already normalizes low <= high; baseline should still behave if
    # low > high by accident. With the formula, a negative width yields 0.
    features = MarketFeatures(
        range_low=105_000,
        range_high=95_000,
        direction="between",
        resolution_date=datetime(2026, 4, 22, tzinfo=UTC) + timedelta(days=30),
    )
    inp = BaselineInputs(
        features=features, asked_at=_asked, spot=100_000, implied_vol_atm=0.4
    )
    out = range_baseline(inp)
    assert out.probability == 0.0


def test_range_baseline_realized_vol_can_widen_the_distribution() -> None:
    features = MarketFeatures(
        asset="BTC",
        range_low=95_000,
        range_high=105_000,
        direction="between",
        resolution_date=datetime(2026, 4, 22, tzinfo=UTC) + timedelta(days=30),
    )
    iv_only = BaselineInputs(
        features=features,
        asked_at=_asked,
        spot=100_000,
        implied_vol_atm=0.2,
    )
    blended = BaselineInputs(
        features=features,
        asked_at=_asked,
        spot=100_000,
        realized_vol=0.8,
        implied_vol_atm=0.2,
    )
    narrow = range_baseline(iv_only)
    wide = range_baseline(blended)
    assert narrow.probability is not None and wide.probability is not None
    assert wide.probability < narrow.probability


def test_range_baseline_uses_negative_correlation_vs_independence() -> None:
    features = MarketFeatures(
        asset="BTC",
        range_low=95_000,
        range_high=105_000,
        direction="between",
        resolution_date=datetime(2026, 4, 22, tzinfo=UTC) + timedelta(days=30),
    )
    inp = BaselineInputs(
        features=features,
        asked_at=_asked,
        spot=100_000,
        implied_vol_atm=0.4,
    )
    out = range_baseline(inp)
    assert out.probability is not None

    reason = out.reasons[0]
    parts = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in reason.split()
        if "=" in item
    }
    p_ge_lo = float(parts["p_ge_lo"])
    p_le_hi = float(parts["p_le_hi"])
    rho = float(parts["rho"])

    assert rho < 0.0
    assert out.probability < p_ge_lo * p_le_hi


def test_range_correlation_is_more_negative_for_narrower_ranges() -> None:
    narrow = _estimate_range_correlation(
        spot=100_000,
        lo=99_000,
        hi=101_000,
        sigma=0.4,
        T=30 / 365.25,
        r=0.0,
    )
    wide = _estimate_range_correlation(
        spot=100_000,
        lo=80_000,
        hi=120_000,
        sigma=0.4,
        T=30 / 365.25,
        r=0.0,
    )
    assert narrow < wide


def test_range_joint_probability_respects_frechet_bounds() -> None:
    joint = _joint_probability_from_correlation(0.8, 0.7, -0.95)
    assert 0.5 <= joint <= 0.7


def test_multi_outcome_softmax_normalizes() -> None:
    features = MarketFeatures(asset="ETH")
    inp = BaselineInputs(
        features=features,
        asked_at=_asked,
        multi_outcome_raw=[0.4, 0.2, 0.1, 0.1],
        multi_outcome_self_index=1,
    )
    out = multi_outcome_baseline(inp)
    assert out.probability == pytest.approx(0.25)
    assert out.source is BaselineSource.SOFTMAX


def test_multi_outcome_empty_returns_none() -> None:
    inp = BaselineInputs(features=MarketFeatures(), asked_at=_asked)
    out = multi_outcome_baseline(inp)
    assert out.probability is None


def test_multi_outcome_all_zero_falls_back_to_uniform() -> None:
    inp = BaselineInputs(
        features=MarketFeatures(),
        asked_at=_asked,
        multi_outcome_raw=[0.0, 0.0, 0.0],
        multi_outcome_self_index=0,
    )
    out = multi_outcome_baseline(inp)
    assert out.probability == pytest.approx(1.0 / 3.0)


def test_long_tail_embedding_prior_passthrough() -> None:
    inp = BaselineInputs(
        features=MarketFeatures(), asked_at=_asked, embedding_prior=0.12
    )
    out = long_tail_baseline(inp)
    assert out.probability == pytest.approx(0.12)
    assert out.source is BaselineSource.EMBEDDING_MATCH


def test_long_tail_base_rate_when_no_embedding() -> None:
    inp = BaselineInputs(
        features=MarketFeatures(), asked_at=_asked, historical_base_rate=0.04
    )
    out = long_tail_baseline(inp)
    assert out.probability == pytest.approx(0.04)
    assert out.source is BaselineSource.BASE_RATE


def test_long_tail_clips_boundaries() -> None:
    inp = BaselineInputs(
        features=MarketFeatures(), asked_at=_asked, embedding_prior=0.0
    )
    out = long_tail_baseline(inp)
    assert out.probability is not None
    # Clamped to 0.005 (open interval).
    assert 0.0 < out.probability <= 0.01


def test_long_tail_no_prior_returns_none() -> None:
    inp = BaselineInputs(features=MarketFeatures(), asked_at=_asked)
    out = long_tail_baseline(inp)
    assert out.probability is None
    assert out.source is BaselineSource.UNIFORM


def test_discrete_fedwatch_passthrough() -> None:
    inp = BaselineInputs(
        features=MarketFeatures(event_family="fomc"),
        asked_at=_asked,
        fedwatch_prob=0.72,
    )
    out = discrete_event_baseline(inp)
    assert out.probability == pytest.approx(0.72)
    assert out.source is BaselineSource.FEDWATCH


def test_discrete_consensus_above() -> None:
    # CPI survey consensus: 3.0% with stddev 0.2%. Market: "CPI > 3.2%".
    # z = (3.2 - 3.0) / 0.2 = 1.0 → P(X > 3.2) = 1 - Phi(1) ≈ 0.1587.
    features = MarketFeatures(event_family="cpi", strike=3.2, direction="above")
    inp = BaselineInputs(
        features=features,
        asked_at=_asked,
        consensus_point=3.0,
        consensus_stddev=0.2,
    )
    out = discrete_event_baseline(inp)
    assert out.probability is not None
    assert out.probability == pytest.approx(0.1587, abs=0.005)
    assert out.source is BaselineSource.CONSENSUS


def test_discrete_consensus_below() -> None:
    features = MarketFeatures(event_family="cpi", strike=2.8, direction="below")
    inp = BaselineInputs(
        features=features,
        asked_at=_asked,
        consensus_point=3.0,
        consensus_stddev=0.2,
    )
    out = discrete_event_baseline(inp)
    assert out.probability is not None
    # z = (2.8 - 3.0)/0.2 = -1.0 → P(X < 2.8) = Phi(-1) ≈ 0.1587.
    assert out.probability == pytest.approx(0.1587, abs=0.005)


def test_discrete_no_input_returns_none() -> None:
    inp = BaselineInputs(features=MarketFeatures(event_family="fomc"), asked_at=_asked)
    out = discrete_event_baseline(inp)
    assert out.probability is None
