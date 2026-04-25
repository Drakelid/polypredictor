"""LLM-assisted market-type classifier with human-review queue.

This module provides a thin wrapper around the deterministic
`classify` function defined in :mod:`classifier`. It preserves the
existing heuristics for obvious cases, and for low-confidence or
``MISC`` classifications it enqueues the market for human review and
returns the original classification result. The intention is that
future releases will integrate an actual LLM call here to provide
better coverage on ambiguous markets. For now the implementation is
explicit about deferring to human judgment and surfaces a reason
explaining why the LLM stub was invoked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .classifier import classify, ClassificationResult


# In-memory queue of markets requiring human review.  In a real system
# this might be a database table or message queue.  For the purposes
# of the stub it is simply a list of dictionaries capturing the
# original classification inputs and result.
HUMAN_REVIEW_QUEUE: List[Dict[str, Any]] = []


def classify_with_llm(
    *,
    question: str,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
    resolution_source: Optional[str] = None,
    end_date: Optional[datetime] = None,
    outcomes: Optional[List[str]] = None,
    multi_outcome_sibling_count: int = 0,
) -> ClassificationResult:
    """Classify a market using deterministic rules and invoke an LLM stub when ambiguous.

    The function first delegates to :func:`classifier.classify`.  If the
    resulting classification is confident enough (confidence >= 0.55 and
    market_type is not ``MISC``) it returns the result unchanged.  When
    the classifier is uncertain or falls back to ``MISC`` the market
    inputs and preliminary result are added to the :data:`HUMAN_REVIEW_QUEUE`
    and the returned result gains an additional reason noting that an
    LLM-assisted review has been requested.

    Parameters mirror those of the underlying :func:`classifier.classify`.
    """
    result = classify(
        question=question,
        description=description,
        tags=tags,
        resolution_source=resolution_source,
        end_date=end_date,
        outcomes=outcomes,
        multi_outcome_sibling_count=multi_outcome_sibling_count,
    )

    # If the deterministic classifier is confident and not misc, return immediately.
    if not result.needs_review:
        return result

    # Otherwise, enqueue for human review.  Record the inputs and the
    # preliminary classification to allow manual triage later.  A real
    # implementation might also trigger an asynchronous LLM call here to
    # refine the result before human intervention.
    HUMAN_REVIEW_QUEUE.append(
        {
            "question": question,
            "description": description,
            "tags": tags,
            "resolution_source": resolution_source,
            "end_date": end_date,
            "outcomes": outcomes,
            "multi_outcome_sibling_count": multi_outcome_sibling_count,
            "preliminary_result": result,
        }
    )

    # Augment the reasons to indicate that the LLM stub was invoked.  We do
    # not modify the market_type so downstream code remains consistent.
    augmented_reasons = list(result.reasons)
    augmented_reasons.append("LLM stub invoked — queued for human review")

    return ClassificationResult(
        market_type=result.market_type,
        confidence=result.confidence,
        features=result.features,
        reasons=augmented_reasons,
    )


def get_review_queue() -> List[Dict[str, Any]]:
    """Return a copy of the current human review queue.

    Exposing a copy rather than the underlying list prevents callers
    from mutating the queue outside of this module.  In a production
    implementation this accessor would instead issue a database query or
    consume from a message queue.
    """
    return HUMAN_REVIEW_QUEUE.copy()
