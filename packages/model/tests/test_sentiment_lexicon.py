from __future__ import annotations

import pytest
from model import score_text, score_texts


def test_empty_text_returns_zero() -> None:
    assert score_text("") == 0.0
    assert score_text("   ") == 0.0


def test_text_with_no_lexicon_tokens_returns_zero() -> None:
    assert score_text("the quick brown fox") == 0.0


def test_positive_sentiment() -> None:
    assert score_text("BTC rally is bullish") == pytest.approx(1.0)


def test_negative_sentiment() -> None:
    assert score_text("ETH crash and panic") == pytest.approx(-1.0)


def test_mixed_sentiment_normalized() -> None:
    # 1 positive + 1 negative -> 0
    assert score_text("rally then crash") == pytest.approx(0.0)


def test_negation_flips_next_scored_token() -> None:
    # 'not bullish' becomes negative
    assert score_text("BTC is not bullish today") == pytest.approx(-1.0)
    # 'never bearish' becomes positive
    assert score_text("ETH is never bearish") == pytest.approx(1.0)


def test_score_clamped_to_unit_interval() -> None:
    # Even with many positive words, score caps at 1.0
    assert score_text("rally surge boom moon ath bullish") == pytest.approx(1.0)


def test_extra_positive_extends_lexicon() -> None:
    # 'tokenomics' isn't in the default lexicon
    assert score_text("strong tokenomics", extra_positive=["tokenomics"]) > 0.0


def test_extra_negative_extends_lexicon() -> None:
    assert score_text("weird governance", extra_negative=["weird"]) < 0.0


def test_score_texts_batch() -> None:
    scores = score_texts(["bullish rally", "crash and dump", "the quick fox"])
    assert scores[0] > 0.0
    assert scores[1] < 0.0
    assert scores[2] == 0.0


def test_score_unaffected_by_punctuation_and_case() -> None:
    a = score_text("BULLISH!! Rally!!")
    b = score_text("bullish rally")
    assert a == pytest.approx(b)
