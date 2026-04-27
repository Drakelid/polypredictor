"""LLM-assisted market-type classifier with pluggable review queue.

The deterministic rules in :mod:`model.classifier` are high-precision but
flag low-confidence / MISC outputs via :attr:`ClassificationResult.needs_review`.
This module wraps that classifier so an operator can:

1. Optionally pre-fill an LLM-suggested market_type for ambiguous cases.
   The :class:`LlmCallable` Protocol keeps the model package free of any
   LLM SDK dependency — operators wire Anthropic / OpenAI / local models
   from the API layer at request time.
2. Optionally route ambiguous markets into a persistent review queue. The
   :class:`QueueWriter` Protocol decouples the queue implementation; the
   API service uses :func:`api.classification_review.enqueue_for_review`
   in production.

Both extension points are optional. With neither configured, the function
collapses to the underlying deterministic classifier — no side effects,
no global state. The previously-exported in-memory ``HUMAN_REVIEW_QUEUE``
list is retained for backwards compatibility but is no longer the canonical
queue (it's now a documented test-only helper).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .classifier import ClassificationResult, classify
from .types import MarketType

# Public for backwards compatibility — kept as an in-memory test sink. Real
# enqueueing goes through the QueueWriter protocol.
HUMAN_REVIEW_QUEUE: list[dict[str, Any]] = []


@dataclass(frozen=True)
class LlmSuggestion:
    market_type: MarketType
    confidence: float
    rationale: str


# Operator-pluggable LLM call. Must return None when no suggestion is
# available (rate-limited, low confidence, etc.); the caller treats None
# as "fall back to deterministic result".
LlmCallable = Callable[[ClassificationResult, dict[str, Any]], LlmSuggestion | None]


# Queue writer side effect. The API service implements this via
# api.classification_review.enqueue_for_review; tests can supply a fake.
QueueWriter = Callable[[dict[str, Any]], Awaitable[str | None]]


def classify_with_llm(
    *,
    question: str,
    description: str | None = None,
    tags: Sequence[str] | None = None,
    resolution_source: str | None = None,
    end_date: datetime | None = None,
    outcomes: Sequence[str] | None = None,
    multi_outcome_sibling_count: int = 0,
    llm_callable: LlmCallable | None = None,
    record_in_test_sink: bool = False,
) -> ClassificationResult:
    """Classify a market and refine ambiguous cases via an optional LLM.

    Steps:
      1. Run the deterministic classifier.
      2. Return immediately when the result is confident (``not needs_review``).
      3. If ``llm_callable`` is provided, ask it for a suggestion. When the
         suggestion's confidence beats the deterministic confidence, return
         a refined :class:`ClassificationResult` carrying the LLM's market
         type plus a reason recording the LLM provenance.
      4. Otherwise return the deterministic result, augmented with a reason
         documenting that the LLM was unavailable / declined.

    The function is pure and side-effect-free unless ``record_in_test_sink``
    is set, in which case the inputs and preliminary result are appended to
    :data:`HUMAN_REVIEW_QUEUE` so unit tests can introspect the queue.
    Persistent queueing is the API service's responsibility.
    """
    deterministic = classify(
        question=question,
        description=description,
        tags=list(tags) if tags is not None else None,
        resolution_source=resolution_source,
        end_date=end_date,
        outcomes=list(outcomes) if outcomes is not None else None,
        multi_outcome_sibling_count=multi_outcome_sibling_count,
    )

    if not deterministic.needs_review:
        return deterministic

    inputs = {
        "question": question,
        "description": description,
        "tags": list(tags) if tags is not None else None,
        "resolution_source": resolution_source,
        "end_date": end_date,
        "outcomes": list(outcomes) if outcomes is not None else None,
        "multi_outcome_sibling_count": multi_outcome_sibling_count,
        "preliminary_result": deterministic,
    }

    if record_in_test_sink:
        HUMAN_REVIEW_QUEUE.append(inputs)

    if llm_callable is None:
        return _augment_reasons(
            deterministic,
            ["LLM not configured; deterministic classification flagged for human review"],
        )

    try:
        suggestion = llm_callable(deterministic, inputs)
    except Exception as exc:
        return _augment_reasons(
            deterministic,
            [f"LLM callable raised {exc.__class__.__name__}; falling back to deterministic"],
        )

    if suggestion is None:
        return _augment_reasons(
            deterministic,
            ["LLM declined to refine; preserving deterministic result"],
        )

    if suggestion.confidence <= deterministic.confidence:
        return _augment_reasons(
            deterministic,
            [
                f"LLM suggested {suggestion.market_type.value} at {suggestion.confidence:.2f} "
                f"but did not beat deterministic confidence {deterministic.confidence:.2f}"
            ],
        )

    return ClassificationResult(
        market_type=suggestion.market_type,
        confidence=suggestion.confidence,
        features=deterministic.features,
        reasons=[
            *deterministic.reasons,
            f"LLM refinement: {suggestion.market_type.value} "
            f"(confidence {suggestion.confidence:.2f}). {suggestion.rationale}",
        ],
    )


def _augment_reasons(
    result: ClassificationResult, extra: Sequence[str]
) -> ClassificationResult:
    return ClassificationResult(
        market_type=result.market_type,
        confidence=result.confidence,
        features=result.features,
        reasons=[*result.reasons, *extra],
    )


def get_review_queue() -> list[dict[str, Any]]:
    """Return a copy of the in-memory test sink.

    The persistent queue lives in Postgres; use
    :func:`api.classification_review.list_pending_reviews` for the
    production view. This helper exists to keep legacy callers (test
    harnesses) compiling.
    """
    return list(HUMAN_REVIEW_QUEUE)


def reset_review_queue() -> None:
    """Clear the in-memory test sink."""
    HUMAN_REVIEW_QUEUE.clear()


__all__ = [
    "HUMAN_REVIEW_QUEUE",
    "LlmCallable",
    "LlmSuggestion",
    "QueueWriter",
    "classify_with_llm",
    "get_review_queue",
    "reset_review_queue",
]
