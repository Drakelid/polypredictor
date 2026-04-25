"""
Helpers for decision time metrics.

The product metrics (§8) include a goal that the median time from signal
generation to user decision be less than five minutes. This module provides a
function to compute the median of a list of durations and can be used in
backtests or simulations to assess the metric. It does not perform any
scheduling or timing logic itself.
"""

from __future__ import annotations

import statistics
from typing import Iterable


def median_time_to_decision(durations: Iterable[float]) -> float:
    """Return the median of the supplied decision durations (in minutes).

    Parameters
    ----------
    durations:
        Iterable of decision times in minutes. Non-numeric entries are
        ignored.

    Returns
    -------
    float
        The median decision time, or NaN if there are no valid values.
    """
    valid = []
    for d in durations:
        try:
            valid.append(float(d))
        except (TypeError, ValueError):
            continue
    return statistics.median(valid) if valid else float('nan')