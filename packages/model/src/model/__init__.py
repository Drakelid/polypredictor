"""Model submodule exposing classifiers and types.

This submodule contains concrete implementations of market-type
classifiers (`classifier.py` and the LLM-assisted variant
`llm_classifier.py`), shared enumerations (`types.py`), and other
model-specific utilities. By centralising imports here, consumers can
simply import from `packages.model` or `packages.model.src.model` and
have access to all the supported classifiers and types.
"""

from .classifier import classify, ClassificationResult, MarketFeatures  # noqa: F401
from .llm_classifier import classify_with_llm, HUMAN_REVIEW_QUEUE  # noqa: F401
from .types import MarketType, BaselineSource, HeadlineType  # noqa: F401

# SHAP/explainer helpers
from .explainer import extract_shap_values, top_n_drivers, summarize_top_drivers  # noqa: F401

# Headline classifier
from .headline_classifier import (
    classify_headline,
    HeadlineClassificationResult,
)  # noqa: F401

# Structured sentiment heuristics
from .structured_sentiment import (
    novelty_score,
    credibility_weight,
    reach_adjusted_volume,
    sentiment_dispersion,
    tone_shift,
)  # noqa: F401

# KOL list helpers
from .kol_lists import (
    get_kols_for_category,
    filter_posts_by_category,
)  # noqa: F401

# Arbitrage checker
from .arb_checker import no_arb_violated, arb_deviation  # noqa: F401

# Smart-money signals
from .smart_money import net_flow, weighted_flow  # noqa: F401

# API cost estimator
from .cost_watch import estimate_api_cost  # noqa: F401

# API changelog watcher
from .changelog_watch import is_newer_version  # noqa: F401

# Metrics and analytics
from .metrics import brier_score, mean_brier_score, conformal_coverage  # noqa: F401

# Liquidity helpers
from .liquidity import is_thin_book  # noqa: F401

# Distribution utilities
from .distribution_utils import mean_of_distribution, distribution_to_probability  # noqa: F401

# Regulatory watch helper
from .regulatory_watch import filter_regulatory_news  # noqa: F401

# Privacy utilities
from .privacy_utils import records_contain_only_allowed_fields  # noqa: F401

# Security utilities
from .security_utils import needs_rotation  # noqa: F401

# Signal feed
from .signal_feed import is_actionable, select_actionable_events  # noqa: F401

# Journal helpers
from .journal_utils import JournalCall, compute_pnl, compute_call_brier  # noqa: F401

# Paper trading toggle
from .paper_trading import enable_paper_trading, is_paper_trading_enabled  # noqa: F401

# Decision time metrics
from .decision_time import median_time_to_decision  # noqa: F401

# Alert metrics
from .alert_metrics import false_positive_rate  # noqa: F401

# Resolution metrics
from .resolution_metrics import precision_recall  # noqa: F401

# Ablation metrics
from .ablation_metrics import brier_skill_improvement  # noqa: F401

# Feature aggregator
from .feature_aggregator import aggregate_features  # noqa: F401
