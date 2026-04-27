"""Tests for the scored multi-signal headline classifier (M4.1)."""

from __future__ import annotations

import pytest

from model.headline_classifier import (
    HeadlineClassificationResult,
    HEADLINE_FEATURE_WEIGHTS,
    classify_headline,
)
from model.types import HeadlineType


# ---------------------------------------------------------------------------
# Basic classification correctness
# ---------------------------------------------------------------------------


def test_breaking_keyword() -> None:
    result = classify_headline("BREAKING: Bitcoin surges past $100k")
    assert result.headline_type == HeadlineType.BREAKING
    assert result.confidence > 0.5


def test_breaking_all_caps_style() -> None:
    result = classify_headline("BITCOIN JUST HIT 100K")
    assert result.headline_type == HeadlineType.BREAKING
    assert result.confidence > 0.5


def test_breaking_exclamation_marks() -> None:
    result = classify_headline("Bitcoin breaks ATH!! Major move incoming!!")
    assert result.headline_type == HeadlineType.BREAKING
    assert result.confidence > 0.4


def test_opinion_column() -> None:
    result = classify_headline("Opinion: Why Bitcoin will dominate finance in 2027")
    assert result.headline_type == HeadlineType.OPINION
    assert result.confidence > 0.5


def test_opinion_analysis() -> None:
    result = classify_headline("Analysis: What the Fed rate cut means for crypto markets")
    assert result.headline_type == HeadlineType.OPINION


def test_rumor_unconfirmed() -> None:
    result = classify_headline("Unconfirmed: BlackRock to launch spot ETH ETF next week")
    assert result.headline_type == HeadlineType.RUMOR
    assert result.confidence > 0.5


def test_rumor_trailing_question_mark() -> None:
    result = classify_headline("Is Coinbase about to list Solana futures?")
    assert result.headline_type == HeadlineType.RUMOR


def test_dev_update_release() -> None:
    result = classify_headline("Ethereum v2.5 released — key upgrade notes")
    assert result.headline_type == HeadlineType.DEV_UPDATE
    assert result.confidence > 0.5


def test_dev_update_patch() -> None:
    result = classify_headline("Critical patch deployed to the Solana mainnet")
    assert result.headline_type == HeadlineType.DEV_UPDATE


def test_dev_update_roadmap() -> None:
    result = classify_headline("Uniswap v4 roadmap: what to expect in Q3")
    assert result.headline_type == HeadlineType.DEV_UPDATE


def test_other_no_signals() -> None:
    result = classify_headline("A market summary for the week")
    assert result.headline_type == HeadlineType.OTHER
    assert result.confidence == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Negation handling
# ---------------------------------------------------------------------------


def test_negation_reduces_breaking_confidence() -> None:
    """'not breaking' should score lower than 'breaking'."""
    positive = classify_headline("Breaking: ETH spikes 10%")
    negated = classify_headline("This is not breaking news: ETH moves slightly")
    assert positive.confidence > negated.confidence


def test_negation_reduces_rumor_confidence() -> None:
    rumor = classify_headline("Rumour: Binance to delist BTC")
    debunked = classify_headline("Debunked rumour: Binance confirmed no such plan")
    assert rumor.confidence > debunked.confidence


# ---------------------------------------------------------------------------
# Positional bonus
# ---------------------------------------------------------------------------


def test_positional_bonus_increases_confidence() -> None:
    """A keyword at the start of the headline should score higher confidence."""
    front = classify_headline("Breaking: Fed increases rates by 50bps")
    back = classify_headline("Fed increases rates by 50bps — this is breaking")
    # Both should classify as BREAKING; front should have higher confidence.
    assert front.headline_type == HeadlineType.BREAKING
    assert back.headline_type == HeadlineType.BREAKING
    assert front.confidence >= back.confidence


# ---------------------------------------------------------------------------
# Ambiguity detection
# ---------------------------------------------------------------------------


def test_ambiguous_headline_has_lower_confidence() -> None:
    """A headline mixing opinion + breaking signals should flag low confidence."""
    # "Breaking opinion" fires both BREAKING and OPINION signals
    result = classify_headline("Breaking opinion: Why Bitcoin will crash")
    # We accept any category but confidence must be modest
    assert result.confidence < 0.80


# ---------------------------------------------------------------------------
# Multi-signal accumulation
# ---------------------------------------------------------------------------


def test_multiple_dev_signals_accumulate() -> None:
    """Multiple DEV_UPDATE signals should produce higher confidence than one."""
    single = classify_headline("New release for the protocol")
    multi = classify_headline("New release patch for the mainnet upgrade — changelog available")
    assert multi.confidence >= single.confidence


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


def test_returns_dataclass_with_reasons() -> None:
    result = classify_headline("Breaking: major crash incoming!!")
    assert isinstance(result, HeadlineClassificationResult)
    assert isinstance(result.reasons, list)
    assert len(result.reasons) > 0


# ---------------------------------------------------------------------------
# Config integrity
# ---------------------------------------------------------------------------


def test_all_categories_have_patterns() -> None:
    """Every non-OTHER category should have at least one weight entry."""
    for cat in HeadlineType:
        if cat == HeadlineType.OTHER:
            continue
        assert cat in HEADLINE_FEATURE_WEIGHTS, f"Missing weights for {cat}"
        assert len(HEADLINE_FEATURE_WEIGHTS[cat]) > 0
