"""Multi-outcome baseline.

PRD §6.1: "Softmax over outcomes, sum-to-one enforced post-hoc."

In practice the caller supplies per-outcome raw priors (from market mids,
head-to-head comps, base rates, etc.). We normalize them so they sum to 1
and return this market's share.
"""

from __future__ import annotations

from ..types import BaselineSource
from .base import BaselineInputs, BaselineOutput


def multi_outcome_baseline(inp: BaselineInputs) -> BaselineOutput:
    raw = inp.multi_outcome_raw
    idx = inp.multi_outcome_self_index

    if raw is None or idx is None:
        return BaselineOutput(
            probability=None,
            source=BaselineSource.SOFTMAX,
            uncertainty_multiplier=1.5,
            reasons=["missing multi-outcome siblings"],
        )
    if idx < 0 or idx >= len(raw):
        return BaselineOutput(
            probability=None,
            source=BaselineSource.SOFTMAX,
            uncertainty_multiplier=1.5,
            reasons=[f"self_index {idx} out of range (n={len(raw)})"],
        )
    if len(raw) < 2:
        return BaselineOutput(
            probability=None,
            source=BaselineSource.SOFTMAX,
            uncertainty_multiplier=1.5,
            reasons=["fewer than 2 sibling outcomes"],
        )

    clipped = [max(0.0, float(x)) for x in raw]
    total = sum(clipped)
    if total <= 0:
        # All-zero priors → uniform over siblings.
        return BaselineOutput(
            probability=1.0 / len(clipped),
            source=BaselineSource.SOFTMAX,
            uncertainty_multiplier=1.5,
            reasons=["degenerate zero-sum priors; uniform fallback"],
        )

    prob = clipped[idx] / total
    return BaselineOutput(
        probability=prob,
        source=BaselineSource.SOFTMAX,
        uncertainty_multiplier=1.2,
        reasons=[f"normalized {len(clipped)} siblings; raw_sum={total:.4f}"],
    )
