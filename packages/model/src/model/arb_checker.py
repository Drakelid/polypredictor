"""
Arbitrage checks for prediction markets.

In binary and multi‑outcome prediction markets, the sum of the probabilities
across all mutually exclusive outcomes should equal 1.0 under a no‑arbitrage
assumption. Real markets may deviate due to liquidity, fees or rounding; a
simple checker helps flag instances where the deviation exceeds a tolerance.

This module provides helper functions to compute whether a set of prices
(interpreted as probabilities) satisfies the no‑arbitrage condition and to
quantify the magnitude of any violation.
"""

from __future__ import annotations

from typing import Iterable


def no_arb_violated(probabilities: Iterable[float], tolerance: float = 0.01) -> bool:
    """Return True if the sum of probabilities deviates from 1 beyond tolerance.

    Parameters
    ----------
    probabilities:
        An iterable of outcome probabilities (e.g. midpoints of yes/no shares).
    tolerance:
        The acceptable deviation from 1.0. If the absolute difference between
        ``sum(probabilities)`` and 1.0 exceeds this value, the market is
        flagged as violating the no‑arbitrage condition.

    Returns
    -------
    bool
        True if no‑arbitrage appears to be violated, False otherwise.
    """
    total = 0.0
    count = 0
    for p in probabilities:
        try:
            total += float(p)
            count += 1
        except (TypeError, ValueError):
            continue
    if count == 0:
        return False
    return abs(total - 1.0) > tolerance


def arb_deviation(probabilities: Iterable[float]) -> float:
    """Return the difference between the sum of probabilities and 1.0.

    A positive value means the market is overpriced (sum > 1), while a negative
    value means it is underpriced (sum < 1). The magnitude of this value
    reflects the potential arbitrage opportunity in the idealized frictionless
    case.

    Parameters
    ----------
    probabilities:
        An iterable of outcome probabilities.

    Returns
    -------
    float
        The signed deviation from 1.0.
    """
    total = 0.0
    for p in probabilities:
        try:
            total += float(p)
        except (TypeError, ValueError):
            continue
    return total - 1.0