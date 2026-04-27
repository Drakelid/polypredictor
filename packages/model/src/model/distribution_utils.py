"""
Utilities for summarizing continuous market distributions.

Continuous prediction markets produce distributions rather than single
probabilities. To surface those signals in the UI or feed them into
binary‑classification metrics, we often need to extract summary statistics or
translate distributions into probabilities relative to a threshold. This
module provides simple helpers for doing so.

Functions:

* :func:`mean_of_distribution` – compute the (optionally weighted) mean of a
  set of samples.
* :func:`distribution_to_probability` – given samples from a distribution
  and a threshold, estimate the probability that the variable exceeds the
  threshold.
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple


def mean_of_distribution(
    values: Iterable[float], weights: Optional[Iterable[float]] = None
) -> float:
    """Compute the mean of a distribution.

    Parameters
    ----------
    values:
        Samples from the distribution.
    weights:
        Optional weights for each sample. When omitted, the mean of ``values``
        is returned. If provided, must be the same length as ``values``.

    Returns
    -------
    float
        Weighted or unweighted mean of the samples. Returns NaN if no
        valid samples are provided.
    """
    total = 0.0
    weight_sum = 0.0
    if weights is None:
        count = 0
        for v in values:
            try:
                total += float(v)
                count += 1
            except (TypeError, ValueError):
                continue
        return total / count if count else float('nan')
    for v, w in zip(values, weights):
        try:
            fv = float(v)
            fw = float(w)
        except (TypeError, ValueError):
            continue
        total += fv * fw
        weight_sum += fw
    return total / weight_sum if weight_sum else float('nan')


def distribution_to_probability(
    samples: Iterable[float], threshold: float
) -> float:
    """Estimate the probability that a continuous variable exceeds a threshold.

    This helper treats the input ``samples`` as draws from an underlying
    distribution and estimates ``P(X > threshold)`` as the fraction of
    samples greater than the threshold. When no valid samples are provided,
    NaN is returned.

    Parameters
    ----------
    samples:
        An iterable of numeric samples.
    threshold:
        The threshold against which to compute the exceedance probability.

    Returns
    -------
    float
        Fraction of samples greater than ``threshold``, or NaN if no
        valid samples are present.
    """
    total = 0
    count = 0
    for s in samples:
        try:
            val = float(s)
        except (TypeError, ValueError):
            continue
        if val > threshold:
            total += 1
        count += 1
    return total / count if count else float('nan')


def percentile_grid(
    samples: list[float],
    n_points: int = 25,
) -> list[tuple[float, float]]:
    """Return a percentile grid from a list of samples.

    Given a list of numeric samples (e.g. Monte-Carlo draws from a
    distribution), return ``n_points`` evenly spaced (percentile, value)
    pairs spanning the range ``[1/(n_points+1), n_points/(n_points+1)]``
    so that the extreme quantiles are never exactly 0 or 1 (avoids
    ±∞ in logit space).

    Parameters
    ----------
    samples:
        Numeric samples from the distribution.  Must be non-empty.
    n_points:
        Number of quantile points to return (default 25).

    Returns
    -------
    list[tuple[float, float]]
        Sorted list of ``(percentile, value)`` pairs, or an empty list
        when ``samples`` is empty or contains no valid floats.

    Examples
    --------
    >>> grid = percentile_grid([0.1, 0.3, 0.5, 0.7, 0.9], n_points=3)
    >>> len(grid)
    3
    >>> grid[0][0] < grid[1][0] < grid[2][0]
    True
    """
    valid = sorted(float(v) for v in samples if v == v)  # filter NaN
    if not valid or n_points < 1:
        return []
    n = len(valid)
    step = 1.0 / (n_points + 1)
    grid: list[tuple[float, float]] = []
    for i in range(1, n_points + 1):
        pct = i * step
        # Linear interpolation between adjacent sorted values.
        idx_f = pct * (n - 1)
        lo = int(idx_f)
        hi = min(lo + 1, n - 1)
        frac = idx_f - lo
        value = valid[lo] + frac * (valid[hi] - valid[lo])
        grid.append((round(pct, 6), round(value, 6)))
    return grid