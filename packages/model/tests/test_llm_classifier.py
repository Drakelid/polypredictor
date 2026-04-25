from __future__ import annotations

from typing import Any

import pytest
from model import (
    HUMAN_REVIEW_QUEUE,
    ClassificationResult,
    LlmSuggestion,
    MarketType,
    classify_with_llm,
    reset_review_queue,
)


@pytest.fixture(autouse=True)
def _clear_queue() -> None:
    reset_review_queue()
    yield
    reset_review_queue()


def test_confident_classification_skips_llm_and_queue() -> None:
    """A clean threshold question shouldn't invoke the LLM at all."""
    invoked = False

    def _fake_llm(_result: ClassificationResult, _inputs: dict[str, Any]) -> LlmSuggestion | None:
        nonlocal invoked
        invoked = True
        return None

    result = classify_with_llm(
        question="Will BTC be above $100,000 on Dec 31, 2026?",
        llm_callable=_fake_llm,
    )
    assert result.market_type is MarketType.THRESHOLD
    assert invoked is False
    assert HUMAN_REVIEW_QUEUE == []


def test_ambiguous_question_falls_back_when_no_llm() -> None:
    result = classify_with_llm(question="qwerty asdfg zxcv")
    assert result.market_type is MarketType.MISC
    assert any("LLM not configured" in r for r in result.reasons)


def test_ambiguous_question_records_to_test_sink_when_requested() -> None:
    result = classify_with_llm(
        question="qwerty asdfg zxcv",
        record_in_test_sink=True,
    )
    assert result.market_type is MarketType.MISC
    assert len(HUMAN_REVIEW_QUEUE) == 1
    assert HUMAN_REVIEW_QUEUE[0]["question"] == "qwerty asdfg zxcv"
    assert HUMAN_REVIEW_QUEUE[0]["preliminary_result"].market_type is MarketType.MISC


def test_llm_refines_ambiguous_classification_when_more_confident() -> None:
    def _llm(
        result: ClassificationResult, _inputs: dict[str, Any]
    ) -> LlmSuggestion | None:
        assert result.market_type is MarketType.MISC
        return LlmSuggestion(
            market_type=MarketType.DISCRETE_EVENT,
            confidence=0.82,
            rationale="Catalyst-driven binary about a Fed meeting",
        )

    result = classify_with_llm(
        question="qwerty asdfg zxcv",
        llm_callable=_llm,
    )
    assert result.market_type is MarketType.DISCRETE_EVENT
    assert result.confidence == pytest.approx(0.82)
    assert any("LLM refinement" in r for r in result.reasons)


def test_llm_suggestion_below_deterministic_confidence_is_ignored() -> None:
    """An LLM suggestion that doesn't beat the deterministic confidence
    is dropped; the deterministic answer wins, with a documenting reason."""

    def _llm(
        result: ClassificationResult, _inputs: dict[str, Any]
    ) -> LlmSuggestion | None:
        return LlmSuggestion(
            market_type=MarketType.MULTI_OUTCOME,
            confidence=result.confidence - 0.01,
            rationale="Borderline call",
        )

    result = classify_with_llm(
        question="qwerty asdfg zxcv",
        llm_callable=_llm,
    )
    assert result.market_type is MarketType.MISC
    assert any("did not beat deterministic confidence" in r for r in result.reasons)


def test_llm_callable_exception_is_swallowed() -> None:
    def _broken(*_args: object, **_kw: object) -> LlmSuggestion | None:
        raise RuntimeError("Anthropic 429 rate limit")

    result = classify_with_llm(
        question="qwerty asdfg zxcv",
        llm_callable=_broken,
    )
    assert result.market_type is MarketType.MISC
    assert any("RuntimeError" in r for r in result.reasons)


def test_llm_returning_none_falls_back_to_deterministic() -> None:
    def _decline(*_args: object, **_kw: object) -> LlmSuggestion | None:
        return None

    result = classify_with_llm(
        question="qwerty asdfg zxcv",
        llm_callable=_decline,
    )
    assert result.market_type is MarketType.MISC
    assert any("LLM declined" in r for r in result.reasons)
