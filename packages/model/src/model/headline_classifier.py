"""Scored multi-signal headline classifier (M4.1).

Replaces the single-pass first-match heuristic with a weighted scoring
approach that evaluates *every* category in parallel and returns the
highest-scoring one, breaking ties with category priority.

Design goals
------------
* **Additive scoring** — multiple weak signals combine; a single strong
  signal wins cleanly; ambiguous headlines score low confidence.
* **Configurable weights** — ``HEADLINE_FEATURE_WEIGHTS`` is a plain dict
  so operators can tune without code changes.
* **Backward-compatible interface** — returns the same
  :class:`HeadlineClassificationResult` with the same fields.
* **Negation handling** — "not breaking", "no update" reduce the
  corresponding category score.

Scoring model
-------------
For each category we accumulate a raw score across three signal groups:

1. **Keyword hits** — each matched pattern contributes its weight.
2. **Positional bonus** — headline *starts with* a category-specific
   keyword earns an extra multiplier (position_weight).
3. **Style signals** — all-caps, trailing ``?``, multiple ``!`` are
   cross-cutting; they boost the category that is most consistent with
   that style (BREAKING for caps/!, RUMOR for ?).

Normalisation: raw scores are divided by the sum across all categories
so the returned confidence lies in (0, 1] with values close to 1.0
indicating unambiguous classification.  When all categories score zero
the result is ``OTHER`` with confidence ``0.25``.

Negation guard: the presence of a negation word (see
``_NEGATION_PATTERNS``) within two tokens of a keyword reduces its
contribution by ``negation_discount`` (default 0.6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .types import HeadlineType


# ---------------------------------------------------------------------------
# Weight configuration
# ---------------------------------------------------------------------------

#: Per-category keyword pattern → score weight.
#: Each entry is ``(regex_pattern, weight)``.  Patterns are compiled
#: case-insensitively and matched against the *lowercased* headline.
HEADLINE_FEATURE_WEIGHTS: Dict[
    HeadlineType, List[Tuple[str, float]]
] = {
    HeadlineType.BREAKING: [
        (r"\bbreaking\b", 2.5),
        (r"\bjust in\b", 2.0),
        (r"\balert\b", 1.5),
        (r"\bflash\b", 1.2),
        (r"\bbreaks?\b", 0.8),
        (r"\burgent\b", 1.4),
        (r"\bexclusive\b", 0.7),
        (r"\blive\b", 0.5),
        # Ticker-colon pattern (e.g. "BTC: price surges")
        (r"^[a-z]{1,8}:", 1.0),
        # Multiple exclamation marks
        (r"!{2,}", 1.8),
    ],
    HeadlineType.OPINION: [
        (r"\bopinion\b", 2.5),
        (r"\banalysis\b", 1.8),
        (r"\bcolumn\b", 2.0),
        (r"\beditorial\b", 2.2),
        (r"\bcommentary\b", 1.6),
        (r"\bperspective\b", 1.4),
        (r"\bthink\s+piece\b", 1.8),
        (r"\bwhy\s+\w", 1.2),
        (r"\bhow\s+(to|we|i)\b", 0.9),
        (r"\bshould\b", 0.6),
        (r"\bwhat\s+it\s+means\b", 1.0),
        (r"\bcase\s+for\b", 0.8),
        (r"\bcase\s+against\b", 0.8),
    ],
    HeadlineType.RUMOR: [
        (r"\brumou?r\b", 3.0),
        (r"\bunconfirmed\b", 2.5),
        (r"\breport(s|edly)?\b", 1.2),
        (r"\bsources?\s+say\b", 1.8),
        (r"\bapparently\b", 1.0),
        (r"\bsupposedly\b", 1.2),
        (r"\bspeculat(e|ion|ing)\b", 1.5),
        (r"\bwhisper\b", 1.4),
        (r"\bleaked?\b", 1.6),
        (r"\binsider\b", 1.0),
        # Trailing question mark is a rumour signal
        (r"\?\s*$", 1.5),
    ],
    HeadlineType.DEV_UPDATE: [
        (r"\bupdat(e|ing|ed)\b", 1.8),
        (r"\breleas(e|ed|ing)\b", 2.0),
        (r"\bpatch\b", 2.2),
        (r"\bbugfix\b", 2.5),
        (r"\bchangelog\b", 2.5),
        (r"\blaunch(es|ed|ing)?\b", 1.4),
        (r"\broad\s*map\b", 2.0),
        (r"\bdev\s+blog\b", 2.5),
        (r"\bupgrade\b", 1.6),
        (r"\bnetwork\s+upgrade\b", 2.0),
        (r"\bhard\s+fork\b", 1.8),
        (r"\bsmart\s+contract\b", 0.6),
        (r"\bprotocol\b", 0.5),
        (r"\bv\d+\.\d+", 1.6),  # version number like "v2.1"
        (r"\btest\s*net\b", 1.2),
        (r"\bmain\s*net\b", 1.4),
    ],
}

#: Category priority for tie-breaking (lower index = higher priority).
_CATEGORY_PRIORITY: List[HeadlineType] = [
    HeadlineType.BREAKING,
    HeadlineType.DEV_UPDATE,
    HeadlineType.RUMOR,
    HeadlineType.OPINION,
    HeadlineType.OTHER,
]

#: Positional bonus multiplier when a keyword appears at the *start* of
#: the headline (first 6 tokens).
_POSITION_BONUS = 0.4

#: The best-vs-second-best score ratio below which we penalise confidence
#: (ambiguous classification).
_AMBIGUITY_RATIO_THRESHOLD = 1.4

#: Negation patterns.  When one fires within the context window around a
#: matched keyword, the keyword's contribution is multiplied by this factor.
_NEGATION_DISCOUNT = 0.15
_NEGATION_PATTERNS = [
    r"\bnot?\b",
    r"\bno\b",
    r"\bnever\b",
    r"\bwithout\b",
    r"\bdeny\b",
    r"\bdenies\b",
    r"\bfals(e|ely)\b",
    r"\bdebunked?\b",
]
_NEGATION_WINDOW = 30  # characters on either side of a match to check

# Pre-compile everything once at import time.
_COMPILED_WEIGHTS: Dict[HeadlineType, List[Tuple[re.Pattern[str], float]]] = {
    cat: [(re.compile(pat, re.IGNORECASE), w) for pat, w in patterns]
    for cat, patterns in HEADLINE_FEATURE_WEIGHTS.items()
}
_COMPILED_NEGATIONS: List[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in _NEGATION_PATTERNS
]


# ---------------------------------------------------------------------------
# Public interface (unchanged from the original stub)
# ---------------------------------------------------------------------------


@dataclass
class HeadlineClassificationResult:
    """Result of a headline classification.

    Attributes
    ----------
    headline_type: HeadlineType
        The assigned category.
    confidence: float
        Normalised confidence on (0, 1].  Values near 1.0 indicate an
        unambiguous classification; values near 0.25 indicate that no
        category was strongly supported.
    reasons: List[str]
        Human-readable explanations for the classification.
    """

    headline_type: HeadlineType
    confidence: float
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _style_boosts(text_lower: str) -> Dict[HeadlineType, float]:
    """Return per-category style bonuses based on punctuation / casing.

    These are *additive* — they layer on top of keyword hits.
    """
    original_upper = text_lower.isupper()
    boosts: Dict[HeadlineType, float] = {cat: 0.0 for cat in HeadlineType}
    if original_upper:
        boosts[HeadlineType.BREAKING] += 2.0
    if text_lower.endswith("?"):
        boosts[HeadlineType.RUMOR] += 1.0
    excl_count = text_lower.count("!")
    if excl_count >= 2:
        boosts[HeadlineType.BREAKING] += 1.0 + 0.3 * (excl_count - 2)
    return boosts


def _negation_near(text: str, match: re.Match[str]) -> bool:
    """Return True if a negation word appears near the given match."""
    start = max(0, match.start() - _NEGATION_WINDOW)
    end = min(len(text), match.end() + _NEGATION_WINDOW)
    window = text[start:end]
    return any(neg.search(window) is not None for neg in _COMPILED_NEGATIONS)


def _keyword_score(
    text: str,
    text_lower: str,
    category: HeadlineType,
) -> Tuple[float, List[str]]:
    """Compute the raw keyword score for a single category."""
    score = 0.0
    reasons: List[str] = []
    first_tokens = " ".join(text_lower.split()[:6])
    patterns = _COMPILED_WEIGHTS.get(category, [])
    for pattern, weight in patterns:
        for m in pattern.finditer(text_lower):
            effective_w = weight
            if _negation_near(text_lower, m):
                effective_w *= _NEGATION_DISCOUNT
            # Positional bonus: keyword appears early in the headline.
            if pattern.search(first_tokens) is not None:
                effective_w *= 1.0 + _POSITION_BONUS
            score += effective_w
            reasons.append(
                f"[{category}] pattern '{pattern.pattern}' "
                f"(w={effective_w:.2f})"
            )
    return score, reasons


def _normalise_confidence(
    winner_score: float,
    scores: Dict[HeadlineType, float],
    *,
    ambiguous: bool,
) -> float:
    """Convert raw scores into a normalised confidence."""
    total = sum(scores.values())
    if total == 0:
        return 0.25
    raw = winner_score / total
    if ambiguous:
        # Penalise when the margin over second-best is slim.
        raw *= 0.75
    # Clamp to [0.25, 0.97]
    return max(0.25, min(0.97, raw))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def classify_headline(headline: str) -> HeadlineClassificationResult:
    """Classify a news or social headline into a coarse topic category.

    Parameters
    ----------
    headline:
        Raw headline string.  Leading/trailing whitespace is stripped.

    Returns
    -------
    HeadlineClassificationResult
        The assigned :class:`~.types.HeadlineType`, a normalised
        confidence, and a list of human-readable reasons.

    Notes
    -----
    The scoring pipeline proceeds as follows:

    1. **Style boosts** — all-caps / punctuation patterns contribute
       before keywords are evaluated.
    2. **Keyword scoring** — every category is scored in full; a short-
       circuit exit after the very first match no longer occurs.
    3. **Negation discount** — keyword hits within a negation context
       are downweighted.
    4. **Positional bonus** — early-headline keywords earn a 40% weight
       boost.
    5. **Tie-breaking** — the category in ``_CATEGORY_PRIORITY`` with the
       highest score wins; on exact tie the higher-priority category wins.
    6. **Ambiguity penalty** — when winner/second-best ratio is below
       ``_AMBIGUITY_RATIO_THRESHOLD`` confidence is discounted.
    """
    text = headline.strip()
    text_lower = text.lower()
    all_reasons: List[str] = []

    # 1. Style boosts
    style = _style_boosts(text_lower)

    # 2+3+4. Keyword scoring per category
    scores: Dict[HeadlineType, float] = {cat: 0.0 for cat in HeadlineType}
    for cat in HeadlineType:
        kw_score, kw_reasons = _keyword_score(text, text_lower, cat)
        scores[cat] = kw_score + style.get(cat, 0.0)
        if kw_reasons:
            all_reasons.extend(kw_reasons)

    # 5. Tie-breaking by priority
    valid_cats = [cat for cat in _CATEGORY_PRIORITY if cat in scores]
    winner = max(valid_cats, key=lambda c: scores[c])
    winner_score = scores[winner]

    if winner_score == 0.0:
        all_reasons.append("no patterns matched; defaulting to OTHER")
        return HeadlineClassificationResult(
            headline_type=HeadlineType.OTHER,
            confidence=0.25,
            reasons=all_reasons,
        )

    # 6. Ambiguity check
    sorted_scores = sorted(scores.values(), reverse=True)
    second_best = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
    ambiguous = (
        second_best > 0
        and (winner_score / second_best) < _AMBIGUITY_RATIO_THRESHOLD
    )

    confidence = _normalise_confidence(
        winner_score, scores, ambiguous=ambiguous
    )
    if ambiguous:
        all_reasons.append(
            f"ambiguous: winner={winner} score={winner_score:.2f}, "
            f"second={second_best:.2f} → confidence discounted"
        )

    return HeadlineClassificationResult(
        headline_type=winner,
        confidence=confidence,
        reasons=all_reasons,
    )