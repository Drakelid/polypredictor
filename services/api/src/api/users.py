"""Shared user helpers for user-scoped API modules."""

from __future__ import annotations

from uuid import UUID

from asyncpg import Pool

from .settings import Settings


async def ensure_demo_user(pool: Pool, settings: Settings) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, display_name)
            VALUES ($1, $2)
            ON CONFLICT (email) DO UPDATE
            SET display_name = EXCLUDED.display_name
            RETURNING id
            """,
            settings.journal_demo_user_email,
            settings.journal_demo_user_name,
        )
    assert row is not None
    return row["id"]
