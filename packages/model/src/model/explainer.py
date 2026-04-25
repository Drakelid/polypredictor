"""Simple explanation utilities for per-prediction feature attribution.

This module provides lightweight, dependency-free helper functions to
approximate SHAP-like values for a given model and input sample. In
production the ensemble models shipped with PolyPredictor expose an
``explain_prediction`` method (see ``packages/model/src/model/ensemble.py``)
that returns a ``PredictionExplanation`` containing per-feature
contributions. Rather than pulling in the heavy SHAP library or
duplicating logic, these helpers simply delegate to that method when
available.

The returned values are **not** mathematically identical to formal
SHAP values; they are deterministic contributions from the linear
stack and boosted stumps used in the ensemble. For the purposes of
the dashboard and API, these contributions provide intuitive insight
into which features drove the model’s probability on a single market.
Future milestones may replace these stubs with true SHAP extraction.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def extract_shap_values(sample: Any, model: Any) -> Dict[str, float]:
    """Return per-feature contribution values for a single prediction.

    This function is a thin wrapper around a model’s ``explain_prediction``
    method. If the given ``model`` implements an ``explain_prediction``
    method that accepts ``sample`` and returns an object with a
    ``contributions`` attribute (iterable of objects with ``feature_name``
    and ``score_contribution`` attributes), the contributions are
    converted into a ``dict`` keyed by feature name. Otherwise an empty
    dictionary is returned.

    Parameters
    ----------
    sample: Any
        The model input on which to compute contributions. In PolyPredictor
        this will typically be an instance of ``EnsembleSample`` but any
        object is accepted as this stub does not enforce a strict type.
    model: Any
        A model object that may implement ``explain_prediction(sample)``.

    Returns
    -------
    Dict[str, float]
        A mapping from feature names to their raw score contributions. If
        no contributions are available an empty dict is returned.
    """
    # Delegate to a model-specific explanation method when available
    explain_fn = getattr(model, "explain_prediction", None)
    if explain_fn is None:
        return {}
    try:
        explanation = explain_fn(sample)  # type: ignore[call-arg]
    except Exception:
        # Best effort: if the model throws, return no values
        return {}
    contributions = getattr(explanation, "contributions", None)
    if contributions is None:
        return {}
    out: Dict[str, float] = {}
    for item in contributions:
        # Each item should have feature_name and score_contribution attributes
        name = getattr(item, "feature_name", None)
        contribution = getattr(item, "score_contribution", None)
        if isinstance(name, str) and isinstance(contribution, (int, float)):
            out[name] = float(contribution)
    return out


def top_n_drivers(sample: Any, model: Any, n: int = 3) -> List[Tuple[str, float]]:
    """Return the top ``n`` features ranked by absolute contribution.

    This helper sorts the contributions returned by
    :func:`extract_shap_values` by their absolute value and returns the top
    ``n`` pairs of feature name and contribution. The intention is to
    surface the most influential drivers behind a model prediction. If
    fewer than ``n`` contributions are available, all are returned.

    Parameters
    ----------
    sample: Any
        The input sample used for the prediction.
    model: Any
        The predictive model with an ``explain_prediction`` method.
    n: int, default 3
        The number of top drivers to return.

    Returns
    -------
    List[Tuple[str, float]]
        A list of tuples ``(feature_name, contribution)`` sorted by the
        absolute magnitude of the contribution in descending order. The raw
        contribution value (which may be positive or negative) is returned
        so that consumers can display both the magnitude and sign.
    """
    shap_values = extract_shap_values(sample, model)
    # Sort by absolute contribution descending
    sorted_items = sorted(
        shap_values.items(), key=lambda item: abs(item[1]), reverse=True
    )
    return sorted_items[:n]


def summarize_top_drivers(sample: Any, model: Any, n: int = 3) -> List[str]:
    """Return simple natural-language explanations for the top drivers.

    This helper builds human-readable sentences describing how the top ``n``
    features contribute to a prediction. Positive contributions are phrased
    as increasing the probability, while negative contributions decrease it.
    The contribution magnitudes are formatted as percentage points (e.g.
    ``+2.3bp``). This deterministic summarization acts as a placeholder
    for the constrained-LM narrator planned in future milestones.

    Parameters
    ----------
    sample: Any
        The input sample on which contributions are computed.
    model: Any
        The predictive model with an ``explain_prediction`` method.
    n: int, default 3
        The number of top drivers to include in the summary.

    Returns
    -------
    List[str]
        A list of explanatory sentences, one per driver. If no drivers are
        available an empty list is returned.
    """
    drivers = top_n_drivers(sample, model, n=n)
    summaries: List[str] = []
    for name, contrib in drivers:
        # Format contribution as basis points (multiply by 100 for percent, then 100 for bps)
        # But contributions are in logit score space; we keep raw numeric value for now.
        direction = "increased" if contrib >= 0 else "decreased"
        formatted = f"{contrib:+.2f}"
        summaries.append(
            f"Feature '{name}' {direction} the model's score by {formatted}."
        )
    return summaries