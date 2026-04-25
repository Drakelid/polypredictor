from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from api.beta_onboarding import (
    FeedbackInput,
    beta_invite_summary,
    create_beta_invite,
    submit_feedback,
)
from api.settings import Settings


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.invite_id = uuid4()
        self.feedback_id = uuid4()

    async def fetch(self, query: str) -> list[dict[str, object]]:
        self.calls.append((query, ()))
        return [
            {
                "id": self.invite_id,
                "email": "one@example.com",
                "display_name": "One",
                "status": "invited",
                "invited_at": datetime(2026, 4, 25, 12, tzinfo=UTC),
                "accepted_at": None,
            }
        ]

    async def fetchrow(self, query: str, *args: object) -> dict[str, object]:
        self.calls.append((query, args))
        if "count(*)::int AS invited_count" in query:
            return {"invited_count": 1, "accepted_count": 0}
        if "INSERT INTO beta_feedback" in query:
            return {
                "id": self.feedback_id,
                "kind": args[1],
                "submitted_at": datetime(2026, 4, 25, 13, tzinfo=UTC),
            }
        return {
            "id": self.invite_id,
            "email": args[1],
            "display_name": args[2],
            "status": "invited",
            "invited_at": datetime(2026, 4, 25, 12, tzinfo=UTC),
            "accepted_at": None,
        }


class _Acquire:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_beta_invite_summary_reports_target_remaining() -> None:
    summary = await beta_invite_summary(
        pool=_FakePool(),
        settings=Settings(beta_invite_target_count=20),
    )
    assert summary.target_count == 20
    assert summary.invited_count == 1
    assert summary.remaining_slots == 19
    assert summary.invites[0].email == "one@example.com"


@pytest.mark.asyncio
async def test_create_beta_invite_normalizes_email() -> None:
    pool = _FakePool()
    invite = await create_beta_invite(
        pool=pool,
        email="  TWO@Example.com ",
        display_name=" Two ",
    )
    assert invite.email == "TWO@Example.com"
    _, args = pool.conn.calls[0]
    assert args[1] == "TWO@Example.com"
    assert args[2] == "Two"


@pytest.mark.asyncio
async def test_create_beta_invite_rejects_invalid_email() -> None:
    with pytest.raises(ValueError, match="valid email"):
        await create_beta_invite(pool=_FakePool(), email="missing-at")


@pytest.mark.asyncio
async def test_submit_feedback_persists_context() -> None:
    pool = _FakePool()
    receipt = await submit_feedback(
        pool=pool,
        payload=FeedbackInput(
            kind="model",
            message="  calibration looks off ",
            page_url="http://localhost:3000/",
            condition_id="cid",
            contact_email="USER@Example.com",
        ),
    )
    assert receipt.kind == "model"
    _, args = pool.conn.calls[0]
    assert args[2] == "calibration looks off"
    assert args[3] == "http://localhost:3000/"
    assert args[4] == "cid"
    assert args[5] == "USER@Example.com"
