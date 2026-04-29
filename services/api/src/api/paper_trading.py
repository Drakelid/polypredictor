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

The ``user_id`` column is the user's email (TEXT) — same shape as the
demo user identifier used elsewhere in the system. When the codebase
converges on FK-to-``users``, this column flips to a UUID; the public
function signatures already key on ``email`` so the swap is local.
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


@dataclass(frozen=True)
class PaperTradingStatus:
    email: str
    enabled: bool
    updated_at: datetime | None


def _normalize_email(email: str) -> str:
    cleaned = (email or "").strip().lower()
    if not cleaned or "@" not in cleaned:
        raise ValueError("email is required")
    return cleaned


async def _ensure_table(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)


async def get_paper_trading_status(
    pool: asyncpg.Pool, *, email: str
) -> PaperTradingStatus:
    """Return the current paper-trading flag for ``email``."""
    cleaned = _normalize_email(email)
    await _ensure_table(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled, updated_at FROM user_paper_trading WHERE user_id = $1",
            cleaned,
        )
    if row is None:
        return PaperTradingStatus(email=cleaned, enabled=False, updated_at=None)
    return PaperTradingStatus(
        email=cleaned,
        enabled=bool(row["enabled"]),
        updated_at=row["updated_at"],
    )


async def set_paper_trading(
    pool: asyncpg.Pool,
    *,
    email: str,
    enabled: bool,
) -> PaperTradingStatus:
    """Enable or disable paper trading for ``email``."""
    cleaned = _normalize_email(email)
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
            cleaned,
            enabled,
        )
        row = await conn.fetchrow(
            "SELECT enabled, updated_at FROM user_paper_trading WHERE user_id = $1",
            cleaned,
        )
    assert row is not None
    return PaperTradingStatus(
        email=cleaned,
        enabled=bool(row["enabled"]),
        updated_at=row["updated_at"],
    )
