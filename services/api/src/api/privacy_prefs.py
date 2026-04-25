"""Per-user privacy preferences (M7.4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from asyncpg import Pool

from .settings import Settings
from .users import ensure_demo_user


@dataclass(frozen=True)
class PrivacyPreferences:
    cross_user_learning_opt_in: bool
    updated_at: datetime | None


@dataclass(frozen=True)
class PrivacyPreferencesInput:
    cross_user_learning_opt_in: bool


async def get_preferences(*, pool: Pool, settings: Settings) -> PrivacyPreferences:
    user_id = await ensure_demo_user(pool, settings)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT cross_user_learning_opt_in, updated_at
            FROM user_privacy_preferences
            WHERE user_id = $1
            """,
            user_id,
        )
    if row is None:
        return PrivacyPreferences(
            cross_user_learning_opt_in=False,
            updated_at=None,
        )
    return _preferences_from_row(row)


async def update_preferences(
    *,
    pool: Pool,
    settings: Settings,
    payload: PrivacyPreferencesInput,
) -> PrivacyPreferences:
    user_id = await ensure_demo_user(pool, settings)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_privacy_preferences (
                user_id,
                cross_user_learning_opt_in
            )
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET cross_user_learning_opt_in = EXCLUDED.cross_user_learning_opt_in
            RETURNING cross_user_learning_opt_in, updated_at
            """,
            user_id,
            bool(payload.cross_user_learning_opt_in),
        )
    assert row is not None
    return _preferences_from_row(row)


def _preferences_from_row(row: Any) -> PrivacyPreferences:
    return PrivacyPreferences(
        cross_user_learning_opt_in=bool(row["cross_user_learning_opt_in"]),
        updated_at=_coerce_utc(row["updated_at"]) if row["updated_at"] is not None else None,
    )


def _coerce_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
