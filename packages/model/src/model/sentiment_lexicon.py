"""Deterministic, dependency-free sentiment scorer.

The PRD calls for *structured* sentiment features (dispersion, tone-shift,
reach-adjusted volume) rather than raw polarity, but every one of those
features needs a per-message scalar score as its building block. This
module provides a small word-list scorer so the structured pipeline can
run end-to-end on existing Reddit / RSS social rows without taking a
dependency on a large NLP model.

Design notes:

* Lexicon-only — no n-grams, no negation handling beyond a lightweight
  `not <word>` flip. This is intentional: the structured-feature layer
  expects noise per message; precision per message matters less than
  cross-message dispersion.
* Output range: ``[-1.0, +1.0]``. A message with no scored tokens returns
  ``0.0`` so missing data doesn't bias dispersion.
* The lexicon focuses on crypto / finance vocabulary so the score
  responds to the markets we actually run against.

Operators can extend the lexicon by passing additional terms to
:func:`score_text` — see the ``extra_positive`` / ``extra_negative`` kwargs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")

_NEGATION_TERMS = frozenset({"not", "no", "never", "isn't", "ain't", "won't", "doesn't", "don't"})

_POSITIVE = frozenset(
    {
        # Generic
        "good", "great", "strong", "bullish", "rally", "surge", "soar", "soared",
        "boom", "rebound", "recovery", "uptrend", "uptrending", "outperform",
        "beat", "beats", "beating", "exceeded", "growth", "growing", "expanding",
        "positive", "optimistic", "confident", "confidence", "win", "wins", "winning",
        # Crypto / finance
        "moon", "mooning", "breakout", "breakouts", "ath", "atl-bounce",
        "accumulating", "accumulation", "inflow", "inflows", "long", "longs",
        "buying", "bid", "bids", "approval", "approved", "etf-approved",
        "halving", "burning", "burn", "deflationary", "demand",
    }
)

_NEGATIVE = frozenset(
    {
        # Generic
        "bad", "weak", "bearish", "crash", "crashing", "drop", "dropped",
        "fall", "fell", "plunge", "plunged", "tumble", "tumbled", "dump",
        "dumping", "selloff", "sell-off", "decline", "declining", "downturn",
        "negative", "pessimistic", "fear", "fears", "panic", "panicking",
        "loss", "losses", "lose", "losing", "miss", "missed", "missing",
        "downgrade", "downgraded", "warning",
        # Crypto / finance
        "rugpull", "rug", "scam", "hack", "hacked", "exploit", "exploited",
        "outflow", "outflows", "short", "shorts", "shorting", "liquidation",
        "liquidations", "rejected", "denied", "ban", "banned",
        "delisted", "delisting", "halt", "halted", "freeze", "frozen",
    }
)


def _tokens(text: str) -> list[str]:
    if not text:
        return []
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def score_text(
    text: str,
    *,
    extra_positive: Iterable[str] | None = None,
    extra_negative: Iterable[str] | None = None,
) -> float:
    """Return a sentiment score in ``[-1.0, +1.0]``.

    Each scored token contributes ``+1`` for positive and ``-1`` for
    negative; a preceding negation term flips the sign of the next scored
    token. The raw count is normalized by the total scored-token count
    so a message with one positive word scores the same as one with ten.
    Messages with no scored tokens return ``0.0``.
    """
    positives = set(_POSITIVE)
    negatives = set(_NEGATIVE)
    if extra_positive:
        positives = positives | {w.lower() for w in extra_positive}
    if extra_negative:
        negatives = negatives | {w.lower() for w in extra_negative}

    tokens = _tokens(text)
    if not tokens:
        return 0.0

    score = 0
    matched = 0
    negate_next = False
    for token in tokens:
        if token in _NEGATION_TERMS:
            negate_next = True
            continue
        if token in positives:
            score += -1 if negate_next else 1
            matched += 1
            negate_next = False
        elif token in negatives:
            score += 1 if negate_next else -1
            matched += 1
            negate_next = False
        else:
            negate_next = False
    if matched == 0:
        return 0.0
    return max(-1.0, min(1.0, score / float(matched)))


def score_texts(
    texts: Iterable[str],
    *,
    extra_positive: Iterable[str] | None = None,
    extra_negative: Iterable[str] | None = None,
) -> list[float]:
    """Score multiple texts in one call. Convenience wrapper."""
    return [
        score_text(text, extra_positive=extra_positive, extra_negative=extra_negative)
        for text in texts
    ]


__all__ = ["score_text", "score_texts"]
