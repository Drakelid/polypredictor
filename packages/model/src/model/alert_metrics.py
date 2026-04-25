"""
Alert metrics helper functions.

This module computes simple diagnostic metrics for alert systems, such as
false positive rate. These functions are meant to support testing and
analysis of the edge-alert subsystem (§8.5).
"""

from __future__ import annotations

from typing import Iterable, Tuple


def false_positive_rate(false_positives: int, total_alerts: int) -> float:
    """Return the false positive rate given counts.

    The false positive rate is defined as ``false_positives / total_alerts``.
    If ``total_alerts`` is zero, NaN is returned.

    Parameters
    ----------
    false_positives:
        Number of alerts that turned out to be false positives.
    total_alerts:
        Total number of alerts triggered.

    Returns
    -------
    float
        False positive rate in [0, 1], or NaN if undefined.
    """
    try:
        fp = int(false_positives)
        tot = int(total_alerts)
    except (TypeError, ValueError):
        return float('nan')
    if tot == 0:
        return float('nan')
    return fp / tot