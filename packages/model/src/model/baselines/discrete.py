"""Discrete-event baseline.

PRD §6.1: "CME FedWatch / OIS-implied probabilities; Bloomberg consensus
survey for macro prints."

For M1 the math is thin — the expensive part is producing the upstream
inputs (FedWatch scrape, survey cache). Both ship later:

  * ``fedwatch_prob``: probability supplied directly by the caller (M5 hooks
    FedWatch). Passes through unchanged.
  * ``consensus_point`` + ``consensus_stddev``: Gaussian approximation for
    macro prints ("CPI prints below 2.8%") when a survey consensus is
    available. We map (strike, point, stddev, direction) → Phi(z).
"""

from __future__ import annotations

from scipy.stats import norm

from ..types import BaselineSource
from .base import BaselineInputs, BaselineOutput


def discrete_event_baseline(inp: BaselineInputs) -> BaselineOutput:
    f = inp.features
    reasons: list[str] = []

    if inp.fedwatch_prob is not None:
        prob = max(0.0, min(1.0, float(inp.fedwatch_prob)))
        reasons.append("fedwatch pass-through")
        return BaselineOutput(
            probability=prob,
            source=BaselineSource.FEDWATCH,
            uncertainty_multiplier=1.1,
            reasons=reasons,
        )

    if inp.consensus_point is not None and inp.consensus_stddev is not None:
        if f.strike is None or f.direction is None:
            reasons.append("consensus available but missing strike/direction")
            return BaselineOutput(
                probability=None,
                source=BaselineSource.CONSENSUS,
                uncertainty_multiplier=1.5,
                reasons=reasons,
            )
        sigma = float(inp.consensus_stddev)
        if sigma <= 0:
            reasons.append("non-positive consensus stddev")
            return BaselineOutput(
                probability=None,
                source=BaselineSource.CONSENSUS,
                uncertainty_multiplier=2.0,
                reasons=reasons,
            )
        z = (float(f.strike) - float(inp.consensus_point)) / sigma
        # direction='above' → P(X > strike) = 1 - Phi(z); 'below' → Phi(z).
        prob = float(1.0 - norm.cdf(z)) if f.direction == "above" else float(norm.cdf(z))
        reasons.append(
            f"consensus Gaussian: point={inp.consensus_point:.4g} sigma={sigma:.4g} "
            f"strike={f.strike:.4g} dir={f.direction}"
        )
        return BaselineOutput(
            probability=prob,
            source=BaselineSource.CONSENSUS,
            uncertainty_multiplier=1.2,
            reasons=reasons,
        )

    return BaselineOutput(
        probability=None,
        source=BaselineSource.CONSENSUS,
        uncertainty_multiplier=2.0,
        reasons=["no fedwatch / consensus input"],
    )
