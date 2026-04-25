"""Deterministic market-type classifier.

M1 scope: regex + keyword heuristics only. An LLM-assisted classifier with a
human-review queue is planned for M1.1 follow-up, but deterministic rules
are both more auditable and catch the bulk of crypto/finance markets cleanly.

The classifier extracts structured features (strike, range bounds, event
family, multi-outcome siblings) alongside the label, because the baseline
layer needs those features to compute probabilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .types import MarketType

# --- Feature extraction -----------------------------------------------------


@dataclass(frozen=True)
class MarketFeatures:
    """Structured signals extracted from market text.

    Empty fields simply mean the classifier didn't find them — downstream
    baselines decide whether absence invalidates the call or just lowers the
    confidence badge.
    """

    asset: str | None = None           # 'BTC', 'ETH', 'SOL', 'SP500', ...
    strike: float | None = None         # $ value for threshold/range markets
    range_low: float | None = None      # range markets: lower bound
    range_high: float | None = None     # range markets: upper bound
    direction: str | None = None        # 'above' | 'below' | 'between'
    event_family: str | None = None     # 'fomc' | 'cpi' | 'nfp' | 'etf_approval' | ...
    resolution_date: datetime | None = None
    is_multi_outcome_sibling: bool = False


@dataclass(frozen=True)
class ClassificationResult:
    market_type: MarketType
    confidence: float                  # 0.0-1.0
    features: MarketFeatures
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        """Low-confidence or MISC classifications should queue for review."""
        return self.confidence < 0.55 or self.market_type is MarketType.MISC


# --- Regex library ----------------------------------------------------------

# Assets we recognize in v1 (PRD §5.1: crypto + macro). Ordered so ETH isn't
# greedy-matched inside ETHUSD etc.
_ASSET_PATTERNS = [
    (r"\bbtc\b|\bbitcoin\b", "BTC"),
    (r"\beth\b|\bether(?:eum)?\b", "ETH"),
    (r"\bsol\b|\bsolana\b", "SOL"),
    (r"\bs&?p\s*500\b|\bsp500\b|\bspx\b", "SP500"),
    (r"\bnasdaq\b|\bndx\b", "NDX"),
    (r"\bdxy\b|\bdollar\s*index\b", "DXY"),
    (r"\bgold\b|\bxau\b", "GOLD"),
]

# Dollar amount with magnitude suffix or comma-separated.
_DOLLAR_RE = re.compile(
    r"\$?\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<mag>[kKmMbB]?)\b"
)
_NUMBER_PATTERN = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?"
_OPTIONAL_MAG_SUFFIX_PATTERN = r"(?:\s*(?P<mag>[kKmMbB]))?(?![A-Za-z])"

# Expanded threshold keywords to capture more natural phrasing.
# Order matters: place longer phrases before shorter ones so specific patterns match first.
# Expanded threshold keywords to capture more natural phrasing.
# Order matters: place longer phrases before shorter ones so specific patterns match first.
_THRESHOLD_KEYWORDS = (
    # Inclusive and colloquial phrases (multi-word)
    "no less than",
    "not less than",
    "no more than",
    "not more than",
    "at least",
    "at most",
    "less than or equal to",
    "less than or equal",
    "less or equal to",
    "greater than or equal to",
    "greater than or equal",
    "at or above",
    "at or below",
    "or more",
    "or less",
    # Symbolic operators and their plain-text equivalents
    ">=",
    "<=",
    "≥",
    "≤",
    # Single-word/shorter phrases
    "above",
    "over",
    "greater than",
    "more than",
    ">",
    "exceed",
    "reach",
    "hit",
    "cross",
    "surpass",
    "below",
    "under",
    "less than",
    "lower than",
    "<",
)

# Keywords that imply a "below" direction. These are matched case-insensitively.
_BELOW_KEYWORDS = {
    # Phrases that indicate a "below" or "at most" type direction.
    "below",
    "under",
    "less than",
    "lower than",
    "<",
    "<=",
    "≤",
    "no more than",
    "not more than",
    "at most",
    "at or below",
    "less than or equal",
    "less than or equal to",
    "less or equal to",
    "or less",
}

# Range matcher: support "between X and Y", "from X to Y", and hyphenated ranges like "X - Y".
_RANGE_RE = re.compile(
    rf"(?:between|from)\s+\$?(?P<lo>{_NUMBER_PATTERN})(?:\s*(?P<lo_mag>[kKmMbB]))?(?![A-Za-z])"
    # Allow 'and', 'to' or a hyphen between bounds with optional whitespace around the separator.
    rf"\s*(?:and|to|-)\s*\$?(?P<hi>{_NUMBER_PATTERN})(?:\s*(?P<hi_mag>[kKmMbB]))?(?![A-Za-z])",
    re.IGNORECASE,
)

# Additional pattern to catch hyphenated ranges without an explicit prefix (e.g. "20k-30k").
_RANGE_HYPHEN_RE = re.compile(
    rf"\$?(?P<lo>{_NUMBER_PATTERN})(?:\s*(?P<lo_mag>[kKmMbB]))?\s*-\s*\$?(?P<hi>{_NUMBER_PATTERN})(?:\s*(?P<hi_mag>[kKmMbB]))?(?![A-Za-z])",
    re.IGNORECASE,
)

_EVENT_FAMILIES = {
    "fomc": ("fomc", "fed rate", "federal reserve", "rate decision", "fed cut", "fed hike", "fed hold"),
    "cpi": ("cpi ", "consumer price", "inflation print"),
    "nfp": ("nfp", "non-farm payroll", "nonfarm payroll", "jobs report"),
    "etf_approval": ("etf approval", "etf flow", "etf inflow", "etf outflow"),
    "earnings": ("earnings beat", "beat earnings", "earnings miss"),
}

_MULTI_OUTCOME_HINTS = (
    "which",
    "who will",
    "winner of",
    "first to",
)


# --- Helpers ----------------------------------------------------------------


def _parse_dollar(num: str, mag: str) -> float:
    value = float(num.replace(",", ""))
    m = (mag or "").lower()
    if m == "k":
        return value * 1_000
    if m == "m":
        return value * 1_000_000
    if m == "b":
        return value * 1_000_000_000
    return value


def _find_asset(text: str) -> str | None:
    for pattern, label in _ASSET_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return None


def _find_event_family(text: str) -> str | None:
    lowered = text.lower()
    for family, hints in _EVENT_FAMILIES.items():
        if any(h in lowered for h in hints):
            return family
    return None


def _find_strike(text: str) -> tuple[float, str] | None:
    """Return (strike_value, direction) or None.

    Looks for a threshold keyword adjacent to a dollar amount.
    """
    for kw in _THRESHOLD_KEYWORDS:
        pattern = re.compile(
            rf"\b{re.escape(kw)}\b[^.?!]*?"
            rf"(?P<num>{_NUMBER_PATTERN}){_OPTIONAL_MAG_SUFFIX_PATTERN}",
            re.IGNORECASE,
        )
        m = pattern.search(text)
        if m:
            # Determine direction based on the keyword's membership in the below-set. Defaults to 'above'.
            direction = "below" if kw.lower() in _BELOW_KEYWORDS else "above"
            return _parse_dollar(m.group("num"), m.group("mag")), direction
    return None


def _find_range(text: str) -> tuple[float, float] | None:
    # First try the explicit range pattern (between/from X and/to Y).
    m = _RANGE_RE.search(text)
    if m:
        lo = _parse_dollar(m.group("lo"), m.group("lo_mag"))
        hi = _parse_dollar(m.group("hi"), m.group("hi_mag"))
        return (min(lo, hi), max(lo, hi))
    # Fall back to hyphenated ranges if an asset is present nearby; this helps avoid matching year ranges.
    m2 = _RANGE_HYPHEN_RE.search(text)
    if m2:
        lo = _parse_dollar(m2.group("lo"), m2.group("lo_mag"))
        hi = _parse_dollar(m2.group("hi"), m2.group("hi_mag"))
        return (min(lo, hi), max(lo, hi))
    return None


# --- Classifier entrypoint --------------------------------------------------


def classify(
    *,
    question: str,
    description: str | None = None,
    tags: list[str] | None = None,
    resolution_source: str | None = None,
    end_date: datetime | None = None,
    outcomes: list[str] | None = None,
    multi_outcome_sibling_count: int = 0,
) -> ClassificationResult:
    """Classify a market.

    ``multi_outcome_sibling_count`` is set by the ingestion layer when a market
    is part of an event group (e.g. "Which L2 hits $10B TVL first?" with
    N>=3 exclusive outcomes). A single market in isolation can't detect this
    reliably from text alone.
    """
    text = " ".join(filter(None, [question, description or "", " ".join(tags or [])])).strip()
    reasons: list[str] = []
    asset = _find_asset(text)
    event_family = _find_event_family(text)
    lowered = text.lower()

    # --- multi-outcome ---------------------------------------------------
    if multi_outcome_sibling_count >= 3 or (outcomes is not None and len(outcomes) >= 3):
        reasons.append(f"multi_outcome: siblings={multi_outcome_sibling_count}")
        return ClassificationResult(
            market_type=MarketType.MULTI_OUTCOME,
            confidence=0.9,
            features=MarketFeatures(
                asset=asset,
                event_family=event_family,
                resolution_date=end_date,
                is_multi_outcome_sibling=True,
            ),
            reasons=reasons,
        )
    # Phrasing-only hint for multi-outcome binary legs.
    if any(h in lowered for h in _MULTI_OUTCOME_HINTS) and multi_outcome_sibling_count >= 2:
        reasons.append("multi_outcome phrasing + sibling")
        return ClassificationResult(
            market_type=MarketType.MULTI_OUTCOME,
            confidence=0.75,
            features=MarketFeatures(
                asset=asset,
                event_family=event_family,
                resolution_date=end_date,
                is_multi_outcome_sibling=True,
            ),
            reasons=reasons,
        )

    # --- range -----------------------------------------------------------
    range_ = _find_range(text)
    if range_ is not None:
        lo, hi = range_
        reasons.append(f"range: {lo}-{hi}")
        return ClassificationResult(
            market_type=MarketType.RANGE,
            confidence=0.9,
            features=MarketFeatures(
                asset=asset,
                range_low=lo,
                range_high=hi,
                direction="between",
                event_family=event_family,
                resolution_date=end_date,
            ),
            reasons=reasons,
        )

    # --- discrete event --------------------------------------------------
    if event_family is not None:
        # CPI prints, FOMC decisions etc. are discrete events even when they
        # happen to mention a number ("CPI prints below 2.8%"). Threshold-like
        # markets on macro releases are still driven by consensus/OIS priors
        # rather than Black-Scholes on spot. Classify as discrete_event and let
        # the discrete baseline consume the numeric strike if present.
        strike_dir = _find_strike(text)
        reasons.append(f"event_family={event_family}")
        return ClassificationResult(
            market_type=MarketType.DISCRETE_EVENT,
            confidence=0.85,
            features=MarketFeatures(
                asset=asset,
                strike=strike_dir[0] if strike_dir else None,
                direction=strike_dir[1] if strike_dir else None,
                event_family=event_family,
                resolution_date=end_date,
            ),
            reasons=reasons,
        )

    # --- threshold -------------------------------------------------------
    strike_dir = _find_strike(text)
    if strike_dir is not None and asset is not None:
        strike, direction = strike_dir
        reasons.append(f"threshold: {direction} {strike} on {asset}")
        return ClassificationResult(
            market_type=MarketType.THRESHOLD,
            confidence=0.9,
            features=MarketFeatures(
                asset=asset,
                strike=strike,
                direction=direction,
                resolution_date=end_date,
            ),
            reasons=reasons,
        )

    # --- long-tail binary ------------------------------------------------
    if re.search(r"\bwill\b", lowered) or re.search(r"\?\s*$", question):
        reasons.append("long-tail binary phrasing")
        return ClassificationResult(
            market_type=MarketType.LONG_TAIL_BINARY,
            confidence=0.6,
            features=MarketFeatures(
                asset=asset,
                event_family=event_family,
                resolution_date=end_date,
            ),
            reasons=reasons,
        )

    # --- misc fallback ---------------------------------------------------
    reasons.append("no rule matched")
    return ClassificationResult(
        market_type=MarketType.MISC,
        confidence=0.3,
        features=MarketFeatures(
            asset=asset,
            event_family=event_family,
            resolution_date=end_date,
        ),
        reasons=reasons,
    )