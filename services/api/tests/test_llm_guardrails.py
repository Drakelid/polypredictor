from __future__ import annotations

import pytest
from api.llm_guardrails import LlmNarrationInput, validate_llm_narration


def _context() -> LlmNarrationInput:
    return LlmNarrationInput(
        allowed_feature_labels=[
            "Baseline prior",
            "Market price",
            "Smart-money consensus",
        ]
    )


def test_validate_llm_narration_allows_label_only_summary() -> None:
    text = validate_llm_narration(
        "Baseline prior pushed higher while market price pushed lower.",
        _context(),
    )
    assert text == "Baseline prior pushed higher while market price pushed lower."


@pytest.mark.parametrize(
    "text",
    [
        "Baseline prior says this is 54% likely.",
        "Market price moved by 120 bps.",
        "Smart-money consensus implies a $10k flow.",
        "Baseline prior makes the odds attractive.",
        "Market price has positive expected value.",
    ],
)
def test_validate_llm_narration_rejects_numeric_or_estimation_claims(text: str) -> None:
    with pytest.raises(ValueError):
        validate_llm_narration(text, _context())


def test_validate_llm_narration_rejects_unsupported_drivers() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        validate_llm_narration(
            "Baseline prior pushed higher after Twitter and CPI improved.",
            _context(),
        )


def test_validate_llm_narration_requires_supplied_feature_label() -> None:
    with pytest.raises(ValueError, match="feature labels"):
        validate_llm_narration("The signal pushed higher.", _context())
