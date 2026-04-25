"""
Helpers for evaluating incremental improvements in model performance.

When adding new feature families to an ensemble, the ablation criterion is
that the addition should yield non-zero incremental Brier skill (§4.2, §8.5).
This module provides a function to compute the difference between a baseline
Brier score and a new Brier score. Positive improvements correspond to a
lower Brier score (i.e. better calibration). No evaluation of statistical
significance is performed here.
"""

from __future__ import annotations


def brier_skill_improvement(base_brier: float, new_brier: float) -> float:
    """Return the change in Brier score between a base model and a new model.

    A positive result indicates that the new model has a lower Brier score and
    thus better calibration. A negative result indicates worse calibration.

    Parameters
    ----------
    base_brier:
        Baseline Brier score.
    new_brier:
        Brier score of the model with additional features.

    Returns
    -------
    float
        Difference ``base_brier - new_brier``.
    """
    try:
        return float(base_brier) - float(new_brier)
    except (TypeError, ValueError):
        return float('nan')