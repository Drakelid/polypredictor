"""PolyPredictor model layer: classifier, baselines, ensemble, and helpers.

The layering used throughout this package:

  1. :mod:`model.classifier` assigns a :class:`MarketType` to every market.
  2. :mod:`model.baselines` computes a type-specific prior ``p_base``.
  3. :mod:`model.ensemble` refines ``p_base`` with persisted microstructure
     features and a per-type calibrator.
  4. :mod:`model.conformal` produces split-conformal Mondrian intervals.
  5. :mod:`model.backtest` evaluates walk-forward replays.

Lightweight per-task helpers (signal feed, journal utilities, structured
sentiment, etc.) live as their own submodules and are re-exported here.
"""

from .ablation_metrics import brier_skill_improvement
from .alert_metrics import false_positive_rate
from .arb_checker import arb_deviation, no_arb_violated
from .backtest import (
    ROLLING_WINDOWS_DEFAULT,
    TTR_BUCKETS_DEFAULT,
    AutoDisableDecision,
    DailySkillSample,
    RollingWindowReport,
    StratumReport,
    WalkForwardReport,
    WalkForwardSample,
    brier_skill,
    expected_calibration_error,
    kl_divergence,
    population_stability_index,
    rolling_window_reports,
    should_auto_disable,
    walk_forward_evaluate,
)
from .baselines import (
    BaselineInputs,
    BaselineOutput,
    discrete_event_baseline,
    long_tail_baseline,
    multi_outcome_baseline,
    range_baseline,
    threshold_baseline,
)
from .changelog_watch import (
    ShapeDiff,
    compare_response_shape,
    diff_shapes,
    is_newer_version,
    response_shape_hash,
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
    mondrian_key_with_regime,
    purged_embargo_splits,
    ttr_bucket,
)
from .cost_watch import estimate_api_cost
from .decision_time import median_time_to_decision
from .deribit import DeribitClient, DeribitIV, DeribitTermStructurePoint
from .distribution_utils import distribution_to_probability, mean_of_distribution
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
from .explainer import extract_shap_values, summarize_top_drivers, top_n_drivers
from .external_venue import (
    BinanceDailyKline,
    BinanceKlinesClient,
    BinancePerpClient,
    BinancePerpSnapshot,
    BinanceSpotClient,
    CoinbaseIntxPerpClient,
    CoinbaseIntxPerpSnapshot,
    CoinbaseSpotClient,
    CoinGeckoSpotClient,
    SpotTickerSnapshot,
    SpotValidationResult,
    options_implied_probability,
    perp_basis_implied_probability,
    validate_spot_quotes,
)
from .feature_aggregator import aggregate_features
from .headline_classifier import HeadlineClassificationResult, classify_headline
from .journal_utils import JournalCall, compute_call_brier, compute_pnl
from .kol_lists import filter_posts_by_category, get_kols_for_category
from .liquidity import is_thin_book
from .llm_classifier import (
    HUMAN_REVIEW_QUEUE,
    LlmCallable,
    LlmSuggestion,
    QueueWriter,
    classify_with_llm,
    get_review_queue,
    reset_review_queue,
)
from .metrics import brier_score, conformal_coverage, mean_brier_score
from .paper_trading import enable_paper_trading, is_paper_trading_enabled
from .pipeline import PipelineResult, probability_for_market
from .privacy_utils import records_contain_only_allowed_fields
from .regime import (
    RegimeFeatures,
    RegimeLabel,
    RegimeResult,
    RegimeThresholds,
    regime_features_from_btc_closes,
    tag_regime,
    tag_regime_hmm,
)
from .regulatory_watch import filter_regulatory_news
from .resolution_metrics import precision_recall
from .resolution_risk import ResolutionRiskResult, score_resolution_risk
from .security_utils import needs_rotation
from .sibling_arb import (
    ArbViolation,
    MultiOutcomeGroup,
    OrderedThresholdPair,
    SiblingMarket,
    SiblingPrior,
    SiblingQuote,
    build_multi_outcome_groups,
    build_threshold_pairs,
    detect_multi_outcome_arbs,
    detect_threshold_arbs,
    sibling_prior_for_market,
)
from .signal_feed import is_actionable, select_actionable_events
from .smart_money import net_flow, weighted_flow
from .structured_sentiment import (
    credibility_weight,
    novelty_score,
    reach_adjusted_volume,
    sentiment_dispersion,
    tone_shift,
)
from .types import BaselineSource, HeadlineType, MarketType

__all__ = [
    "ANNUALIZATION_FACTOR",
    "BOOSTER_FEATURE_NAMES",
    "HUMAN_REVIEW_QUEUE",
    "LINEAR_FEATURE_NAMES",
    "LlmCallable",
    "LlmSuggestion",
    "ROLLING_WINDOWS_DEFAULT",
    "TTR_BUCKETS_DEFAULT",
    "ArbViolation",
    "AutoDisableDecision",
    "BaselineInputs",
    "BaselineOutput",
    "BaselineSource",
    "BinanceDailyKline",
    "BinanceKlinesClient",
    "BinancePerpClient",
    "BinancePerpSnapshot",
    "BinanceSpotClient",
    "ClassificationResult",
    "CoinGeckoSpotClient",
    "CoinbaseIntxPerpClient",
    "CoinbaseIntxPerpSnapshot",
    "CoinbaseSpotClient",
    "ConformalCell",
    "ConformalSample",
    "DailySkillSample",
    "DeribitClient",
    "DeribitIV",
    "DeribitTermStructurePoint",
    "EnsembleRegistry",
    "EnsembleSample",
    "FeatureContribution",
    "GradientBoostStump",
    "HeadlineClassificationResult",
    "HeadlineType",
    "IsotonicCalibrator",
    "JournalCall",
    "MarketFeatures",
    "MarketType",
    "MultiOutcomeGroup",
    "OrderedThresholdPair",
    "PipelineResult",
    "PredictionExplanation",
    "PurgedFold",
    "QueueWriter",
    "RegimeFeatures",
    "RegimeLabel",
    "RegimeResult",
    "RegimeThresholds",
    "ResolutionRiskResult",
    "RollingWindowReport",
    "ShapeDiff",
    "SiblingMarket",
    "SiblingPrior",
    "SiblingQuote",
    "SplitConformalRegistry",
    "SpotTickerSnapshot",
    "SpotValidationResult",
    "StratumReport",
    "TypeEnsembleModel",
    "WalkForwardReport",
    "WalkForwardSample",
    "aggregate_features",
    "arb_deviation",
    "brier_score",
    "brier_skill",
    "brier_skill_improvement",
    "build_multi_outcome_groups",
    "build_threshold_pairs",
    "classify",
    "classify_headline",
    "classify_with_llm",
    "compare_response_shape",
    "compute_call_brier",
    "compute_pnl",
    "conformal_coverage",
    "credibility_weight",
    "detect_multi_outcome_arbs",
    "detect_threshold_arbs",
    "diff_shapes",
    "discrete_event_baseline",
    "distribution_to_probability",
    "enable_paper_trading",
    "estimate_api_cost",
    "expected_calibration_error",
    "extract_shap_values",
    "false_positive_rate",
    "filter_posts_by_category",
    "filter_regulatory_news",
    "fit_per_type_ensembles",
    "fit_split_conformal",
    "fit_split_conformal_from_folds",
    "get_kols_for_category",
    "get_review_queue",
    "is_actionable",
    "is_newer_version",
    "is_paper_trading_enabled",
    "is_thin_book",
    "kl_divergence",
    "long_tail_baseline",
    "mean_brier_score",
    "mean_of_distribution",
    "median_time_to_decision",
    "mondrian_key",
    "mondrian_key_with_regime",
    "multi_outcome_baseline",
    "needs_rotation",
    "net_flow",
    "no_arb_violated",
    "novelty_score",
    "options_implied_probability",
    "perp_basis_implied_probability",
    "population_stability_index",
    "precision_recall",
    "probability_for_market",
    "purged_embargo_splits",
    "range_baseline",
    "reach_adjusted_volume",
    "records_contain_only_allowed_fields",
    "regime_features_from_btc_closes",
    "reset_review_queue",
    "response_shape_hash",
    "rolling_window_reports",
    "score_resolution_risk",
    "select_actionable_events",
    "sentiment_dispersion",
    "should_auto_disable",
    "sibling_prior_for_market",
    "summarize_top_drivers",
    "tag_regime",
    "tag_regime_hmm",
    "threshold_baseline",
    "tone_shift",
    "top_n_drivers",
    "ttr_bucket",
    "validate_spot_quotes",
    "walk_forward_evaluate",
    "weighted_flow",
]
