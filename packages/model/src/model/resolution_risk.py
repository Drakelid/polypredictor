"""Heuristic resolution-risk scorer for F9.

This is intentionally a bootstrap classifier, not the final UMA-trained model.
It scores ambiguity in market resolution wording so serving can widen
uncertainty and suppress edge on dispute-prone markets today.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolutionRiskResult:
    score: float
    level: str
    is_flagged: bool
    multiplier: float
    reasons: list[str]
    classifier: str = "heuristic_v1"


def score_resolution_risk(
    *,
    question: str,
    description: str | None,
    resolution_source: str | None,
) -> ResolutionRiskResult:
    parts = [question.strip(), (description or "").strip(), (resolution_source or "").strip()]
    text = " ".join(part for part in parts if part).lower()
    reasons: list[str] = []
    score = 0.05

    if not (resolution_source or "").strip():
        score += 0.18
        reasons.append("no explicit resolution source")

    if _contains_any(
        text,
        [
            "significant",
            "material",
            "substantial",
            "meaningful",
            "major",
            "serious",
            "notable",
        ],
    ):
        score += 0.22
        reasons.append("subjective threshold language")

    if _contains_any(
        text,
        [
            "according to",
            "reported by",
            "as announced",
            "official announcement",
            "publicly announced",
            "confirmed by",
        ],
    ):
        score += 0.16
        reasons.append("generic announcement wording")

    if _contains_any(
        text,
        [
            "twitter",
            "x.com",
            "truth social",
            "discord",
            "telegram",
            "single source",
            "oracle",
        ],
    ):
        score += 0.24
        reasons.append("single-source / oracle dependency")

    if _contains_any(
        text,
        [
            "internal",
            "private",
            "not publicly",
            "unpublished",
            "to be determined",
            "tbd",
            "pending confirmation",
        ],
    ):
        score += 0.25
        reasons.append("depends on unpublished or revisable data")

    if _contains_any(
        text,
        [
            "local time",
            "end of day",
            "business day",
            "market close",
            "close of trading",
        ],
    ):
        score += 0.10
        reasons.append("ambiguous timing boundary")

    if _contains_any(
        text,
        [
            "unless otherwise",
            "and/or",
            "or equivalent",
            "or more",
            "or less",
            "similar",
            "comparable",
        ],
    ):
        score += 0.12
        reasons.append("conditional or fuzzy comparison wording")

    if "by end of" in text:
        score += 0.08
        reasons.append("end-of-period cutoff wording")

    score = max(0.0, min(score, 0.95))
    if score >= 0.65:
        level = "high"
    elif score >= 0.35:
        level = "medium"
    else:
        level = "low"
    is_flagged = score >= 0.55
    multiplier = 1.0 + 0.8 * score
    return ResolutionRiskResult(
        score=score,
        level=level,
        is_flagged=is_flagged,
        multiplier=multiplier,
        reasons=reasons,
    )


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)
