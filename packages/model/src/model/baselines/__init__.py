"""Principled baselines per market type (PRD §6.1).

Every baseline returns a :class:`BaselineOutput` with the probability, the
:class:`BaselineSource` that produced it, and a prediction-interval widener
for the conformal layer downstream. Baselines NEVER invent numbers from
partial inputs — missing required inputs must return a ``None`` probability,
not a guess. The pipeline falls back to :data:`BaselineSource.MARKET_MID`
in that case.
"""

from .base import BaselineInputs, BaselineOutput
from .discrete import discrete_event_baseline
from .long_tail import long_tail_baseline
from .multi import multi_outcome_baseline
from .range_ import range_baseline
from .threshold import threshold_baseline

__all__ = [
    "BaselineInputs",
    "BaselineOutput",
    "discrete_event_baseline",
    "long_tail_baseline",
    "multi_outcome_baseline",
    "range_baseline",
    "threshold_baseline",
]
