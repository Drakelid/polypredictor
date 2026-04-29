"""Shared user helpers for user-scoped API modules.

Two helpers, intentionally split:

* :func:`ensure_user_by_email` — idempotent upsert keyed on email. Used by
  every route that has been migrated to per-user data isolation.
* :func:`ensure_demo_user` — thin wrapper that resolves to the demo user
  via :func:`ensure_user_by_email`. Kept for not-yet-migrated callers and
  for system services (e.g. ``journal_auto_sync``) that operate on the
  demo user record by design. Once every callsite passes through an
  authenticated email, this can be deleted.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from asyncpg import Pool

from .settings import Settings


async def ensure_user_by_email(
    pool: Pool,
    *,
    email: str,
    display_name: str | None = None,
) -> UUID:
    """Idempotent upsert of a `users` row keyed on email.

    Returns the row id. Raises :class:`ValueError` for blank or malformed
    email — callers (route handlers) should already be feeding in an
    authenticated email from :func:`api.auth.current_user_email`, so this
    is a defensive check, not a validation layer.
    """
    cleaned = (email or "").strip().lower()
    if not cleaned or "@" not in cleaned:
        raise ValueError("email is required")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, display_name)
            VALUES ($1, $2)
            ON CONFLICT (email) DO UPDATE
            SET display_name = COALESCE(EXCLUDED.display_name, users.display_name)
            RETURNING id
            """,
            cleaned,
            display_name,
        )
    assert row is not None
    return cast(UUID, row["id"])


async def ensure_demo_user(pool: Pool, settings: Settings) -> UUID:
    """Resolve (or create) the well-known demo user record.

    Used by:
    * Modules that haven't been migrated to per-user isolation yet
      (clob_credentials, journal, polymarket_account, privacy_prefs,
      push_prefs, provider_credentials, tuning).
    * The lifespan-attached ``journal_auto_sync`` service, which operates
      on the demo user by design (system-level, not a user request).
    """
    return await ensure_user_by_email(
        pool,
        email=settings.journal_demo_user_email,
        display_name=settings.journal_demo_user_name,
    )


__all__ = [
    "ensure_demo_user",
    "ensure_user_by_email",
]
