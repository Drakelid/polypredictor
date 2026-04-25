from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from api import classification_review


class _FakeRow(dict):
    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key)


class _FakeTransaction:
    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeConn:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.store = store
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        if "UPDATE market_classification_review" in query and "superseded" in query:
            (condition_id,) = args
            count = 0
            for row in self.store.values():
                if row["condition_id"] == condition_id and row["status"] == "pending":
                    row["status"] = "superseded"
                    count += 1
            return f"UPDATE {count}"
        return "OK"

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "INSERT INTO market_classification_review" in query:
            (
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
                created_at,
            ) = args
            new_id = str(uuid.uuid4())
            self.store[new_id] = {
                "id": new_id,
                "condition_id": condition_id,
                "question": question,
                "description": description,
                "tags": list(tags or []),
                "resolution_source": resolution_source,
                "end_date": end_date,
                "multi_outcome_sibling_count": multi_outcome_sibling_count,
                "preliminary_market_type": preliminary_market_type,
                "preliminary_confidence": preliminary_confidence,
                "preliminary_reasons": list(preliminary_reasons or []),
                "llm_market_type": llm_market_type,
                "llm_confidence": llm_confidence,
                "llm_rationale": llm_rationale,
                "status": "pending",
                "reviewer_decision": None,
                "reviewer_notes": None,
                "created_at": created_at,
                "reviewed_at": None,
            }
            return new_id
        if "SELECT COUNT(*)" in query:
            return sum(1 for r in self.store.values() if r["status"] == "pending")
        return None

    async def fetch(self, query: str, *args: Any) -> list[_FakeRow]:
        if "SELECT id, condition_id" in query:
            (limit,) = args
            pending = sorted(
                (r for r in self.store.values() if r["status"] == "pending"),
                key=lambda r: r["created_at"],
            )[: int(limit)]
            return [_FakeRow(r) for r in pending]
        return []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRow | None:
        if "UPDATE market_classification_review" in query and "RETURNING" in query:
            decision_value, reviewer_notes, reviewed_at, review_id = args
            row = self.store.get(review_id)
            if row is None or row["status"] != "pending":
                return None
            row["status"] = decision_value
            row["reviewer_decision"] = decision_value
            row["reviewer_notes"] = reviewer_notes
            row["reviewed_at"] = reviewed_at
            return _FakeRow(row)
        return None

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    def acquire(self) -> _FakeConn:
        return _FakeConn(self.store)


@pytest.mark.asyncio
async def test_enqueue_for_review_inserts_pending_row() -> None:
    pool = _FakePool()
    new_id = await classification_review.enqueue_for_review(
        pool=pool,  # type: ignore[arg-type]
        condition_id="cond-1",
        question="What will happen?",
        description=None,
        tags=["weird"],
        resolution_source=None,
        end_date=None,
        multi_outcome_sibling_count=0,
        preliminary_market_type="misc",
        preliminary_confidence=0.3,
        preliminary_reasons=["no keyword match"],
    )
    assert new_id
    row = pool.store[new_id]
    assert row["status"] == "pending"
    assert row["preliminary_market_type"] == "misc"
    assert row["tags"] == ["weird"]


@pytest.mark.asyncio
async def test_enqueue_for_review_supersedes_prior_pending_row() -> None:
    pool = _FakePool()
    first = await classification_review.enqueue_for_review(
        pool=pool,  # type: ignore[arg-type]
        condition_id="cond-1",
        question="What?",
        description=None,
        tags=[],
        resolution_source=None,
        end_date=None,
        multi_outcome_sibling_count=0,
        preliminary_market_type="misc",
        preliminary_confidence=0.30,
        preliminary_reasons=[],
    )
    second = await classification_review.enqueue_for_review(
        pool=pool,  # type: ignore[arg-type]
        condition_id="cond-1",
        question="What?",
        description=None,
        tags=[],
        resolution_source=None,
        end_date=None,
        multi_outcome_sibling_count=0,
        preliminary_market_type="misc",
        preliminary_confidence=0.42,
        preliminary_reasons=["new run"],
    )
    assert pool.store[first]["status"] == "superseded"
    assert pool.store[second]["status"] == "pending"


@pytest.mark.asyncio
async def test_enqueue_for_review_rejects_blank_inputs() -> None:
    pool = _FakePool()
    with pytest.raises(ValueError):
        await classification_review.enqueue_for_review(
            pool=pool,  # type: ignore[arg-type]
            condition_id="   ",
            question="something",
            description=None,
            tags=[],
            resolution_source=None,
            end_date=None,
            multi_outcome_sibling_count=0,
            preliminary_market_type="misc",
            preliminary_confidence=0.3,
            preliminary_reasons=[],
        )


@pytest.mark.asyncio
async def test_list_pending_reviews_returns_oldest_first() -> None:
    pool = _FakePool()
    base = datetime(2026, 4, 25, 10, tzinfo=UTC)
    await classification_review.enqueue_for_review(
        pool=pool,  # type: ignore[arg-type]
        condition_id="cond-a",
        question="A",
        description=None,
        tags=[],
        resolution_source=None,
        end_date=None,
        multi_outcome_sibling_count=0,
        preliminary_market_type="misc",
        preliminary_confidence=0.3,
        preliminary_reasons=[],
        now=base,
    )
    await classification_review.enqueue_for_review(
        pool=pool,  # type: ignore[arg-type]
        condition_id="cond-b",
        question="B",
        description=None,
        tags=[],
        resolution_source=None,
        end_date=None,
        multi_outcome_sibling_count=0,
        preliminary_market_type="misc",
        preliminary_confidence=0.3,
        preliminary_reasons=[],
        now=base.replace(hour=11),
    )
    rows = await classification_review.list_pending_reviews(
        pool=pool,  # type: ignore[arg-type]
        limit=10,
    )
    assert [r.condition_id for r in rows] == ["cond-a", "cond-b"]


@pytest.mark.asyncio
async def test_submit_review_marks_decision_and_blocks_double_decide() -> None:
    pool = _FakePool()
    new_id = await classification_review.enqueue_for_review(
        pool=pool,  # type: ignore[arg-type]
        condition_id="cond-1",
        question="What?",
        description=None,
        tags=[],
        resolution_source=None,
        end_date=None,
        multi_outcome_sibling_count=0,
        preliminary_market_type="misc",
        preliminary_confidence=0.3,
        preliminary_reasons=[],
    )
    decision = await classification_review.submit_review(
        pool=pool,  # type: ignore[arg-type]
        review_id=new_id,
        decision="accepted",
        reviewer_notes="LGTM",
    )
    assert decision.status == "accepted"
    assert decision.reviewer_notes == "LGTM"
    # Second decision against the same row must fail — only pending rows
    # are decidable.
    with pytest.raises(ValueError):
        await classification_review.submit_review(
            pool=pool,  # type: ignore[arg-type]
            review_id=new_id,
            decision="accepted",
        )


@pytest.mark.asyncio
async def test_submit_review_rejects_invalid_decision() -> None:
    pool = _FakePool()
    new_id = await classification_review.enqueue_for_review(
        pool=pool,  # type: ignore[arg-type]
        condition_id="cond-1",
        question="What?",
        description=None,
        tags=[],
        resolution_source=None,
        end_date=None,
        multi_outcome_sibling_count=0,
        preliminary_market_type="misc",
        preliminary_confidence=0.3,
        preliminary_reasons=[],
    )
    with pytest.raises(ValueError):
        await classification_review.submit_review(
            pool=pool,  # type: ignore[arg-type]
            review_id=new_id,
            decision="maybe",
        )


@pytest.mark.asyncio
async def test_pending_count_reflects_state() -> None:
    pool = _FakePool()
    await classification_review.enqueue_for_review(
        pool=pool,  # type: ignore[arg-type]
        condition_id="cond-1",
        question="What?",
        description=None,
        tags=[],
        resolution_source=None,
        end_date=None,
        multi_outcome_sibling_count=0,
        preliminary_market_type="misc",
        preliminary_confidence=0.3,
        preliminary_reasons=[],
    )
    assert await classification_review.pending_count(pool=pool) == 1  # type: ignore[arg-type]
