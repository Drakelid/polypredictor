"""Public waitlist + sign-up management (v1 launch).

Provides a self-service sign-up path (no authentication required) for the
public waitlist, plus an admin view of who has signed up.

Schema (Postgres)
-----------------
::

    CREATE TABLE IF NOT EXISTS public_waitlist (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email        TEXT NOT NULL,
        name         TEXT,
        use_case     TEXT,
        source       TEXT NOT NULL DEFAULT 'web',
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        converted_at TIMESTAMPTZ,
        UNIQUE (email)
    );

Endpoints exposed via ``main.py``
-----------------------------------
* ``POST /v1/waitlist``   — unauthenticated; idempotent on email (returns
  existing row on re-submission).
* ``GET  /v1/waitlist``   — admin summary; returns aggregate counts + list.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public_waitlist (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT NOT NULL,
    name         TEXT,
    use_case     TEXT,
    source       TEXT NOT NULL DEFAULT 'web',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    converted_at TIMESTAMPTZ,
    UNIQUE (email)
);
"""


@dataclass(frozen=True)
class WaitlistEntry:
    id: UUID
    email: str
    name: str | None
    use_case: str | None
    source: str
    created_at: datetime
    converted_at: datetime | None


@dataclass(frozen=True)
class WaitlistInput:
    email: str
    name: str | None = None
    use_case: str | None = None
    source: str = "web"


@dataclass(frozen=True)
class WaitlistSummary:
    total_signups: int
    converted: int
    pending: int
    latest_entries: list[WaitlistEntry]


async def _ensure_table(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)


def _row_to_entry(row: asyncpg.Record) -> WaitlistEntry:
    return WaitlistEntry(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        use_case=row["use_case"],
        source=row["source"],
        created_at=row["created_at"],
        converted_at=row["converted_at"],
    )


def _validate_email(email: str) -> None:
    email = email.strip()
    if not email or "@" not in email or len(email) > 320:
        raise ValueError("invalid email address")


async def create_waitlist_entry(
    pool: asyncpg.Pool,
    payload: WaitlistInput,
) -> WaitlistEntry:
    """Insert a new waitlist entry; return the existing row if already present.

    This is idempotent on ``email`` so double-submissions on the landing page
    don't show an error to the user.
    """
    _validate_email(payload.email)
    await _ensure_table(pool)
    async with pool.acquire() as conn:
        # Use ON CONFLICT DO NOTHING + re-fetch for idempotency.
        await conn.execute(
            """
            INSERT INTO public_waitlist (email, name, use_case, source)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (email) DO NOTHING
            """,
            payload.email.strip().lower(),
            payload.name,
            payload.use_case,
            payload.source,
        )
        row = await conn.fetchrow(
            "SELECT * FROM public_waitlist WHERE email = $1",
            payload.email.strip().lower(),
        )
    if row is None:
        raise RuntimeError("waitlist entry missing after insert")
    return _row_to_entry(row)


async def get_waitlist_summary(
    pool: asyncpg.Pool,
    *,
    limit: int = 50,
) -> WaitlistSummary:
    """Return aggregate counts + the most recent ``limit`` entries."""
    await _ensure_table(pool)
    async with pool.acquire() as conn:
        counts = await conn.fetchrow(
            """
            SELECT
                count(*)                                      AS total,
                count(*) FILTER (WHERE converted_at IS NOT NULL) AS converted
            FROM public_waitlist
            """
        )
        rows = await conn.fetch(
            """
            SELECT * FROM public_waitlist
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
    total = int(counts["total"]) if counts else 0
    converted = int(counts["converted"]) if counts else 0
    return WaitlistSummary(
        total_signups=total,
        converted=converted,
        pending=total - converted,
        latest_entries=[_row_to_entry(r) for r in rows],
    )


async def mark_converted(pool: asyncpg.Pool, email: str) -> bool:
    """Mark a waitlist entry as converted (subscription started)."""
    await _ensure_table(pool)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE public_waitlist
            SET converted_at = now()
            WHERE email = $1 AND converted_at IS NULL
            """,
            email.strip().lower(),
        )
    return result == "UPDATE 1"
