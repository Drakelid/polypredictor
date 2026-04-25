"""Daily regime tagger (M6.2, PRD §6.3).

Pure functions only. The eventual production target is an HMM trained on
(BTC realized vol, BTC ↔ NDX correlation, stablecoin-supply delta), but
that is deferred until the upstream daily series are persisted reliably.
This module ships the *deterministic* classifier the HMM will be trained
against — same inputs, same labels, explainable rules. The downstream
ensemble already knows how to consume a ``regime`` string (Mondrian axis
in the conformal registry, by-regime aggregate in the backtest evaluator).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

ANNUALIZATION_FACTOR = math.sqrt(365.0)


class RegimeLabel(StrEnum):
    BULL_TREND = "bull_trend"
    BEAR_TREND = "bear_trend"
    CHOP = "chop"
    LIQUIDITY_CRISIS = "liquidity_crisis"


@dataclass(frozen=True)
class RegimeFeatures:
    """Daily inputs the classifier needs.

    All values are nullable — when an input is missing we fall back to a
    quieter rule rather than fabricating data. This is consistent with how
    the threshold baseline degrades when Deribit IV is unavailable.
    """

    btc_realized_vol_24h: float | None = None
    btc_realized_vol_7d: float | None = None
    btc_momentum_7d: float | None = None
    btc_ndx_correlation_30d: float | None = None
    stablecoin_supply_delta_7d: float | None = None  # fractional, e.g. -0.05 = -5%


@dataclass(frozen=True)
class RegimeThresholds:
    """Tunable knobs. Defaults reflect rough crypto-cycle norms; the regime
    job is expected to read overrides from settings as they're calibrated.
    """

    # Liquidity-crisis signals.
    stablecoin_contraction_fraction: float = -0.03   # supply shrinks ≥ 3% / week
    crisis_realized_vol: float = 1.20                # realized vol ≥ 120% annualized
    # Vol regimes for trend-vs-chop.
    moderate_realized_vol_min: float = 0.30          # below = chop, above = trending
    extreme_realized_vol: float = 1.00               # above = crisis-adjacent
    # Momentum thresholds (7d log return).
    bull_momentum: float = 0.04
    bear_momentum: float = -0.04


@dataclass(frozen=True)
class RegimeResult:
    label: RegimeLabel
    confidence: float
    reasons: list[str] = field(default_factory=list)


def _classify(
    features: RegimeFeatures,
    thresholds: RegimeThresholds,
) -> tuple[RegimeLabel, float, list[str]]:
    reasons: list[str] = []

    stablecoin_delta = features.stablecoin_supply_delta_7d
    realized_vol = features.btc_realized_vol_24h
    realized_vol_7d = features.btc_realized_vol_7d
    momentum = features.btc_momentum_7d

    # Liquidity-crisis path — strong stablecoin contraction OR realized vol
    # in the explicit crisis regime overrides everything else.
    if stablecoin_delta is not None and stablecoin_delta <= thresholds.stablecoin_contraction_fraction:
        reasons.append(
            f"stablecoin supply Δ7d={stablecoin_delta:+.1%} ≤ "
            f"{thresholds.stablecoin_contraction_fraction:+.1%}"
        )
        return RegimeLabel.LIQUIDITY_CRISIS, 0.9, reasons

    if realized_vol is not None and realized_vol >= thresholds.crisis_realized_vol:
        reasons.append(
            f"realized vol {realized_vol:.0%} ≥ {thresholds.crisis_realized_vol:.0%}"
        )
        return RegimeLabel.LIQUIDITY_CRISIS, 0.75, reasons

    # Trend vs chop — gate on vol then split by momentum sign.
    if realized_vol is not None and realized_vol < thresholds.moderate_realized_vol_min:
        reasons.append(
            f"realized vol {realized_vol:.0%} < "
            f"{thresholds.moderate_realized_vol_min:.0%}: chop regime"
        )
        return RegimeLabel.CHOP, 0.7, reasons

    if momentum is None:
        reasons.append("no 7d momentum input — defaulting to chop")
        return RegimeLabel.CHOP, 0.4, reasons

    if momentum >= thresholds.bull_momentum:
        reasons.append(
            f"7d momentum {momentum:+.1%} ≥ {thresholds.bull_momentum:+.1%}"
        )
        confidence = _scaled_confidence(
            value=momentum,
            anchor=thresholds.bull_momentum,
            saturate=2 * thresholds.bull_momentum,
        )
        return RegimeLabel.BULL_TREND, confidence, reasons

    if momentum <= thresholds.bear_momentum:
        reasons.append(
            f"7d momentum {momentum:+.1%} ≤ {thresholds.bear_momentum:+.1%}"
        )
        confidence = _scaled_confidence(
            value=-momentum,
            anchor=-thresholds.bear_momentum,
            saturate=-2 * thresholds.bear_momentum,
        )
        return RegimeLabel.BEAR_TREND, confidence, reasons

    reasons.append(
        f"7d momentum {momentum:+.1%} between bear ({thresholds.bear_momentum:+.1%}) "
        f"and bull ({thresholds.bull_momentum:+.1%}) thresholds"
    )

    # Optional NDX-correlation reasoning — does not change the label by
    # itself, but adds a hint to the explainer.
    if features.btc_ndx_correlation_30d is not None:
        reasons.append(
            f"30d BTC↔NDX correlation = {features.btc_ndx_correlation_30d:+.2f}"
        )

    if realized_vol_7d is not None and realized_vol_7d >= thresholds.extreme_realized_vol:
        reasons.append(
            f"7d realized vol {realized_vol_7d:.0%} ≥ extreme threshold "
            f"{thresholds.extreme_realized_vol:.0%}"
        )
        return RegimeLabel.LIQUIDITY_CRISIS, 0.6, reasons

    return RegimeLabel.CHOP, 0.55, reasons


def _scaled_confidence(*, value: float, anchor: float, saturate: float) -> float:
    """Linearly ramp confidence from 0.6 at the threshold to 0.95 at saturate.

    Used so a label landing right at the threshold is reported with modest
    confidence; an obviously-above-threshold input gets near-saturation.
    """
    if not math.isfinite(value) or anchor >= saturate:
        return 0.7
    span = saturate - anchor
    if span <= 0:
        return 0.7
    proximity = max(0.0, min(1.0, (value - anchor) / span))
    return 0.6 + 0.35 * proximity


def tag_regime(
    features: RegimeFeatures,
    *,
    thresholds: RegimeThresholds | None = None,
) -> RegimeResult:
    """Daily regime label + reasons. Pure function; no I/O."""
    label, confidence, reasons = _classify(features, thresholds or RegimeThresholds())
    return RegimeResult(label=label, confidence=confidence, reasons=reasons)


def regime_features_from_btc_closes(
    closes: Sequence[float],
    *,
    btc_ndx_correlation_30d: float | None = None,
    stablecoin_supply_delta_7d: float | None = None,
) -> RegimeFeatures:
    """Derive ``RegimeFeatures`` from a BTC daily-close series.

    Convention: ``closes`` is ordered oldest-first, all positive. We compute
    log returns and annualize stdev by ``sqrt(365)``:

    * ``btc_realized_vol_24h`` = |latest daily log return| × √365
    * ``btc_realized_vol_7d``  = stdev of trailing 7 daily log returns × √365
    * ``btc_momentum_7d``      = ln(latest_close / close 7 days ago)

    Returns features with all derived fields ``None`` when the series is too
    short or contains non-positive values; the classifier already degrades
    gracefully on missing inputs.
    """
    cleaned = [float(value) for value in closes if value is not None and value > 0]
    if len(cleaned) < 2:
        return RegimeFeatures(
            btc_ndx_correlation_30d=btc_ndx_correlation_30d,
            stablecoin_supply_delta_7d=stablecoin_supply_delta_7d,
        )
    log_returns = [
        math.log(cleaned[i] / cleaned[i - 1]) for i in range(1, len(cleaned))
    ]
    last_return = log_returns[-1]
    realized_vol_24h = abs(last_return) * ANNUALIZATION_FACTOR
    trailing_7 = log_returns[-7:]
    realized_vol_7d: float | None = None
    if len(trailing_7) >= 2:
        realized_vol_7d = statistics.pstdev(trailing_7) * ANNUALIZATION_FACTOR
    momentum_7d: float | None = None
    if len(cleaned) >= 8:
        momentum_7d = math.log(cleaned[-1] / cleaned[-8])
    elif len(cleaned) >= 2:
        # Falls back to the longest available window so an early-deploy
        # corpus still emits a momentum signal rather than going blind.
        momentum_7d = math.log(cleaned[-1] / cleaned[0])
    return RegimeFeatures(
        btc_realized_vol_24h=realized_vol_24h,
        btc_realized_vol_7d=realized_vol_7d,
        btc_momentum_7d=momentum_7d,
        btc_ndx_correlation_30d=btc_ndx_correlation_30d,
        stablecoin_supply_delta_7d=stablecoin_supply_delta_7d,
    )


_HMM_STATES: tuple[RegimeLabel, ...] = (
    RegimeLabel.BULL_TREND,
    RegimeLabel.BEAR_TREND,
    RegimeLabel.CHOP,
    RegimeLabel.LIQUIDITY_CRISIS,
)


@dataclass(frozen=True)
class _StatePrototype:
    realized_vol: float
    momentum_7d: float
    stablecoin_delta_7d: float


# Emission prototypes per regime — see PRD §6.3 for the qualitative shape.
# These reflect rough crypto-cycle norms; thresholds.py overrides only the
# rule-based classifier, the HMM keeps its own continuous prototypes so the
# emission likelihood degrades smoothly as features move between states.
_HMM_PROTOTYPES: dict[RegimeLabel, _StatePrototype] = {
    RegimeLabel.BULL_TREND: _StatePrototype(
        realized_vol=0.60, momentum_7d=0.08, stablecoin_delta_7d=0.01
    ),
    RegimeLabel.BEAR_TREND: _StatePrototype(
        realized_vol=0.60, momentum_7d=-0.08, stablecoin_delta_7d=-0.01
    ),
    RegimeLabel.CHOP: _StatePrototype(
        realized_vol=0.30, momentum_7d=0.0, stablecoin_delta_7d=0.0
    ),
    RegimeLabel.LIQUIDITY_CRISIS: _StatePrototype(
        realized_vol=1.50, momentum_7d=-0.10, stablecoin_delta_7d=-0.05
    ),
}

# Per-feature standard deviations used as the diagonal of the (assumed
# diagonal) Gaussian emission covariance.
_HMM_FEATURE_SCALE = _StatePrototype(
    realized_vol=0.40, momentum_7d=0.05, stablecoin_delta_7d=0.02
)

# Sticky 4x4 transition matrix. Diagonal = 0.85 stay-probability; off-diagonal
# spread across the remaining states. Liquidity crises decay to BEAR_TREND
# slightly faster than to other states.
_HMM_TRANSITIONS: dict[RegimeLabel, dict[RegimeLabel, float]] = {
    RegimeLabel.BULL_TREND: {
        RegimeLabel.BULL_TREND: 0.85,
        RegimeLabel.BEAR_TREND: 0.05,
        RegimeLabel.CHOP: 0.07,
        RegimeLabel.LIQUIDITY_CRISIS: 0.03,
    },
    RegimeLabel.BEAR_TREND: {
        RegimeLabel.BULL_TREND: 0.05,
        RegimeLabel.BEAR_TREND: 0.85,
        RegimeLabel.CHOP: 0.05,
        RegimeLabel.LIQUIDITY_CRISIS: 0.05,
    },
    RegimeLabel.CHOP: {
        RegimeLabel.BULL_TREND: 0.06,
        RegimeLabel.BEAR_TREND: 0.06,
        RegimeLabel.CHOP: 0.85,
        RegimeLabel.LIQUIDITY_CRISIS: 0.03,
    },
    RegimeLabel.LIQUIDITY_CRISIS: {
        RegimeLabel.BULL_TREND: 0.02,
        RegimeLabel.BEAR_TREND: 0.10,
        RegimeLabel.CHOP: 0.03,
        RegimeLabel.LIQUIDITY_CRISIS: 0.85,
    },
}

# Initial-state prior — equal across the four states.
_HMM_INITIAL_LOG_PRIOR = math.log(1.0 / len(_HMM_STATES))


def _emission_log_likelihood(
    features: RegimeFeatures, state: RegimeLabel
) -> float:
    """Diagonal-Gaussian log-likelihood of ``features`` under ``state``.

    Missing features are dropped from the sum so partial inputs degrade
    gracefully — consistent with the rule classifier's null-tolerant style.
    """
    prototype = _HMM_PROTOTYPES[state]
    pairs = [
        (features.btc_realized_vol_24h, prototype.realized_vol, _HMM_FEATURE_SCALE.realized_vol),
        (features.btc_momentum_7d, prototype.momentum_7d, _HMM_FEATURE_SCALE.momentum_7d),
        (
            features.stablecoin_supply_delta_7d,
            prototype.stablecoin_delta_7d,
            _HMM_FEATURE_SCALE.stablecoin_delta_7d,
        ),
    ]
    score = 0.0
    n_observed = 0
    for value, mean, sigma in pairs:
        if value is None or sigma <= 0 or not math.isfinite(value):
            continue
        z = (value - mean) / sigma
        score += -0.5 * z * z
        n_observed += 1
    if n_observed == 0:
        # No observed features — uniform emission across states.
        return 0.0
    return score


def _viterbi(
    feature_sequence: Sequence[RegimeFeatures],
) -> tuple[list[RegimeLabel], list[float]]:
    """Standard log-domain Viterbi over the regime HMM.

    Returns the most likely state sequence and a per-step confidence
    derived from the softmax over states' Viterbi log-probabilities at
    that step.
    """
    n = len(feature_sequence)
    if n == 0:
        return [], []

    log_v: list[dict[RegimeLabel, float]] = [{} for _ in range(n)]
    backpointer: list[dict[RegimeLabel, RegimeLabel | None]] = [{} for _ in range(n)]

    # t = 0
    for state in _HMM_STATES:
        log_v[0][state] = (
            _HMM_INITIAL_LOG_PRIOR + _emission_log_likelihood(feature_sequence[0], state)
        )
        backpointer[0][state] = None

    # t > 0
    for t in range(1, n):
        for current in _HMM_STATES:
            best_prev: RegimeLabel | None = None
            best_score = -math.inf
            for prev in _HMM_STATES:
                trans = _HMM_TRANSITIONS[prev][current]
                if trans <= 0:
                    continue
                candidate = log_v[t - 1][prev] + math.log(trans)
                if candidate > best_score:
                    best_score = candidate
                    best_prev = prev
            log_v[t][current] = best_score + _emission_log_likelihood(
                feature_sequence[t], current
            )
            backpointer[t][current] = best_prev

    # Backtrace from the highest-prob terminal state.
    final_state = max(_HMM_STATES, key=lambda s: log_v[n - 1][s])
    states_reversed: list[RegimeLabel] = [final_state]
    for t in range(n - 1, 0, -1):
        prev = backpointer[t][states_reversed[-1]]
        if prev is None:
            prev = states_reversed[-1]
        states_reversed.append(prev)
    state_sequence = list(reversed(states_reversed))

    # Per-step confidence from the softmax over Viterbi log-probs.
    confidences: list[float] = []
    for t in range(n):
        scores = [log_v[t][s] for s in _HMM_STATES]
        confidences.append(_softmax_chosen(scores, _HMM_STATES.index(state_sequence[t])))
    return state_sequence, confidences


def _softmax_chosen(scores: Sequence[float], chosen_index: int) -> float:
    """Numerically-stable softmax probability for the chosen index, clipped
    into ``(0.5, 0.95)`` so tied/uncertain timesteps still report at least
    moderate confidence — same convention the rule classifier uses."""
    if not scores:
        return 0.5
    max_score = max(scores)
    exps = [math.exp(s - max_score) for s in scores]
    total = sum(exps)
    if total <= 0:
        return 0.5
    posterior = exps[chosen_index] / total
    return max(0.5, min(0.95, 0.5 + 0.45 * (posterior - 1.0 / len(scores))))


def tag_regime_hmm(
    feature_sequence: Sequence[RegimeFeatures],
    *,
    thresholds: RegimeThresholds | None = None,
) -> list[RegimeResult]:
    """Hidden-Markov-Model regime tagger over a daily feature sequence.

    The HMM has four discrete states matching :class:`RegimeLabel`. Emission
    log-likelihoods are computed against per-state prototypes in
    ``(realized_vol_24h, momentum_7d, stablecoin_supply_delta_7d)`` space
    using a diagonal Gaussian; missing features are dropped from the sum so
    short or partial daily series still produce a label. Transitions are
    sticky (0.85 stay-probability) so a single noisy day can't flip the
    regime — the temporal smoothing is the HMM's value over the rule-based
    :func:`tag_regime` it sits next to.

    The argument ``thresholds`` is accepted for API compatibility with the
    rule classifier but is not used: the HMM has its own continuous emission
    model. When ``thresholds`` is provided we still surface a leading reason
    documenting that the rule overrides do not apply.

    Returns
    -------
    list[RegimeResult]
        Per-step label, softmax-derived confidence, and a short reason
        explaining the chosen state.
    """
    if not feature_sequence:
        return []

    states, confidences = _viterbi(feature_sequence)
    out: list[RegimeResult] = []
    for idx, (features, state, confidence) in enumerate(
        zip(feature_sequence, states, confidences, strict=True)
    ):
        reasons: list[str] = []
        if thresholds is not None:
            reasons.append(
                "HMM emission prototypes ignore RegimeThresholds; pass via tag_regime if rule overrides are required"
            )
        emission = _emission_log_likelihood(features, state)
        reasons.append(
            f"HMM step {idx + 1}/{len(feature_sequence)}: state={state.value}, "
            f"emission_loglik={emission:.2f}"
        )
        if features.btc_realized_vol_24h is not None:
            reasons.append(f"realized vol = {features.btc_realized_vol_24h:.0%}")
        if features.btc_momentum_7d is not None:
            reasons.append(f"7d momentum = {features.btc_momentum_7d:+.1%}")
        if features.stablecoin_supply_delta_7d is not None:
            reasons.append(
                f"7d stablecoin Δ = {features.stablecoin_supply_delta_7d:+.1%}"
            )
        out.append(RegimeResult(label=state, confidence=confidence, reasons=reasons))
    return out


__all__ = [
    "ANNUALIZATION_FACTOR",
    "RegimeFeatures",
    "RegimeLabel",
    "RegimeResult",
    "RegimeThresholds",
    "regime_features_from_btc_closes",
    "tag_regime",
    "tag_regime_hmm",
]