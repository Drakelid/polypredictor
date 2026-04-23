"""Range-market baseline.

PRD §6.1: "Conjunction of two threshold probabilities with empirically-estimated
correlation."

We model a range market as the joint event:

    A = {S_T >= low}
    B = {S_T <= high}

and estimate:

    P(range) = P(A ∩ B)

from the two terminal threshold probabilities plus a negative correlation term.
Because both events are driven by the same terminal price, they are not
independent: narrow ranges imply a materially more negative correlation than
wide ranges. We encode that as a simple empirical width / moneyness heuristic
and clip the result to the Fréchet bounds for Bernoulli events.
"""

from __future__ import annotations

import math

from ..types import BaselineSource
from .base import BaselineInputs, BaselineOutput
from .threshold import _effective_sigma, _terminal_probability, _years_to_resolution


def range_baseline(inp: BaselineInputs) -> BaselineOutput:
    f = inp.features
    if f.range_low is None or f.range_high is None:
        return BaselineOutput(
            probability=None,
            source=BaselineSource.RANGE_CONJUNCTION,
            uncertainty_multiplier=2.0,
            reasons=["missing range bounds"],
        )
    if inp.spot is None or inp.spot <= 0:
        return BaselineOutput(
            probability=None,
            source=BaselineSource.RANGE_CONJUNCTION,
            uncertainty_multiplier=2.0,
            reasons=["missing spot"],
        )
    if inp.implied_vol_atm is None or inp.implied_vol_atm <= 0:
        return BaselineOutput(
            probability=None,
            source=BaselineSource.RANGE_CONJUNCTION,
            uncertainty_multiplier=2.0,
            reasons=["missing implied vol"],
        )
    if f.resolution_date is None:
        return BaselineOutput(
            probability=None,
            source=BaselineSource.RANGE_CONJUNCTION,
            uncertainty_multiplier=2.0,
            reasons=["missing resolution date"],
        )

    sigma = _effective_sigma(inp.implied_vol_strike, inp.implied_vol_atm, inp.realized_vol)
    if sigma is None or sigma <= 0:
        return BaselineOutput(
            probability=None,
            source=BaselineSource.RANGE_CONJUNCTION,
            uncertainty_multiplier=2.0,
            reasons=["non-positive sigma"],
        )

    T = _years_to_resolution(f.resolution_date, inp.asked_at)
    if T <= 0:
        return BaselineOutput(
            probability=None,
            source=BaselineSource.RANGE_CONJUNCTION,
            uncertainty_multiplier=2.0,
            reasons=["resolution_date in the past"],
        )

    spot = float(inp.spot)
    lo = float(f.range_low)
    hi = float(f.range_high)
    if hi <= lo:
        return BaselineOutput(
            probability=0.0,
            source=BaselineSource.RANGE_CONJUNCTION,
            uncertainty_multiplier=1.5,
            reasons=[f"inverted or empty bounds: lo={lo:.4g} hi={hi:.4g}"],
        )

    r = float(inp.risk_free_rate)
    p_ge_lo = _terminal_probability(spot, lo, sigma, T, r, "above")
    p_le_hi = _terminal_probability(spot, hi, sigma, T, r, "below")
    rho = _estimate_range_correlation(spot=spot, lo=lo, hi=hi, sigma=sigma, T=T, r=r)
    p = _joint_probability_from_correlation(p_ge_lo, p_le_hi, rho)

    width_sigmas = math.log(hi / lo) / (sigma * math.sqrt(T))
    widen = 1.0 + 0.1 * abs(rho)
    if width_sigmas < 0.5:
        widen *= 1.2
    if inp.realized_vol is None or abs(inp.implied_vol_atm - inp.realized_vol) > 0.15:
        widen *= 1.1

    rv = "None" if inp.realized_vol is None else f"{inp.realized_vol:.3f}"
    reasons = [
        (
            f"p_ge_lo={p_ge_lo:.3f} p_le_hi={p_le_hi:.3f} rho={rho:.3f} "
            f"spot={spot:.4g} lo={lo:.4g} hi={hi:.4g} sigma={sigma:.3f} "
            f"iv_atm={inp.implied_vol_atm:.3f} rv={rv} T={T:.3f}y"
        )
    ]
    return BaselineOutput(
        probability=p,
        source=BaselineSource.RANGE_CONJUNCTION,
        uncertainty_multiplier=widen,
        reasons=reasons,
    )


def _estimate_range_correlation(
    *,
    spot: float,
    lo: float,
    hi: float,
    sigma: float,
    T: float,
    r: float,
) -> float:
    """Heuristic correlation estimate for the two threshold events.

    Narrower ranges create a stronger negative dependence between
    ``{S_T >= low}`` and ``{S_T <= high}``. Ranges centered far away from the
    forward also weaken the dependence because both threshold events are
    already close to deterministic.
    """
    sqrtT = math.sqrt(T)
    width_sigmas = math.log(hi / lo) / (sigma * sqrtT)
    forward = spot * math.exp(r * T)
    center = math.sqrt(lo * hi)
    center_sigmas = abs(math.log(center / forward)) / (sigma * sqrtT)

    width_term = math.exp(-0.9 * max(width_sigmas, 0.0))
    center_term = math.exp(-0.35 * center_sigmas)
    rho = -0.92 * width_term * center_term
    return max(-0.95, min(-0.02, rho))


def _joint_probability_from_correlation(p_a: float, p_b: float, rho: float) -> float:
    """Bernoulli joint probability from marginals + correlation.

    The result is clipped to the admissible Fréchet interval so the heuristic
    correlation estimate cannot produce impossible probabilities.
    """
    var_term = math.sqrt(
        max(0.0, p_a * (1.0 - p_a)) * max(0.0, p_b * (1.0 - p_b))
    )
    raw = p_a * p_b + rho * var_term
    lower = max(0.0, p_a + p_b - 1.0)
    upper = min(p_a, p_b)
    return min(upper, max(lower, raw))
