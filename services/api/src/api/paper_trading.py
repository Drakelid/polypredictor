"""Per-user paper-trading toggle (Open Question, tasks.md).

Persists a paper-trading flag per user in Postgres.  When the flag is
enabled, new journal calls created via the detail-page action are tagged
``paper_trade=true`` so they are analysed separately from live positions.

Schema (Postgres)
-----------------
::

    CREATE TABLE IF NOT EXISTS user_paper_trading (
        user_id     TEXT PRIMARY KEY,
        enabled     BOOLEAN NOT NULL DEFAULT false,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

The ``user_id`` is the same demo user identifier used elsewhere in the
system (``settings.journal_demo_user_email``).  When multi-user auth is
introduced this column becomes a FK to the users table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_paper_trading (
    user_id     TEXT PRIMARY KEY,
    enabled     BOOLEAN NOT NULL DEFAULT false,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_DEFAULT_USER_ID = "demo"


@dataclass(frozen=True)
class PaperTradingStatus:
    user_id: str
    enabled: bool
    updated_at: datetime | None


async def _ensure_table(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)


async def get_paper_trading_status(
    pool: asyncpg.Pool, *, user_id: str = _DEFAULT_USER_ID
) -> PaperTradingStatus:
    """Return the current paper-trading flag for ``user_id``."""
    await _ensure_table(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled, updated_at FROM user_paper_trading WHERE user_id = $1",
            user_id,
        )
    if row is None:
        return PaperTradingStatus(user_id=user_id, enabled=False, updated_at=None)
    return PaperTradingStatus(
        user_id=user_id,
        enabled=bool(row["enabled"]),
        updated_at=row["updated_at"],
    )


async def set_paper_trading(
    pool: asyncpg.Pool,
    *,
    user_id: str = _DEFAULT_USER_ID,
    enabled: bool,
) -> PaperTradingStatus:
    """Enable or disable paper trading for ``user_id``."""
    await _ensure_table(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_paper_trading (user_id, enabled, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (user_id) DO UPDATE
                SET enabled    = EXCLUDED.enabled,
                    updated_at = now()
            """,
            user_id,
            enabled,
        )
        row = await conn.fetchrow(
            "SELECT enabled, updated_at FROM user_paper_trading WHERE user_id = $1",
            user_id,
        )
    return PaperTradingStatus(
        user_id=user_id,
        enabled=bool(row["enabled"]),  # type: ignore[index]
        updated_at=row["updated_at"],  # type: ignore[index]
    )
