"""
Metric helpers for scoring prediction markets.

This module offers lightweight functions to compute common performance metrics
such as Brier scores and conformal interval coverage. These helpers can be
used during development and testing to verify that model outputs align with
product specifications, and they serve as placeholders until full audit
pipelines are available.

Functions:

* :func:`brier_score` – compute the squared error between a probability and
  the binary outcome.
* :func:`mean_brier_score` – compute the average Brier score over multiple
  predictions.
* :func:`conformal_coverage` – compute the fraction of actual values that lie
  within predicted intervals.
"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple


def brier_score(probability: float, outcome: int) -> float:
    """Return the Brier score for a single probability/outcome pair.

    The Brier score is the squared difference between a predicted probability
    and the actual outcome (0 or 1). Lower scores indicate better calibrated
    predictions.

    Parameters
    ----------
    probability:
        Predicted probability of the event occurring (0 ≤ p ≤ 1).
    outcome:
        Actual outcome (1 for YES, 0 for NO).

    Returns
    -------
    float
        The squared error between ``probability`` and ``outcome``.
    """
    try:
        p = float(probability)
        o = 1.0 if outcome else 0.0
    except (TypeError, ValueError):
        return float('nan')
    return (p - o) ** 2


def mean_brier_score(
    probabilities: Iterable[float], outcomes: Iterable[int]
) -> float:
    """Compute the mean Brier score over multiple predictions.

    Parameters
    ----------
    probabilities:
        Iterable of predicted probabilities for each event.
    outcomes:
        Iterable of binary outcomes corresponding to the predictions.

    Returns
    -------
    float
        The average Brier score. Returns NaN if there are no valid pairs.
    """
    total = 0.0
    count = 0
    for p, o in zip(probabilities, outcomes):
        try:
            score = brier_score(p, int(o))
            if score == score:  # ignore NaNs
                total += score
                count += 1
        except Exception:
            continue
    return total / count if count else float('nan')


def conformal_coverage(
    intervals: Iterable[Tuple[float, float]],
    actuals: Iterable[float],
) -> float:
    """Compute the fraction of actual values contained within predicted intervals.

    Each interval is a tuple ``(lower, upper)``. An actual value is
    considered covered if ``lower ≤ value ≤ upper``. The function returns
    the proportion of pairs where the actual lies within the interval. NaN
    values are ignored in both intervals and actuals.

    Parameters
    ----------
    intervals:
        Iterable of tuples specifying lower and upper bounds of prediction
        intervals.
    actuals:
        Iterable of actual realized values.

    Returns
    -------
    float
        The coverage ratio in [0, 1]. Returns NaN if there are no valid
        comparisons.
    """
    covered = 0
    total = 0
    for (lower, upper), actual in zip(intervals, actuals):
        try:
            l = float(lower)
            u = float(upper)
            a = float(actual)
        except (TypeError, ValueError):
            continue
        if l <= a <= u:
            covered += 1
        total += 1
    return covered / total if total else float('nan')