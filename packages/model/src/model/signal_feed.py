"""
Signal feed aggregation helpers.

Polypredictor surfaces actionable events (signals) to users via a feed. A
production system would ingest events from multiple pipelines (social, on‑chain,
macro, model outputs), rank them by significance, and throttle them to avoid
noise. This module implements minimal helpers to filter and rank events for
development and testing. Each event is assumed to be represented as a
dictionary with a numeric ``score`` field indicating relevance.

Functions:

* :func:`is_actionable` – determine if an event meets a minimum score
  threshold.
* :func:`select_actionable_events` – return the top N actionable events.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple


def is_actionable(event: Dict[str, object], threshold: float = 0.5) -> bool:
    """Return True if the event’s score exceeds the threshold.

    Parameters
    ----------
    event:
        Dictionary containing at least a ``score`` key with a numeric value.
    threshold:
        Minimum score required for an event to be considered actionable.

    Returns
    -------
    bool
        True if the event’s score is greater than or equal to the threshold.
    """
    try:
        return float(event.get("score", 0.0)) >= threshold
    except (TypeError, ValueError):
        return False


def select_actionable_events(
    events: Iterable[Dict[str, object]], limit: int = 10, threshold: float = 0.5
) -> List[Dict[str, object]]:
    """Filter and return the top ``limit`` events by score.

    Parameters
    ----------
    events:
        Iterable of event dictionaries, each with a ``score`` key.
    limit:
        Maximum number of events to return.
    threshold:
        Minimum score to qualify as actionable.

    Returns
    -------
    List[Dict[str, object]]
        Sorted list of actionable events, highest score first.
    """
    actionable: List[Tuple[float, Dict[str, object]]] = []
    for event in events:
        try:
            score = float(event.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        if score >= threshold:
            actionable.append((score, event))
    # Sort descending by score
    actionable.sort(key=lambda x: x[0], reverse=True)
    return [event for _, event in actionable[:limit]]