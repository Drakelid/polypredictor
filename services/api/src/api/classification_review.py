"""Postgres-backed human-review queue for ambiguous market classifications (M1.1).

The deterministic classifier in :mod:`model.classifier` is high-precision but
flags low-confidence or MISC outputs via :attr:`ClassificationResult.needs_review`.
Those rows land here so an operator can pick a label by hand — and, optionally,
an operator-supplied LLM callable (Anthropic / OpenAI / local) can pre-fill
a suggested type before human review.

Functions:

* :func:`enqueue_for_review` - upsert a pending row for a single market;
  any prior pending row for the same ``condition_id`` is marked ``superseded``.
* :func:`list_pending_reviews` - read pending rows oldest-first.
* :func:`submit_review` - record the human decision and stamp ``reviewed_at``.

Side-effect free model-layer helpers in :mod:`model.llm_classifier` accept
an :class:`LlmCallable` so an operator can wire any LLM SDK without the
model package taking a dependency on it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from asyncpg import Pool


_VALID_DECISIONS = frozenset({"accepted", "rejected"})


@dataclass(frozen=True)
class PendingReviewRow:
    id: str
    condition_id: str
    question: str
    description: str | None
    tags: list[str]
    resolution_source: str | None
    end_date: datetime | None
    multi_outcome_sibling_count: int
    preliminary_market_type: str
    preliminary_confidence: float
    preliminary_reasons: list[str]
    llm_market_type: str | None
    llm_confidence: float | None
    llm_rationale: str | None
    created_at: datetime


@dataclass(frozen=True)
class ReviewDecision:
    id: str
    condition_id: str
    status: str
    reviewer_decision: str | None
    reviewer_notes: str | None
    reviewed_at: datetime


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def enqueue_for_review(
    *,
    pool: Pool,
    condition_id: str,
    question: str,
    description: str | None,
    tags: Sequence[str],
    resolution_source: str | None,
    end_date: datetime | None,
    multi_outcome_sibling_count: int,
    preliminary_market_type: str,
    preliminary_confidence: float,
    preliminary_reasons: Sequence[str],
    llm_market_type: str | None = None,
    llm_confidence: float | None = None,
    llm_rationale: str | None = None,
    now: datetime | None = None,
) -> str:
    """Upsert a pending review row; supersede any prior pending row.

    Returns the row's ``id`` so the caller can correlate with logs.
    """
    cleaned_condition = (condition_id or "").strip()
    cleaned_question = (question or "").strip()
    if not cleaned_condition or not cleaned_question:
        raise ValueError("condition_id and question are required")
    issued_at = now or datetime.now(tz=UTC)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE market_classification_review
                   SET status = 'superseded'
                 WHERE condition_id = $1
                   AND status = 'pending'
                """,
                cleaned_condition,
            )
            new_id = await conn.fetchval(
                """
                INSERT INTO market_classification_review (
                    condition_id,
                    question,
                    description,
                    tags,
                    resolution_source,
                    end_date,
                    multi_outcome_sibling_count,
                    preliminary_market_type,
                    preliminary_confidence,
                    preliminary_reasons,
                    llm_market_type,
                    llm_confidence,
                    llm_rationale,
                    status,
                    created_at
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                    $11, $12, $13, 'pending', $14
                )
                RETURNING id
                """,
                cleaned_condition,
                cleaned_question,
                description,
                list(tags),
                resolution_source,
                end_date,
                int(multi_outcome_sibling_count),
                preliminary_market_type,
                float(preliminary_confidence),
                list(preliminary_reasons),
                llm_market_type,
                float(llm_confidence) if llm_confidence is not None else None,
                llm_rationale,
                issued_at,
            )
    return str(new_id)


async def list_pending_reviews(
    *,
    pool: Pool,
    limit: int = 50,
) -> list[PendingReviewRow]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, condition_id, question, description, tags,
                   resolution_source, end_date, multi_outcome_sibling_count,
                   preliminary_market_type, preliminary_confidence,
                   preliminary_reasons, llm_market_type, llm_confidence,
                   llm_rationale, created_at
              FROM market_classification_review
             WHERE status = 'pending'
             ORDER BY created_at ASC
             LIMIT $1
            """,
            int(limit),
        )
    return [
        PendingReviewRow(
            id=str(row["id"]),
            condition_id=str(row["condition_id"]),
            question=str(row["question"]),
            description=row["description"],
            tags=list(row["tags"] or []),
            resolution_source=row["resolution_source"],
            end_date=_coerce_utc(row["end_date"]),
            multi_outcome_sibling_count=int(row["multi_outcome_sibling_count"]),
            preliminary_market_type=str(row["preliminary_market_type"]),
            preliminary_confidence=float(row["preliminary_confidence"]),
            preliminary_reasons=list(row["preliminary_reasons"] or []),
            llm_market_type=row["llm_market_type"],
            llm_confidence=(
                float(row["llm_confidence"]) if row["llm_confidence"] is not None else None
            ),
            llm_rationale=row["llm_rationale"],
            created_at=_coerce_utc(row["created_at"]) or datetime.now(tz=UTC),
        )
        for row in rows
    ]


async def submit_review(
    *,
    pool: Pool,
    review_id: str,
    decision: str,
    reviewer_notes: str | None = None,
    now: datetime | None = None,
) -> ReviewDecision:
    """Record an ``accepted`` / ``rejected`` decision against a pending row."""
    cleaned_decision = (decision or "").strip().lower()
    if cleaned_decision not in _VALID_DECISIONS:
        raise ValueError(
            f"decision must be one of {sorted(_VALID_DECISIONS)}, got {decision!r}"
        )
    asked_at = now or datetime.now(tz=UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE market_classification_review
               SET status = $1,
                   reviewer_decision = $1,
                   reviewer_notes = $2,
                   reviewed_at = $3
             WHERE id = $4
               AND status = 'pending'
            RETURNING id, condition_id, status, reviewer_decision, reviewer_notes, reviewed_at
            """,
            cleaned_decision,
            reviewer_notes,
            asked_at,
            review_id,
        )
    if row is None:
        raise ValueError("review row not found or already decided")
    return ReviewDecision(
        id=str(row["id"]),
        condition_id=str(row["condition_id"]),
        status=str(row["status"]),
        reviewer_decision=row["reviewer_decision"],
        reviewer_notes=row["reviewer_notes"],
        reviewed_at=_coerce_utc(row["reviewed_at"]) or asked_at,
    )


async def pending_count(*, pool: Pool) -> int:
    async with pool.acquire() as conn:
        result = await conn.fetchval(
            """
            SELECT COUNT(*) FROM market_classification_review
             WHERE status = 'pending'
            """
        )
    return int(result or 0)


__all__ = [
    "PendingReviewRow",
    "ReviewDecision",
    "enqueue_for_review",
    "list_pending_reviews",
    "pending_count",
    "submit_review",
]


# Avoid an unused-import warning when the module is imported for typing-only.
_ANY = Any
