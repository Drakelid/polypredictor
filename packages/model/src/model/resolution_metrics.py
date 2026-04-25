"""
Resolution risk evaluation metrics.

To assess the performance of the resolution-risk classifier (§4.4), we need
basic metrics such as precision and recall on labelled datasets (e.g. UMA
dispute history). This module implements simple precision and recall
calculations for binary classification tasks.
"""

from __future__ import annotations

from typing import Iterable, Tuple


def precision_recall(
    predictions: Iterable[int],
    truths: Iterable[int],
) -> Tuple[float, float]:
    """Compute precision and recall for binary classification.

    Parameters
    ----------
    predictions:
        Iterable of predicted labels (1 for positive class, 0 for negative).
    truths:
        Iterable of true labels (1 for positive class, 0 for negative).

    Returns
    -------
    Tuple[float, float]
        (precision, recall). If there are no predicted positives, precision is
        NaN. If there are no true positives, recall is NaN.
    """
    tp = 0
    fp = 0
    fn = 0
    for pred, truth in zip(predictions, truths):
        try:
            p = 1 if int(pred) else 0
            t = 1 if int(truth) else 0
        except (TypeError, ValueError):
            continue
        if p == 1 and t == 1:
            tp += 1
        elif p == 1 and t == 0:
            fp += 1
        elif p == 0 and t == 1:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else float('nan')
    recall = tp / (tp + fn) if (tp + fn) > 0 else float('nan')
    return precision, recall