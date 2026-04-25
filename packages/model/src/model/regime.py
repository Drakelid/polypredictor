"""Daily regime tagger (M6.2, PRD §6.3).

Pure functions only. The eventual production target is an HMM trained on
(BTC realized vol, BTC ↔ NDX correlation, stablecoin-supply delta), but
that is deferred until the upstream daily series are persisted reliably.
This module ships the *deterministic* classifier the HMM will be trained
against — same inputs, same labels, explainable rules. The downstream
ensemble already knows how to consume a ``regime`` string (Mondrian axis
in the conformal registry, by-regime aggregate in the backtest evaluator).

Labels (PRD §6.3 / §6.2):

* ``liquidity_crisis`` — stablecoin supply contracting fast OR realized vol
  in extreme regime; risk-off.
* ``bull_trend``       — momentum positive AND vol moderate; risk-on with
  conviction.
* ``bear_trend``       — momentum negative AND vol moderate.
* ``chop``             — fallback; low-conviction directional state.

The classifier returns a ``RegimeResult`` with the label, a confidence in
[0, 1], and a reasons list so consumers can render explainable badges (PRD
§6.10 LLM guardrails: deterministic before narrative).
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


__all__ = [
    "ANNUALIZATION_FACTOR",
    "RegimeFeatures",
    "RegimeLabel",
    "RegimeResult",
    "RegimeThresholds",
    "regime_features_from_btc_closes",
    "tag_regime",
]
