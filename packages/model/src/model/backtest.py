"""Walk-forward backtest evaluator (M6.1) + drift-monitoring helpers (M6.3).

Pure functions only — no I/O. The expected calling pattern:

1. A driver (CLI / API audit) replays the PIT store: for each resolved
   market, replay each as-of timestamp the model would have predicted at,
   compute the ensemble probability that *would have* been served at that
   moment, and record one :class:`WalkForwardSample` carrying the prediction,
   outcome, time-to-resolution, market-type, and (later) regime label.

2. The driver passes the list to :func:`walk_forward_evaluate`, which
   stratifies by (market_type, ttr_bucket, regime) and returns Brier / ECE /
   conformal-coverage metrics per stratum plus an overall aggregate. The
   "walk-forward" property is enforced by the PIT replay in step 1; the
   evaluator itself is just a stratified scorer.

3. For drift monitoring (§6.3), :func:`population_stability_index` and
   :func:`kl_divergence` compare a feature's training distribution against a
   recent live window and surface PSI / KL — the values the auto-disable rule
   alarms on.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from .types import MarketType

# Default time-to-resolution buckets in seconds (PRD §6.4 Mondrian axis).
TTR_BUCKETS_DEFAULT: tuple[tuple[float, float, str], ...] = (
    (0.0, 86_400.0, "lt_1d"),
    (86_400.0, 7 * 86_400.0, "1d_7d"),
    (7 * 86_400.0, 30 * 86_400.0, "7d_30d"),
    (30 * 86_400.0, math.inf, "gt_30d"),
)


@dataclass(frozen=True)
class WalkForwardSample:
    """One PIT-replayed prediction.

    ``predicted_prob`` is the model's output at ``asked_at``; ``outcome`` is
    the eventual binary resolution (0 or 1). ``time_to_resolution_s`` is
    measured at ``asked_at``, NOT at resolution — a stratification axis, not a
    leak. ``band_lo`` / ``band_hi`` are the conformal interval endpoints for
    coverage scoring; pass ``None`` to skip coverage in that stratum.
    """

    market_type: MarketType
    asked_at: float
    resolved_at: float
    predicted_prob: float
    outcome: int
    time_to_resolution_s: float
    band_lo: float | None = None
    band_hi: float | None = None
    regime: str | None = None


@dataclass(frozen=True)
class StratumReport:
    market_type: str
    ttr_bucket: str
    regime: str | None
    sample_count: int
    brier: float
    ece: float
    coverage: float | None
    avg_predicted: float
    avg_outcome: float


@dataclass(frozen=True)
class WalkForwardReport:
    strata: list[StratumReport]
    overall: StratumReport
    by_type: list[StratumReport]
    by_regime: list[StratumReport]


def _bucket_for_ttr(seconds: float, buckets: Sequence[tuple[float, float, str]]) -> str:
    for lo, hi, label in buckets:
        if lo <= seconds < hi:
            return label
    return "unknown"


def _brier(samples: list[WalkForwardSample]) -> float:
    if not samples:
        return 0.0
    return sum((s.predicted_prob - s.outcome) ** 2 for s in samples) / len(samples)


def expected_calibration_error(
    samples: list[WalkForwardSample],
    *,
    bins: int = 10,
) -> float:
    """Equal-width-bin ECE: weighted gap between mean prediction and mean
    outcome per probability bin. Returns 0 for empty input.
    """
    if not samples:
        return 0.0
    bin_predictions: list[list[float]] = [[] for _ in range(bins)]
    bin_outcomes: list[list[int]] = [[] for _ in range(bins)]
    for sample in samples:
        clipped = min(max(sample.predicted_prob, 0.0), 1.0 - 1e-9)
        idx = min(int(clipped * bins), bins - 1)
        bin_predictions[idx].append(clipped)
        bin_outcomes[idx].append(sample.outcome)
    n = len(samples)
    ece = 0.0
    for predictions, outcomes in zip(bin_predictions, bin_outcomes, strict=True):
        if not predictions:
            continue
        weight = len(predictions) / n
        avg_pred = sum(predictions) / len(predictions)
        avg_out = sum(outcomes) / len(outcomes)
        ece += weight * abs(avg_pred - avg_out)
    return ece


def _coverage(samples: list[WalkForwardSample]) -> float | None:
    """Empirical conformal coverage. None when no samples carry bands."""
    banded = [s for s in samples if s.band_lo is not None and s.band_hi is not None]
    if not banded:
        return None
    hits = sum(
        1
        for s in banded
        if (s.band_lo is not None and s.band_hi is not None)
        and s.band_lo <= float(s.outcome) <= s.band_hi
    )
    return hits / len(banded)


def _stratum_report(
    samples: list[WalkForwardSample],
    *,
    market_type: str,
    ttr_label: str,
    regime: str | None,
) -> StratumReport:
    return StratumReport(
        market_type=market_type,
        ttr_bucket=ttr_label,
        regime=regime,
        sample_count=len(samples),
        brier=_brier(samples),
        ece=expected_calibration_error(samples),
        coverage=_coverage(samples),
        avg_predicted=(
            sum(s.predicted_prob for s in samples) / len(samples)
            if samples
            else 0.0
        ),
        avg_outcome=(
            sum(s.outcome for s in samples) / len(samples) if samples else 0.0
        ),
    )


def walk_forward_evaluate(
    samples: list[WalkForwardSample],
    *,
    ttr_buckets: Sequence[tuple[float, float, str]] = TTR_BUCKETS_DEFAULT,
) -> WalkForwardReport:
    """Stratify PIT-replayed predictions and score per (type, ttr [, regime]).

    Returns a per-stratum table plus three aggregates: overall, by-type, and
    by-regime. Empty input gives an empty report (zeros throughout).
    """
    if not samples:
        empty = StratumReport(
            market_type="overall",
            ttr_bucket="overall",
            regime=None,
            sample_count=0,
            brier=0.0,
            ece=0.0,
            coverage=None,
            avg_predicted=0.0,
            avg_outcome=0.0,
        )
        return WalkForwardReport(strata=[], overall=empty, by_type=[], by_regime=[])

    by_cell: dict[tuple[str, str, str | None], list[WalkForwardSample]] = defaultdict(list)
    by_type: dict[str, list[WalkForwardSample]] = defaultdict(list)
    by_regime: dict[str | None, list[WalkForwardSample]] = defaultdict(list)
    for sample in samples:
        type_label = sample.market_type.value
        ttr_label = _bucket_for_ttr(sample.time_to_resolution_s, ttr_buckets)
        cell_key = (type_label, ttr_label, sample.regime)
        by_cell[cell_key].append(sample)
        by_type[type_label].append(sample)
        by_regime[sample.regime].append(sample)

    strata = sorted(
        (
            _stratum_report(
                cell_samples,
                market_type=key[0],
                ttr_label=key[1],
                regime=key[2],
            )
            for key, cell_samples in by_cell.items()
        ),
        key=lambda r: (r.market_type, r.ttr_bucket, r.regime or ""),
    )
    type_reports = sorted(
        (
            _stratum_report(
                type_samples,
                market_type=type_label,
                ttr_label="overall",
                regime=None,
            )
            for type_label, type_samples in by_type.items()
        ),
        key=lambda r: r.market_type,
    )
    regime_reports = sorted(
        (
            _stratum_report(
                regime_samples,
                market_type="overall",
                ttr_label="overall",
                regime=regime,
            )
            for regime, regime_samples in by_regime.items()
        ),
        key=lambda r: r.regime or "",
    )
    overall = _stratum_report(
        samples,
        market_type="overall",
        ttr_label="overall",
        regime=None,
    )
    return WalkForwardReport(
        strata=strata,
        overall=overall,
        by_type=type_reports,
        by_regime=regime_reports,
    )


# --- Drift monitoring (§6.3) -------------------------------------------------


def _bucket_edges(reference: Sequence[float], buckets: int) -> list[float]:
    """Equal-frequency edges from the reference distribution.

    Falls back to equal-width over [min, max] when the reference has too few
    distinct values to support quantile cuts.
    """
    if not reference:
        return [0.0, 1.0]
    ordered = sorted(float(value) for value in reference)
    distinct = sorted(set(ordered))
    if len(distinct) < 2:
        # Degenerate reference — bucket around the constant value.
        center = float(distinct[0])
        return [center - 0.5, center + 0.5]
    if len(distinct) - 1 < buckets:
        # Equal-width fallback — equal-frequency cuts collapse on tiny corpora.
        lo, hi = float(distinct[0]), float(distinct[-1])
        step = (hi - lo) / buckets
        return [lo + i * step for i in range(buckets + 1)]
    edges: list[float] = []
    n = len(ordered)
    for q in range(buckets + 1):
        idx = min(int(q * n / buckets), n - 1)
        edges.append(ordered[idx])
    # Ensure strictly increasing edges so digitize is well-defined.
    deduped: list[float] = []
    for edge in edges:
        if not deduped or edge > deduped[-1]:
            deduped.append(edge)
        else:
            deduped.append(deduped[-1] + 1e-9)
    return deduped


def _bucket_distribution(
    values: Sequence[float],
    edges: list[float],
) -> list[float]:
    counts = [0] * (len(edges) - 1)
    for value in values:
        v = float(value)
        # Right-inclusive on the last bucket; left-inclusive elsewhere.
        for idx in range(len(counts)):
            lo = edges[idx]
            hi = edges[idx + 1]
            if (lo <= v < hi) or (idx == len(counts) - 1 and v == hi):
                counts[idx] += 1
                break
    total = sum(counts) or 1
    return [count / total for count in counts]


def population_stability_index(
    reference: Sequence[float],
    current: Sequence[float],
    *,
    buckets: int = 10,
    epsilon: float = 1e-6,
) -> float:
    """PSI between a reference and current distribution.

    Bucket edges are equal-frequency on the reference; ``epsilon`` floors the
    ratios so PSI is finite even when a bucket goes empty in either
    distribution. PSI > 0.2 is the PRD's drift alarm threshold (§6.3).
    """
    if not reference or not current:
        return 0.0
    edges = _bucket_edges(list(reference), buckets)
    p_ref = _bucket_distribution(reference, edges)
    p_cur = _bucket_distribution(current, edges)
    psi = 0.0
    for ref_share, cur_share in zip(p_ref, p_cur, strict=True):
        ref_safe = max(ref_share, epsilon)
        cur_safe = max(cur_share, epsilon)
        psi += (cur_safe - ref_safe) * math.log(cur_safe / ref_safe)
    return psi


def kl_divergence(
    reference: Sequence[float],
    current: Sequence[float],
    *,
    buckets: int = 10,
    epsilon: float = 1e-6,
) -> float:
    """KL(current || reference) over equal-frequency reference buckets.

    Same bucketing scheme as :func:`population_stability_index` so PSI and KL
    track the same underlying histogram. Returns 0 when either input is empty.
    """
    if not reference or not current:
        return 0.0
    edges = _bucket_edges(list(reference), buckets)
    p_ref = _bucket_distribution(reference, edges)
    p_cur = _bucket_distribution(current, edges)
    kl = 0.0
    for ref_share, cur_share in zip(p_ref, p_cur, strict=True):
        ref_safe = max(ref_share, epsilon)
        cur_safe = max(cur_share, epsilon)
        kl += cur_safe * math.log(cur_safe / ref_safe)
    return kl


# --- Rolling-window drift driver (§6.3) -------------------------------------


# PRD §6.3 nightly cadence — three trailing windows.
ROLLING_WINDOWS_DEFAULT: tuple[tuple[str, float], ...] = (
    ("7d", 7 * 86_400.0),
    ("30d", 30 * 86_400.0),
    ("90d", 90 * 86_400.0),
)


@dataclass(frozen=True)
class RollingWindowReport:
    """One per (window_label, asked_at). ``report`` carries the same per-cell /
    by-type / by-regime stratification :func:`walk_forward_evaluate` returns,
    just filtered to samples whose ``asked_at`` falls inside the window.
    """

    window_label: str
    window_seconds: float
    asked_at: float
    sample_count: int
    report: WalkForwardReport


def rolling_window_reports(
    samples: list[WalkForwardSample],
    *,
    asked_at: float,
    windows: Sequence[tuple[str, float]] = ROLLING_WINDOWS_DEFAULT,
    ttr_buckets: Sequence[tuple[float, float, str]] = TTR_BUCKETS_DEFAULT,
) -> list[RollingWindowReport]:
    """Run :func:`walk_forward_evaluate` over each trailing window.

    A sample qualifies for window ``w`` when its ``asked_at`` falls in
    ``[asked_at - w, asked_at]``. The PIT contract is honored upstream:
    samples must be PIT-replayed predictions (the model's output as of
    ``asked_at``), not retroactively recomputed numbers.
    """
    out: list[RollingWindowReport] = []
    for label, window_seconds in windows:
        floor = asked_at - window_seconds
        windowed = [
            sample
            for sample in samples
            if floor <= sample.asked_at <= asked_at
        ]
        out.append(
            RollingWindowReport(
                window_label=label,
                window_seconds=window_seconds,
                asked_at=asked_at,
                sample_count=len(windowed),
                report=walk_forward_evaluate(windowed, ttr_buckets=ttr_buckets),
            )
        )
    return out


def brier_skill(model_brier: float, baseline_brier: float) -> float | None:
    """Brier skill score: ``1 - model / baseline``.

    Positive = model better than baseline; negative = model worse. Returns
    ``None`` when the baseline is zero (perfect calibration), which would
    make the ratio undefined — the auto-disable rule treats undefined skill
    as "no signal" rather than "model failed".
    """
    if baseline_brier <= 0:
        return None
    return 1.0 - (model_brier / baseline_brier)


@dataclass(frozen=True)
class DailySkillSample:
    """One day's rolling-30d skill snapshot used by the auto-disable rule.

    Carrying ``asked_at`` (as a float ts) lets the rule require *consecutive
    days* rather than just "the last N entries" — gaps in the input would
    otherwise hide weekends / outages and trigger spurious disables.
    """

    asked_at: float
    model_brier: float
    baseline_brier: float


@dataclass(frozen=True)
class AutoDisableDecision:
    should_disable: bool
    consecutive_days: int
    threshold_days: int
    reason: str


def should_auto_disable(
    daily_samples: Sequence[DailySkillSample],
    *,
    consecutive_days: int = 7,
    day_seconds: float = 86_400.0,
    gap_tolerance_seconds: float = 1.5 * 86_400.0,
) -> AutoDisableDecision:
    """PRD §8 auto-disable rule.

    A per-type model is disabled when its rolling-30d Brier has been WORSE
    than the baseline (the market-implied prior in serving) for
    ``consecutive_days`` consecutive daily snapshots — i.e. negative skill
    every day. ``gap_tolerance_seconds`` allows a ~36h gap between snapshots
    so a one-day pipeline outage doesn't reset the streak.

    Returns a decision plus the streak length so callers can render a
    "disabled in N days unless something improves" warning before flipping.
    """
    if consecutive_days <= 0 or not daily_samples:
        return AutoDisableDecision(
            should_disable=False,
            consecutive_days=0,
            threshold_days=consecutive_days,
            reason="no daily skill samples",
        )

    ordered = sorted(daily_samples, key=lambda s: s.asked_at)
    streak = 0
    last_ts: float | None = None
    for sample in ordered:
        skill = brier_skill(sample.model_brier, sample.baseline_brier)
        if skill is None or skill >= 0.0:
            streak = 0
            last_ts = sample.asked_at
            continue
        if last_ts is not None:
            gap = sample.asked_at - last_ts
            if gap > gap_tolerance_seconds:
                streak = 0
        streak += 1
        last_ts = sample.asked_at
    should_disable = streak >= consecutive_days
    if should_disable:
        reason = (
            f"{streak} consecutive days of negative 30d Brier skill "
            f"(threshold: {consecutive_days})"
        )
    else:
        reason = (
            f"streak {streak}/{consecutive_days} days of negative skill"
        )
    return AutoDisableDecision(
        should_disable=should_disable,
        consecutive_days=streak,
        threshold_days=consecutive_days,
        reason=reason,
    )


__all__ = [
    "ROLLING_WINDOWS_DEFAULT",
    "TTR_BUCKETS_DEFAULT",
    "AutoDisableDecision",
    "DailySkillSample",
    "RollingWindowReport",
    "StratumReport",
    "WalkForwardReport",
    "WalkForwardSample",
    "brier_skill",
    "expected_calibration_error",
    "kl_divergence",
    "population_stability_index",
    "rolling_window_reports",
    "should_auto_disable",
    "walk_forward_evaluate",
]
