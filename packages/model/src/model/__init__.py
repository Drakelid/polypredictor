"""PolyPredictor model layer: classifier, baselines, and ensemble logic.

The layering used throughout this package:

  1. :mod:`model.classifier` assigns a :class:`MarketType` to every market.
  2. :mod:`model.baselines` computes a type-specific prior ``p_base``.
  3. :mod:`model.ensemble` refines ``p_base`` with persisted microstructure
     features and a per-type calibrator.

The live API still serves the baseline path today; the ensemble machinery is
implemented here and can be wired into serving once API/UI provenance extends
beyond the current baseline-source badge.
"""

from .baselines import (
    BaselineInputs,
    BaselineOutput,
    discrete_event_baseline,
    long_tail_baseline,
    multi_outcome_baseline,
    range_baseline,
    threshold_baseline,
)
from .classifier import ClassificationResult, MarketFeatures, classify
from .conformal import (
    ConformalCell,
    ConformalSample,
    PurgedFold,
    SplitConformalRegistry,
    fit_split_conformal,
    fit_split_conformal_from_folds,
    mondrian_key,
    purged_embargo_splits,
    ttr_bucket,
)
from .deribit import DeribitClient, DeribitIV
from .ensemble import (
    BOOSTER_FEATURE_NAMES,
    LINEAR_FEATURE_NAMES,
    EnsembleRegistry,
    EnsembleSample,
    FeatureContribution,
    GradientBoostStump,
    IsotonicCalibrator,
    PredictionExplanation,
    TypeEnsembleModel,
    fit_per_type_ensembles,
)
from .pipeline import PipelineResult, probability_for_market
from .types import BaselineSource, MarketType

__all__ = [
    "BOOSTER_FEATURE_NAMES",
    "LINEAR_FEATURE_NAMES",
    "BaselineInputs",
    "BaselineOutput",
    "BaselineSource",
    "ClassificationResult",
    "ConformalCell",
    "ConformalSample",
    "DeribitClient",
    "DeribitIV",
    "EnsembleRegistry",
    "EnsembleSample",
    "FeatureContribution",
    "GradientBoostStump",
    "IsotonicCalibrator",
    "MarketFeatures",
    "MarketType",
    "PipelineResult",
    "PredictionExplanation",
    "PurgedFold",
    "SplitConformalRegistry",
    "TypeEnsembleModel",
    "classify",
    "discrete_event_baseline",
    "fit_per_type_ensembles",
    "fit_split_conformal",
    "fit_split_conformal_from_folds",
    "long_tail_baseline",
    "mondrian_key",
    "multi_outcome_baseline",
    "probability_for_market",
    "purged_embargo_splits",
    "range_baseline",
    "threshold_baseline",
    "ttr_bucket",
]
