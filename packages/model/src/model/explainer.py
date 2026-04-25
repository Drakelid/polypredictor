"""Per-prediction SHAP utilities.

The PolyPredictor ensemble (``packages/model/src/model/ensemble.py``) is
additive: a bounded linear logit stack plus a sum of independent gradient
boost stumps. For additive models the per-feature SHAP value collapses
to ``contribution(x) - E_X[contribution(X)]``; the linear stack is
already mean-centered (so its raw contribution equals its SHAP value)
and each stump records the training-set ``left_fraction`` it was fit on
so its expected contribution is computable at serve time.

These helpers prefer the model's :meth:`explain_prediction_shap` when
available (true additive SHAP) and fall back to ``explain_prediction``
for legacy callers that just want raw contributions.
"""

from __future__ import annotations

from typing import Any


def extract_shap_values(sample: Any, model: Any) -> dict[str, float]:
    """Return per-feature SHAP contributions for a single prediction.

    Prefers the model's :meth:`explain_prediction_shap` (additive SHAP via
    per-stump training-fraction memory — see
    :class:`model.ensemble.GradientBoostStump`) when available; falls back
    to :meth:`explain_prediction` for legacy or non-additive models that
    only expose raw contributions.

    Parameters
    ----------
    sample: Any
        The model input on which to compute contributions. Typically an
        :class:`EnsembleSample` but any object the model accepts is fine.
    model: Any
        A model exposing ``explain_prediction_shap`` and/or
        ``explain_prediction``.

    Returns
    -------
    Dict[str, float]
        Feature name to SHAP value mapping. Empty when neither method
        produces a useful explanation.
    """
    explain_fn = getattr(model, "explain_prediction_shap", None) or getattr(
        model, "explain_prediction", None
    )
    if explain_fn is None:
        return {}
    try:
        explanation = explain_fn(sample)  # type: ignore[call-arg]
    except Exception:
        return {}
    contributions = getattr(explanation, "contributions", None)
    if contributions is None:
        return {}
    out: dict[str, float] = {}
    for item in contributions:
        name = getattr(item, "feature_name", None)
        contribution = getattr(item, "score_contribution", None)
        if isinstance(name, str) and isinstance(contribution, (int, float)):
            out[name] = float(contribution)
    return out


def top_n_drivers(sample: Any, model: Any, n: int = 3) -> list[tuple[str, float]]:
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


def summarize_top_drivers(sample: Any, model: Any, n: int = 3) -> list[str]:
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
    summaries: list[str] = []
    for name, contrib in drivers:
        # Format contribution as basis points (multiply by 100 for percent, then 100 for bps)
        # But contributions are in logit score space; we keep raw numeric value for now.
        direction = "increased" if contrib >= 0 else "decreased"
        formatted = f"{contrib:+.2f}"
        summaries.append(
            f"Feature '{name}' {direction} the model's score by {formatted}."
        )
    return summaries