"""Closed-beta invite and feedback helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from .settings import Settings

FeedbackKind = Literal["bug", "idea", "model", "data", "other"]


@dataclass(frozen=True)
class BetaInvite:
    id: UUID
    email: str
    display_name: str | None
    status: str
    invited_at: datetime
    accepted_at: datetime | None


@dataclass(frozen=True)
class BetaInviteSummary:
    target_count: int
    invited_count: int
    accepted_count: int
    remaining_slots: int
    invites: list[BetaInvite]


@dataclass(frozen=True)
class FeedbackInput:
    kind: FeedbackKind
    message: str
    page_url: str | None = None
    condition_id: str | None = None
    contact_email: str | None = None


@dataclass(frozen=True)
class FeedbackReceipt:
    id: UUID
    kind: FeedbackKind
    submitted_at: datetime


def _clean_text(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    if not clean:
        return None
    return clean[:max_len]


async def beta_invite_summary(
    *,
    pool: Any,
    settings: Settings,
) -> BetaInviteSummary:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, email, display_name, status, invited_at, accepted_at
            FROM beta_invites
            ORDER BY invited_at DESC, email
            LIMIT 200
            """
        )
        counts = await conn.fetchrow(
            """
            SELECT
              count(*)::int AS invited_count,
              count(*) FILTER (WHERE status = 'accepted')::int AS accepted_count
            FROM beta_invites
            """
        )
    invited_count = int(counts["invited_count"]) if counts else 0
    accepted_count = int(counts["accepted_count"]) if counts else 0
    target = settings.beta_invite_target_count
    return BetaInviteSummary(
        target_count=target,
        invited_count=invited_count,
        accepted_count=accepted_count,
        remaining_slots=max(0, target - invited_count),
        invites=[
            BetaInvite(
                id=row["id"],
                email=str(row["email"]),
                display_name=row["display_name"],
                status=str(row["status"]),
                invited_at=row["invited_at"],
                accepted_at=row["accepted_at"],
            )
            for row in rows
        ],
    )


async def create_beta_invite(
    *,
    pool: Any,
    email: str,
    display_name: str | None = None,
) -> BetaInvite:
    clean_email = _clean_text(email, 320)
    if clean_email is None or "@" not in clean_email:
        raise ValueError("valid email is required")
    clean_name = _clean_text(display_name, 200)
    row_id = uuid4()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO beta_invites (id, email, display_name)
            VALUES ($1, lower($2), $3)
            ON CONFLICT (email) DO UPDATE
            SET display_name = COALESCE(EXCLUDED.display_name, beta_invites.display_name),
                updated_at = now()
            RETURNING id, email, display_name, status, invited_at, accepted_at
            """,
            row_id,
            clean_email,
            clean_name,
        )
    assert row is not None
    return BetaInvite(
        id=row["id"],
        email=str(row["email"]),
        display_name=row["display_name"],
        status=str(row["status"]),
        invited_at=row["invited_at"],
        accepted_at=row["accepted_at"],
    )


async def submit_feedback(
    *,
    pool: Any,
    payload: FeedbackInput,
) -> FeedbackReceipt:
    message = _clean_text(payload.message, 5_000)
    if message is None:
        raise ValueError("feedback message is required")
    row_id = uuid4()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO beta_feedback (
              id, kind, message, page_url, condition_id, contact_email
            )
            VALUES ($1, $2, $3, $4, $5, lower($6))
            RETURNING id, kind, submitted_at
            """,
            row_id,
            payload.kind,
            message,
            _clean_text(payload.page_url, 2_000),
            _clean_text(payload.condition_id, 200),
            _clean_text(payload.contact_email, 320),
        )
    assert row is not None
    return FeedbackReceipt(
        id=row["id"],
        kind=row["kind"],
        submitted_at=row["submitted_at"],
    )
