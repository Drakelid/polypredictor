"""Constrained LLM narration for top-feature drivers (M2.4 / PRD §6.5, §6.10).

Numbers in the dashboard always come from deterministic API fields. The PRD
hard rule (§6.10) is that an LLM may *summarize* / *classify* / *narrate*,
but cannot produce numerical estimates. This module is the wrapper that
enforces that contract:

1. Build :class:`LlmNarrationInput` from the supplied feature labels — only
   labels we actually computed are allowed in the output.
2. Invoke the operator-supplied narrator with a sanitized prompt context
   (labels + sign of contribution; values intentionally excluded).
3. Validate the response through :func:`validate_llm_narration`. Any
   numeric token, estimation phrase, or unsupported driver mention causes
   the validator to raise; we catch that and fall back to the deterministic
   summary for that driver.

Like the M1.1 :class:`LlmCallable`, the narrator is operator-pluggable —
the API service stays free of any LLM SDK dependency. With no narrator
configured the function collapses to the deterministic summaries the
existing dashboard already uses.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .llm_guardrails import LlmNarrationInput, validate_llm_narration


@dataclass(frozen=True)
class DriverNarrationContext:
    """Sanitized prompt input for the narrator.

    The contribution magnitude is intentionally excluded — an LLM should
    only see the SIGN of each driver and the human-readable label so that
    its prose can never invent or rephrase a number.
    """

    label: str
    direction: str  # "higher" / "lower" / "neutral"


# Operator-supplied narrator. Returns one prose line per driver context, or
# raises if the LLM call fails. The wrapper validates each line independently
# and substitutes the deterministic fallback for any line that fails the
# guardrail check.
NarratorCallable = Callable[[Sequence[DriverNarrationContext]], Sequence[str]]


_SIGN_DIRECTION = {1: "higher", -1: "lower", 0: "neutral"}


def _sign(score: float) -> int:
    if score > 0:
        return 1
    if score < 0:
        return -1
    return 0


def _deterministic_summary(label: str, direction: str) -> str:
    if direction == "neutral":
        return f"{label} was neutral"
    return f"{label} pushed the ensemble {direction}"


def narrate_top_drivers(
    *,
    drivers: Sequence[tuple[str, float]],
    narrator: NarratorCallable | None = None,
) -> list[str]:
    """Return one narration string per driver.

    ``drivers`` is a sequence of ``(label, score_contribution)`` tuples —
    typically the top-N from :func:`model.top_n_drivers` mapped through
    :func:`api.markets._attribution_label`. With no narrator configured
    the function returns deterministic summaries; with a narrator it asks
    the LLM and falls back per-line on guardrail failures.
    """
    contexts = [
        DriverNarrationContext(label=label, direction=_SIGN_DIRECTION[_sign(score)])
        for label, score in drivers
    ]
    if not contexts:
        return []
    if narrator is None:
        return [
            _deterministic_summary(ctx.label, ctx.direction) for ctx in contexts
        ]

    try:
        raw = narrator(contexts)
    except Exception:
        return [
            _deterministic_summary(ctx.label, ctx.direction) for ctx in contexts
        ]

    raw_list = list(raw)
    guardrail_input = LlmNarrationInput(
        allowed_feature_labels=[ctx.label for ctx in contexts],
    )
    out: list[str] = []
    for ctx, line in zip(contexts, raw_list, strict=False):
        try:
            out.append(validate_llm_narration(line, guardrail_input))
        except ValueError:
            out.append(_deterministic_summary(ctx.label, ctx.direction))
    # If the narrator returned fewer lines than drivers, fill the tail with
    # deterministic summaries so the UI always gets one line per driver.
    while len(out) < len(contexts):
        ctx = contexts[len(out)]
        out.append(_deterministic_summary(ctx.label, ctx.direction))
    return out


__all__ = [
    "DriverNarrationContext",
    "NarratorCallable",
    "narrate_top_drivers",
]
