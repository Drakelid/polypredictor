"""Threshold market baseline — Black-Scholes.

PRD §6.1: "barrier-crossing probability from realized vol + ATM IV + skew
(inverted Black-Scholes). This is the single strongest prior for crypto
price-target markets."

Two flavors:
  * Terminal probability — P(S_T relative to K) at a single resolution time T.
    Used when the market resolves on a specific date.
  * One-touch probability — P(max_{0<=t<=T} S_t >= K) or the below-variant.
    Used when the market resolves YES the moment the barrier is breached.

The classifier doesn't currently distinguish these at the text layer; the
pipeline defaults to one-touch because Polymarket phrasings like "will BTC
exceed $X by DATE" are typically one-touch. Call sites can override.

Math assumes a geometric Brownian motion for the underlying under the
risk-neutral measure:

    dS_t = mu * S_t * dt + sigma * S_t * dW_t

where ``mu`` is the drift (risk-free rate ``r`` minus any dividend yield —
0 by default for crypto) and ``sigma`` is annualized volatility.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
from scipy.stats import norm

from ..types import BaselineSource
from .base import BaselineInputs, BaselineOutput

# --- Public entrypoints -----------------------------------------------------


def threshold_baseline(inp: BaselineInputs, *, mode: str = "one_touch") -> BaselineOutput:
    """Compute a threshold-market baseline probability.

    ``mode``:
      - ``"terminal"`` — finish probability at T.
      - ``"one_touch"`` — barrier-crossing probability any time in [0, T].

    Returns :data:`BaselineOutput` with ``probability=None`` if required
    inputs are missing; never guesses.
    """
    f = inp.features
    reasons: list[str] = []

    if f.strike is None or f.direction is None:
        reasons.append("missing strike or direction")
        return BaselineOutput(
            probability=None,
            source=_source_for_mode(mode),
            uncertainty_multiplier=2.0,
            reasons=reasons,
        )
    if inp.spot is None or inp.spot <= 0:
        reasons.append("missing spot")
        return BaselineOutput(
            probability=None,
            source=_source_for_mode(mode),
            uncertainty_multiplier=2.0,
            reasons=reasons,
        )
    if inp.implied_vol_atm is None or inp.implied_vol_atm <= 0:
        reasons.append("missing implied vol")
        return BaselineOutput(
            probability=None,
            source=_source_for_mode(mode),
            uncertainty_multiplier=2.0,
            reasons=reasons,
        )
    if f.resolution_date is None:
        reasons.append("missing resolution date")
        return BaselineOutput(
            probability=None,
            source=_source_for_mode(mode),
            uncertainty_multiplier=2.0,
            reasons=reasons,
        )

    # Prefer the strike-specific vol; fall back to ATM and widen the band
    # to reflect skew uncertainty.
    sigma = inp.implied_vol_strike if inp.implied_vol_strike is not None else inp.implied_vol_atm
    skew_fallback = inp.implied_vol_strike is None
    if sigma is None or sigma <= 0:
        return BaselineOutput(
            probability=None,
            source=_source_for_mode(mode),
            uncertainty_multiplier=2.0,
            reasons=["non-positive sigma"],
        )

    T = _years_to_resolution(f.resolution_date, inp.asked_at)
    if T <= 0:
        reasons.append("resolution_date in the past")
        return BaselineOutput(
            probability=None,
            source=_source_for_mode(mode),
            uncertainty_multiplier=2.0,
            reasons=reasons,
        )

    spot = float(inp.spot)
    strike = float(f.strike)
    direction = f.direction  # 'above' | 'below'
    r = float(inp.risk_free_rate)

    if mode == "terminal":
        prob = _terminal_probability(spot, strike, sigma, T, r, direction)
        source = BaselineSource.BS_TERMINAL
    else:
        prob = _one_touch_probability(spot, strike, sigma, T, r, direction)
        source = BaselineSource.BS_ONE_TOUCH

    widen = 1.0
    if skew_fallback:
        widen *= 1.15
    # Distant thresholds (>2σ) have more tail-sensitivity than the Gaussian
    # assumption captures; nudge the conformal band wider.
    moneyness_sigma = abs(math.log(strike / spot)) / (sigma * math.sqrt(T))
    if moneyness_sigma > 2.0:
        widen *= 1.15

    reasons.append(f"spot={spot:.4g} strike={strike:.4g} sigma={sigma:.3f} T={T:.3f}y mode={mode}")
    return BaselineOutput(
        probability=prob,
        source=source,
        uncertainty_multiplier=widen,
        reasons=reasons,
    )


# --- Math -------------------------------------------------------------------


def _years_to_resolution(resolution_date: datetime, asked_at_epoch: float) -> float:
    asked = datetime.fromtimestamp(asked_at_epoch, tz=UTC)
    resolution = resolution_date if resolution_date.tzinfo else resolution_date.replace(tzinfo=UTC)
    seconds = (resolution - asked).total_seconds()
    # 365.25 days per year — good enough for sub-year horizons; crypto markets
    # rarely span multiple years, so the rounding is immaterial.
    return seconds / (365.25 * 86_400)


def _terminal_probability(
    spot: float,
    strike: float,
    sigma: float,
    T: float,
    r: float,
    direction: str,
) -> float:
    """Risk-neutral P(S_T >= K) (or <=) for GBM.

    Using the standard Black-Scholes d2:

        d2 = (ln(S/K) + (r - sigma^2/2) * T) / (sigma * sqrt(T))
        P(S_T >= K) = N(d2)
        P(S_T <= K) = N(-d2)
    """
    d2 = (math.log(spot / strike) + (r - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    if direction == "above":
        return float(norm.cdf(d2))
    return float(norm.cdf(-d2))


def _one_touch_probability(
    spot: float,
    barrier: float,
    sigma: float,
    T: float,
    r: float,
    direction: str,
) -> float:
    """Probability the barrier is touched at some time in [0, T].

    Closed-form via the reflection principle. With drift ``mu = r``:

        X_t = ln(S_t / S_0) = (mu - sigma^2/2) * t + sigma * W_t
        b = ln(barrier / spot)
        P(max X_t >= b) = N((b - m*T) / (sigma*sqrt(T))) * (-1) ...

    We use the two-term formula:

        For an up-barrier (direction = 'above', barrier > spot):
            a = ln(barrier / spot) / (sigma * sqrt(T))
            mu_hat = (r - sigma^2/2) / sigma
            P(touch) = N(-a + mu_hat * sqrt(T))
                       + exp(2 * mu_hat * a) * N(-a - mu_hat * sqrt(T))

        For a down-barrier (barrier < spot):
            a = ln(spot / barrier) / (sigma * sqrt(T))
            P(touch) = N(-a - mu_hat * sqrt(T))
                       + exp(-2 * mu_hat * a) * N(-a + mu_hat * sqrt(T))

    Both collapse to 1.0 if the barrier is already breached at t=0, and
    return a non-negative number <= 1.
    """
    # Already breached at t=0.
    if direction == "above" and spot >= barrier:
        return 1.0
    if direction == "below" and spot <= barrier:
        return 1.0

    mu_hat = (r - 0.5 * sigma * sigma) / sigma
    sqrtT = math.sqrt(T)

    if direction == "above":
        a = math.log(barrier / spot) / (sigma * sqrtT)
        term1 = float(norm.cdf(-a + mu_hat * sqrtT))
        term2 = math.exp(2.0 * mu_hat * a) * float(norm.cdf(-a - mu_hat * sqrtT))
    else:
        a = math.log(spot / barrier) / (sigma * sqrtT)
        term1 = float(norm.cdf(-a - mu_hat * sqrtT))
        term2 = math.exp(-2.0 * mu_hat * a) * float(norm.cdf(-a + mu_hat * sqrtT))

    prob = term1 + term2
    # Numerical safety: clip to [0, 1]. The exponential term can overflow
    # slightly for extreme inputs.
    return float(np.clip(prob, 0.0, 1.0))



def _source_for_mode(mode: str) -> BaselineSource:
    return BaselineSource.BS_TERMINAL if mode == "terminal" else BaselineSource.BS_ONE_TOUCH
