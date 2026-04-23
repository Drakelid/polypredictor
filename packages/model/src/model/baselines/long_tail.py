"""Long-tail binary baseline.

PRD §6.1: "Poisson / base-rate from historical frequency of analogous events."
PRD §6.7 cold-start: "Embedding match — nearest resolved markets by cosine
similarity, weighted average of their realized outcomes becomes a prior."

For M1 we consume whichever priors the pipeline supplies:

  1. If an embedding-match prior is available, use it.
  2. Else if a historical base rate is available, use it.
  3. Else return None — caller falls back to market mid.

Both priors arrive pre-computed (the embedding store + base-rate table are
M4/M6 deliverables). For M1 the baseline is purely a thin shim; the value
is in having the interface nailed down so downstream work slots in cleanly.
"""

from __future__ import annotations

from ..types import BaselineSource
from .base import BaselineInputs, BaselineOutput


def long_tail_baseline(inp: BaselineInputs) -> BaselineOutput:
    if inp.embedding_prior is not None:
        return BaselineOutput(
            probability=_clip(inp.embedding_prior),
            source=BaselineSource.EMBEDDING_MATCH,
            uncertainty_multiplier=1.5,
            reasons=["nearest-resolved embedding prior"],
        )
    if inp.historical_base_rate is not None:
        return BaselineOutput(
            probability=_clip(inp.historical_base_rate),
            source=BaselineSource.BASE_RATE,
            uncertainty_multiplier=1.5,
            reasons=["historical base rate"],
        )
    return BaselineOutput(
        probability=None,
        source=BaselineSource.UNIFORM,
        uncertainty_multiplier=2.0,
        reasons=["no embedding or base-rate prior available"],
    )


def _clip(p: float) -> float:
    # Long-tail priors can arrive as ad-hoc blends. Clamp to a sensible open
    # interval so log-odds math downstream doesn't blow up on 0 or 1.
    return min(0.995, max(0.005, float(p)))
