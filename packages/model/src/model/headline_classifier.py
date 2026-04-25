"""Simple rule-based classifier for news and social media headlines.

The product requirements document (§6.3) outlines a need for categorising
headlines into buckets such as breaking news, opinion pieces, rumours and
developer updates. This module implements a deterministic heuristic to map
arbitrary strings into the :class:`HeadlineType` categories defined in
``types.py``. The intent is to surface structured sentiment features without
performing any generative or interpretive language modelling. A future
encoder-based classifier could replace these heuristics to improve
coverage and accuracy.

Usage::

    from packages.model.src.model.headline_classifier import classify_headline
    result = classify_headline("Breaking: Bitcoin surges 5% on ETF rumours")
    print(result.headline_type)  # HeadlineType.BREAKING

The returned :class:`HeadlineClassificationResult` includes a confidence
score and human-readable reasons describing which heuristics fired.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from .types import HeadlineType


@dataclass
class HeadlineClassificationResult:
    """Result of a headline classification.

    Attributes
    ----------
    headline_type: HeadlineType
        The assigned category.
    confidence: float
        A heuristic confidence on [0, 1]. Higher values indicate more
        unambiguous matches (e.g. explicit keywords) while lower values
        indicate weak or no evidence for any category.
    reasons: List[str]
        Explanations for why a particular classification was chosen. These
        strings can be surfaced in debugging or model-interpretation flows.
    """

    headline_type: HeadlineType
    confidence: float
    reasons: List[str] = field(default_factory=list)


def classify_headline(headline: str) -> HeadlineClassificationResult:
    """Classify a news or social headline into a coarse type.

    Parameters
    ----------
    headline:
        The raw headline string to classify. This function lowercases the
        string and trims whitespace but otherwise does not normalise it.

    Returns
    -------
    HeadlineClassificationResult
        The assigned type, a heuristic confidence and a list of reasons.

    Notes
    -----
    This implementation relies on simple keyword and punctuation heuristics:

    * **Breaking:** contains words like "breaking", "just in", or uses a
      shouty style (all-caps or leading numeric alerts). Headlines starting
      with a ticker and colon (e.g. "BTC: Price surges") are also treated
      as breaking news.
    * **Opinion:** contains words such as "opinion", "analysis", "column",
      "editorial", or qualifiers like "why" followed by an assertion.
    * **Rumor:** contains "rumor", "rumour", "unconfirmed", or ends with a
      question mark suggesting speculation.
    * **Dev update:** mentions development-focused terms like "update", "release",
      "patch", "bugfix", "roadmap", or "dev blog".
    * Otherwise the headline is categorised as ``OTHER`` with low confidence.

    Future improvements might include training a supervised classifier on
    labelled examples or fine-tuning a multilingual encoder to predict these
    classes. The simple heuristics here still provide value as part of the
    structured sentiment pipeline by enabling class-specific weighting and
    filtering.
    """

    text = headline.strip().lower()
    reasons: List[str] = []

    # Patterns for each category
    breaking_keywords = [r"\bbreaking\b", r"\bjust in\b", r"^\w{1,6}:", r"!{2,}"]
    opinion_keywords = [
        r"\bopinion\b",
        r"\banalysis\b",
        r"\bcolumn\b",
        r"\beditorial\b",
        r"^why\b",
    ]
    rumor_keywords = [r"\brumor\b", r"\brumour\b", r"\bunconfirmed\b"]
    dev_update_keywords = [
        r"\bupdate\b",
        r"\brelease\b",
        r"\bpatch\b",
        r"\bbugfix\b",
        r"\broadmap\b",
        r"\bdev blog\b",
    ]

    # Determine if the headline is shouty (all caps or many exclamation marks)
    if text.isupper() or text.count("!") >= 2:
        reasons.append("shouty style indicates breaking news")
        return HeadlineClassificationResult(
            headline_type=HeadlineType.BREAKING, confidence=0.9, reasons=reasons
        )

    # Check explicit category keywords
    for pattern in breaking_keywords:
        if re.search(pattern, text):
            reasons.append(f"matched breaking pattern '{pattern}'")
            return HeadlineClassificationResult(
                headline_type=HeadlineType.BREAKING, confidence=0.8, reasons=reasons
            )

    for pattern in opinion_keywords:
        if re.search(pattern, text):
            reasons.append(f"matched opinion pattern '{pattern}'")
            return HeadlineClassificationResult(
                headline_type=HeadlineType.OPINION, confidence=0.7, reasons=reasons
            )

    for pattern in rumor_keywords:
        if re.search(pattern, text):
            reasons.append(f"matched rumor pattern '{pattern}'")
            return HeadlineClassificationResult(
                headline_type=HeadlineType.RUMOR, confidence=0.7, reasons=reasons
            )

    for pattern in dev_update_keywords:
        if re.search(pattern, text):
            reasons.append(f"matched dev-update pattern '{pattern}'")
            return HeadlineClassificationResult(
                headline_type=HeadlineType.DEV_UPDATE, confidence=0.7, reasons=reasons
            )

    # Speculative phrasing: ends with a question mark
    if text.endswith("?"):
        reasons.append("ends with question mark suggests speculation")
        return HeadlineClassificationResult(
            headline_type=HeadlineType.RUMOR, confidence=0.6, reasons=reasons
        )

    # If nothing matches, classify as OTHER with low confidence
    reasons.append("no specific patterns matched")
    return HeadlineClassificationResult(
        headline_type=HeadlineType.OTHER, confidence=0.3, reasons=reasons
    )