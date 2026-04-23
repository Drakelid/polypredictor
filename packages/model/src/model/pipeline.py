"""Classifier → baseline dispatcher.

Single entry point :func:`probability_for_market`. Consumers (API, backtest
harness) pass in whatever inputs they have; the pipeline picks the right
baseline and returns a :class:`PipelineResult` with provenance.
"""

from __future__ import annotations

from dataclasses import dataclass

from .baselines import (
    BaselineInputs,
    BaselineOutput,
    discrete_event_baseline,
    long_tail_baseline,
    multi_outcome_baseline,
    range_baseline,
    threshold_baseline,
)
from .classifier import ClassificationResult
from .types import BaselineSource, MarketType


@dataclass(frozen=True)
class PipelineResult:
    classification: ClassificationResult
    baseline: BaselineOutput
    # Probability the UI should display. Matches baseline.probability when
    # available; falls back to market_mid (and source becomes MARKET_MID).
    displayed_probability: float | None
    displayed_source: BaselineSource

    @property
    def edge_bps(self) -> float | None:
        """Model − market, in basis points. ``None`` if either side is missing."""
        p = self.displayed_probability
        m = self._mid
        if p is None or m is None:
            return None
        return (p - m) * 10_000.0

    # ``_mid`` is set on init via object.__setattr__ (frozen dataclass trick).
    _mid: float | None = None


def probability_for_market(
    classification: ClassificationResult,
    inputs: BaselineInputs,
) -> PipelineResult:
    mt = classification.market_type
    if mt is MarketType.THRESHOLD:
        baseline = threshold_baseline(inputs, mode="one_touch")
    elif mt is MarketType.RANGE:
        baseline = range_baseline(inputs)
    elif mt is MarketType.DISCRETE_EVENT:
        baseline = discrete_event_baseline(inputs)
    elif mt is MarketType.MULTI_OUTCOME:
        baseline = multi_outcome_baseline(inputs)
    elif mt is MarketType.LONG_TAIL_BINARY:
        baseline = long_tail_baseline(inputs)
    else:
        baseline = BaselineOutput(
            probability=None,
            source=BaselineSource.MARKET_MID,
            uncertainty_multiplier=2.0,
            reasons=["MISC type; no principled baseline"],
        )

    if baseline.probability is not None:
        displayed = baseline.probability
        source = baseline.source
    elif inputs.market_mid is not None:
        displayed = inputs.market_mid
        source = BaselineSource.MARKET_MID
    else:
        displayed = None
        source = baseline.source

    result = PipelineResult(
        classification=classification,
        baseline=baseline,
        displayed_probability=displayed,
        displayed_source=source,
    )
    # Set the read-only mid accessor.
    object.__setattr__(result, "_mid", inputs.market_mid)
    return result
