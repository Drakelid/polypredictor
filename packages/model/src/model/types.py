"""Shared enums + small data types.

Kept separate from :mod:`classifier` so downstream code can import enums
without pulling in the regex/keyword machinery.
"""

from __future__ import annotations

from enum import StrEnum


class MarketType(StrEnum):
    """Structural shape of a Polymarket market (PRD §6.1)."""

    THRESHOLD = "threshold"
    RANGE = "range"
    DISCRETE_EVENT = "discrete_event"
    MULTI_OUTCOME = "multi_outcome"
    LONG_TAIL_BINARY = "long_tail_binary"
    MISC = "misc"


class BaselineSource(StrEnum):
    """Which baseline produced the displayed probability.

    Surfaced in the UI as a trust badge so users can judge the number.
    """

    BS_TERMINAL = "bs_terminal"          # Black-Scholes terminal-distribution
    BS_ONE_TOUCH = "bs_one_touch"        # Black-Scholes one-touch barrier
    RANGE_CONJUNCTION = "range_conjunction"
    FEDWATCH = "fedwatch"                # CME FedWatch / OIS for rate markets
    CONSENSUS = "consensus"              # survey consensus (CPI / NFP / ...)
    SOFTMAX = "softmax"                  # multi-outcome normalization
    EMBEDDING_MATCH = "embedding_match"  # nearest-resolved prior (cold start)
    BASE_RATE = "base_rate"              # historical frequency
    UNIFORM = "uniform"                  # uninformative 50/50 fallback
    MARKET_MID = "market_mid"            # when no baseline can be computed


class HeadlineType(StrEnum):
    """Coarse categorisation of news headlines used in social sentiment features.

    This enumeration maps a given news or social headline into one of a handful
    of high-level types. See PRD §6.3 for rationale: distinguishing true
    breaking news from opinion pieces or rumours helps de-bias the sentiment
    signal. The classifier defined in :mod:`headline_classifier` currently uses
    simple keyword heuristics to approximate these categories. Future work
    could replace the heuristics with an encoder-based model.
    """

    BREAKING = "breaking"
    """Breaking news: factual, time-sensitive announcements (e.g. "Breaking: Fed hikes rates")."""

    OPINION = "opinion"
    """Opinion or analysis pieces (e.g. columns, think pieces, editorial commentary)."""

    RUMOR = "rumor"
    """Rumours or unconfirmed reports often signalled by question marks or speculative phrasing."""

    DEV_UPDATE = "dev_update"
    """Developer updates such as software release notes or protocol development news."""

    OTHER = "other"
    """Default bucket for headlines that do not match any of the other categories."""