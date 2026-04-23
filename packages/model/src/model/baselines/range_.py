"""Range-market baseline.

PRD §6.1: "Conjunction of two threshold probabilities with empirically-estimated
correlation."

Under GBM, the terminal price distribution is lognormal so the probability
of landing in a range is:

    P(lo <= S_T <= hi) = N(d2_lo) - N(d2_hi)

That's exact, no correlation needed — the two threshold events are on the
same underlying at the same time. Correlation matters when the range is
barrier-based (touch both barriers, touch either, etc.). For v0 we assume
terminal-range pricing and document the limitation.

For one-touch ranges ("will S cross $X but never reach $Y"), caller can
compose two one-touch threshold baselines with the appropriate signs.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from scipy.stats import norm

from ..types import BaselineSource
from .base import BaselineInputs, BaselineOutput


def range_baseline(inp: BaselineInputs) -> BaselineOutput:
    f = inp.features
    reasons: list[str] = []
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

    sigma = inp.implied_vol_strike if inp.implied_vol_strike is not None else inp.implied_vol_atm
    asked = datetime.fromtimestamp(inp.asked_at, tz=UTC)
    resolution = (
        f.resolution_date if f.resolution_date.tzinfo else f.resolution_date.replace(tzinfo=UTC)
    )
    T = (resolution - asked).total_seconds() / (365.25 * 86_400)
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
    r = float(inp.risk_free_rate)

    def _d2(strike: float) -> float:
        return (math.log(spot / strike) + (r - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))

    # P(S_T >= lo) - P(S_T >= hi) = P(lo <= S_T <= hi)
    p = float(norm.cdf(_d2(lo)) - norm.cdf(_d2(hi)))
    p = max(0.0, min(1.0, p))

    width_sigmas = math.log(hi / lo) / (sigma * math.sqrt(T))
    widen = 1.0
    if width_sigmas < 0.5:
        # Very narrow window — Gaussian tails under-cover.
        widen = 1.3

    reasons.append(f"spot={spot:.4g} lo={lo:.4g} hi={hi:.4g} sigma={sigma:.3f} T={T:.3f}y")
    return BaselineOutput(
        probability=p,
        source=BaselineSource.RANGE_CONJUNCTION,
        uncertainty_multiplier=widen,
        reasons=reasons,
    )
