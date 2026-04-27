"""Guardrails for any external LLM narration path.

PolyPredictor's model math is deterministic. If an LLM is later used to turn
feature attributions into prose, this module is the boundary: LLM output may
summarize supplied feature labels, but it must not introduce numeric estimates,
odds, probabilities, prices, or unsupported drivers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUMERIC_CLAIM_RE = re.compile(
    r"""
    (?:
        [+-]?\$?\d+(?:[.,]\d+)?\s*(?:%|bps?|c|¢|x|usd|usdc|dollars?)?
        |
        \b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

ESTIMATION_TERMS_RE = re.compile(
    r"\b(?:probability|odds|chance|price|target|forecast|estimate|expected value|ev)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LlmNarrationInput:
    allowed_feature_labels: list[str]
    allowed_summary_verbs: list[str] = field(
        default_factory=lambda: [
            "pushed higher",
            "pushed lower",
            "was neutral",
            "increased",
            "decreased",
        ]
    )


def validate_llm_narration(text: str, context: LlmNarrationInput) -> str:
    """Return sanitized narration or raise ``ValueError``.

    The validator is intentionally conservative: if the LLM output contains any
    numeric token or estimation language *outside* the supplied feature labels,
    reject it. Feature labels themselves often contain numerics ("24h realized
    vol", "5% book depth") so we strip allowed labels first, then check the
    residual for numeric / estimation tokens. Numeric values that originate
    elsewhere already exist in deterministic API fields and should be rendered
    directly by the UI rather than copied through an LLM.
    """
    clean = " ".join(text.strip().split())
    if not clean:
        raise ValueError("LLM narration is empty")

    lowered = clean.lower()
    allowed_labels = {label.lower() for label in context.allowed_feature_labels}
    # Strip every allowed label from the residual we run the numeric and
    # estimation regexes against, so a label like "24h realized vol" doesn't
    # itself trip the numeric check on its leading "24".
    residual = lowered
    for label in sorted(allowed_labels, key=len, reverse=True):
        residual = residual.replace(label, " ")
    if NUMERIC_CLAIM_RE.search(residual):
        raise ValueError("LLM narration must not contain numeric claims")
    if ESTIMATION_TERMS_RE.search(residual):
        raise ValueError("LLM narration must not estimate probability, odds, price, or EV")
    mentioned_labels = {label for label in allowed_labels if label in lowered}
    if not mentioned_labels:
        raise ValueError("LLM narration must reference supplied feature labels")

    unsupported_signal_terms = {
        "whale",
        "insider",
        "news",
        "twitter",
        "x",
        "reddit",
        "fed",
        "cpi",
        "funding",
    }
    allowed_words = set().union(*(set(label.split()) for label in allowed_labels))
    unsupported = unsupported_signal_terms - allowed_words
    if any(term in lowered for term in unsupported):
        raise ValueError("LLM narration contains unsupported driver claims")
    return clean
