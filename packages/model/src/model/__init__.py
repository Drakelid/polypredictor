"""PolyPredictor model layer — classifier + principled baselines (PRD §6).

The layering used throughout this package:

  1. :mod:`model.classifier` assigns a :class:`MarketType` to every market.
  2. :mod:`model.baselines` computes a type-specific prior ``p_base``.
  3. (M2+) a gradient-boosted refinement consumes ``p_base`` + features and
     outputs a calibrated probability. Not implemented here.

M1 deliverable stops at step 2 — baselines drive the displayed probability
until the ML ensemble lands.
"""

from .baselines import (
    BaselineInputs,
    BaselineOutput,
    discrete_event_baseline,
    long_tail_baseline,
    multi_outcome_baseline,
    range_baseline,
    threshold_baseline,
)
from .classifier import ClassificationResult, MarketFeatures, classify
from .pipeline import PipelineResult, probability_for_market
from .types import BaselineSource, MarketType

__all__ = [
    "BaselineInputs",
    "BaselineOutput",
    "BaselineSource",
    "ClassificationResult",
    "MarketFeatures",
    "MarketType",
    "PipelineResult",
    "classify",
    "discrete_event_baseline",
    "long_tail_baseline",
    "multi_outcome_baseline",
    "probability_for_market",
    "range_baseline",
    "threshold_baseline",
]
