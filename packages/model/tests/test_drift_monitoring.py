from __future__ import annotations

import pytest
from model import (
    DailySkillSample,
    MarketType,
    WalkForwardSample,
    brier_skill,
    rolling_window_reports,
    should_auto_disable,
)


def _sample(
    *,
    asked_at: float,
    p: float,
    y: int,
    type_: MarketType = MarketType.THRESHOLD,
    ttr_s: float = 3 * 86_400.0,
    regime: str | None = None,
) -> WalkForwardSample:
    return WalkForwardSample(
        market_type=type_,
        asked_at=asked_at,
        resolved_at=asked_at + ttr_s,
        predicted_prob=p,
        outcome=y,
        time_to_resolution_s=ttr_s,
        regime=regime,
    )


def test_rolling_window_reports_default_three_windows() -> None:
    asked_at = 100 * 86_400.0  # day 100
    samples = [
        _sample(asked_at=(100 - 1) * 86_400.0, p=0.8, y=1),   # in 7d/30d/90d
        _sample(asked_at=(100 - 10) * 86_400.0, p=0.3, y=0),  # in 30d/90d only
        _sample(asked_at=(100 - 60) * 86_400.0, p=0.5, y=0),  # in 90d only
        _sample(asked_at=(100 - 200) * 86_400.0, p=0.1, y=1), # outside all windows
    ]

    reports = rolling_window_reports(samples, asked_at=asked_at)
    by_label = {r.window_label: r for r in reports}

    assert set(by_label.keys()) == {"7d", "30d", "90d"}
    assert by_label["7d"].sample_count == 1
    assert by_label["30d"].sample_count == 2
    assert by_label["90d"].sample_count == 3
    # 7d window only sees the well-calibrated sample → small Brier
    assert by_label["7d"].report.overall.brier == pytest.approx((0.8 - 1) ** 2)


def test_rolling_window_reports_excludes_samples_after_asked_at() -> None:
    # A sample posted AFTER asked_at must never appear in a window — that's
    # the PIT contract. Otherwise a backtest at T sees future predictions.
    asked_at = 100 * 86_400.0
    future = _sample(asked_at=(100 + 1) * 86_400.0, p=0.99, y=1)
    inside = _sample(asked_at=(100 - 1) * 86_400.0, p=0.5, y=0)
    reports = rolling_window_reports([future, inside], asked_at=asked_at)
    assert reports[0].sample_count == 1


def test_rolling_window_reports_carries_regime_through_to_strata() -> None:
    asked_at = 100 * 86_400.0
    samples = [
        _sample(asked_at=(100 - 1) * 86_400.0, p=0.5, y=1, regime="bull_trend"),
        _sample(asked_at=(100 - 1) * 86_400.0, p=0.5, y=0, regime="chop"),
    ]
    reports = rolling_window_reports(samples, asked_at=asked_at)
    by_regime = {r.regime: r for r in reports[0].report.by_regime}
    assert set(by_regime.keys()) == {"bull_trend", "chop"}


def test_brier_skill_positive_when_model_beats_baseline() -> None:
    assert brier_skill(0.10, 0.25) == pytest.approx(0.6)


def test_brier_skill_negative_when_model_worse_than_baseline() -> None:
    skill = brier_skill(0.30, 0.25)
    assert skill is not None and skill < 0


def test_brier_skill_returns_none_for_degenerate_baseline() -> None:
    assert brier_skill(0.10, 0.0) is None


def _daily(day: int, *, model: float, baseline: float) -> DailySkillSample:
    return DailySkillSample(
        asked_at=day * 86_400.0,
        model_brier=model,
        baseline_brier=baseline,
    )


def test_auto_disable_fires_after_seven_consecutive_negative_days() -> None:
    # 7 days, every day model_brier > baseline_brier → negative skill streak.
    samples = [_daily(day=d, model=0.30, baseline=0.25) for d in range(1, 8)]
    decision = should_auto_disable(samples)
    assert decision.should_disable is True
    assert decision.consecutive_days == 7
    assert decision.threshold_days == 7


def test_auto_disable_does_not_fire_when_streak_short() -> None:
    samples = [_daily(day=d, model=0.30, baseline=0.25) for d in range(1, 5)]
    decision = should_auto_disable(samples)
    assert decision.should_disable is False
    assert decision.consecutive_days == 4


def test_auto_disable_streak_resets_on_a_positive_skill_day() -> None:
    samples = [
        _daily(day=1, model=0.30, baseline=0.25),  # negative
        _daily(day=2, model=0.30, baseline=0.25),  # negative
        _daily(day=3, model=0.30, baseline=0.25),  # negative
        _daily(day=4, model=0.20, baseline=0.25),  # POSITIVE — resets
        _daily(day=5, model=0.30, baseline=0.25),  # negative
        _daily(day=6, model=0.30, baseline=0.25),  # negative
        _daily(day=7, model=0.30, baseline=0.25),  # negative
        _daily(day=8, model=0.30, baseline=0.25),  # negative
        _daily(day=9, model=0.30, baseline=0.25),  # negative — streak now 5
    ]
    decision = should_auto_disable(samples, consecutive_days=7)
    assert decision.should_disable is False
    assert decision.consecutive_days == 5


def test_auto_disable_streak_resets_when_a_day_is_skipped() -> None:
    # 36h gap is fine; >36h triggers a reset so a multi-day pipeline outage
    # doesn't accidentally accumulate a fake streak.
    samples = [
        _daily(day=1, model=0.30, baseline=0.25),
        _daily(day=2, model=0.30, baseline=0.25),
        _daily(day=2 + 5, model=0.30, baseline=0.25),  # 5-day gap → reset
        _daily(day=2 + 6, model=0.30, baseline=0.25),
        _daily(day=2 + 7, model=0.30, baseline=0.25),
    ]
    decision = should_auto_disable(samples, consecutive_days=4)
    assert decision.should_disable is False
    assert decision.consecutive_days == 3  # only the 3 post-gap days


def test_auto_disable_handles_empty_input_gracefully() -> None:
    decision = should_auto_disable([])
    assert decision.should_disable is False
    assert decision.consecutive_days == 0


def test_auto_disable_streak_continues_when_baseline_is_undefined() -> None:
    # An undefined-skill day (baseline_brier=0) is treated as "no signal"
    # and resets the streak — we should NOT disable through silence.
    samples = [
        _daily(day=1, model=0.30, baseline=0.25),
        _daily(day=2, model=0.30, baseline=0.0),   # undefined → reset
        _daily(day=3, model=0.30, baseline=0.25),
    ]
    decision = should_auto_disable(samples, consecutive_days=2)
    assert decision.should_disable is False
    assert decision.consecutive_days == 1
