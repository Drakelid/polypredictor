"""Per-user push / alert preferences (M3.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from asyncpg import Pool

from .users import ensure_user_by_email

KNOWN_SIGNAL_TYPES = (
    "whale_open",
    "whale_resize",
    "whale_close",
    "arb",
    "external_divergence",
    "large_print",
    "book_shock",
)

DEFAULT_EVENT_TYPES = (
    "whale_open",
    "whale_resize",
    "arb",
    "external_divergence",
    "large_print",
    "book_shock",
)


@dataclass(frozen=True)
class PushPreferences:
    email_enabled: bool
    email_to: str | None
    webhook_enabled: bool
    webhook_url: str | None
    min_severity: float
    event_types: list[str]
    condition_ids: list[str]
    updated_at: datetime | None


@dataclass(frozen=True)
class PushPreferencesInput:
    email_enabled: bool
    email_to: str | None
    webhook_enabled: bool
    webhook_url: str | None
    min_severity: float
    event_types: list[str]
    condition_ids: list[str]


async def get_preferences(*, pool: Pool, email: str) -> PushPreferences:
    user_id = await ensure_user_by_email(pool, email=email)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT email_enabled, email_to, webhook_enabled, webhook_url,
                   min_severity, event_types, condition_ids, updated_at
            FROM user_push_preferences
            WHERE user_id = $1
            """,
            user_id,
        )
    if row is None:
        return PushPreferences(
            email_enabled=False,
            email_to=email,
            webhook_enabled=False,
            webhook_url=None,
            min_severity=1.0,
            event_types=list(DEFAULT_EVENT_TYPES),
            condition_ids=[],
            updated_at=None,
        )
    return _preferences_from_row(row)


async def update_preferences(
    *,
    pool: Pool,
    email: str,
    payload: PushPreferencesInput,
) -> PushPreferences:
    user_id = await ensure_user_by_email(pool, email=email)
    normalized = _normalize_payload(payload, default_email=email)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_push_preferences (
                user_id,
                email_enabled,
                email_to,
                webhook_enabled,
                webhook_url,
                min_severity,
                event_types,
                condition_ids
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::text[], $8::text[])
            ON CONFLICT (user_id) DO UPDATE
            SET email_enabled = EXCLUDED.email_enabled,
                email_to = EXCLUDED.email_to,
                webhook_enabled = EXCLUDED.webhook_enabled,
                webhook_url = EXCLUDED.webhook_url,
                min_severity = EXCLUDED.min_severity,
                event_types = EXCLUDED.event_types,
                condition_ids = EXCLUDED.condition_ids
            RETURNING email_enabled, email_to, webhook_enabled, webhook_url,
                      min_severity, event_types, condition_ids, updated_at
            """,
            user_id,
            normalized.email_enabled,
            normalized.email_to,
            normalized.webhook_enabled,
            normalized.webhook_url,
            normalized.min_severity,
            normalized.event_types,
            normalized.condition_ids,
        )
    assert row is not None
    return _preferences_from_row(row)


def _normalize_payload(
    payload: PushPreferencesInput,
    *,
    default_email: str,
) -> PushPreferencesInput:
    min_severity = max(0.0, float(payload.min_severity))
    email_to = (payload.email_to or "").strip() or default_email
    webhook_url = (payload.webhook_url or "").strip() or None
    if payload.email_enabled and "@" not in email_to:
        raise ValueError("email_to must be a valid email address")
    if payload.webhook_enabled and not _is_http_url(webhook_url):
        raise ValueError("webhook_url must be an absolute http(s) URL")
    return PushPreferencesInput(
        email_enabled=bool(payload.email_enabled),
        email_to=email_to if payload.email_enabled else None,
        webhook_enabled=bool(payload.webhook_enabled),
        webhook_url=webhook_url if payload.webhook_enabled else None,
        min_severity=min_severity,
        event_types=_normalize_event_types(payload.event_types),
        condition_ids=_normalize_condition_ids(payload.condition_ids),
    )


def _normalize_event_types(values: list[str]) -> list[str]:
    normalized = {value.strip() for value in values if value.strip() in KNOWN_SIGNAL_TYPES}
    return sorted(normalized) if normalized else list(DEFAULT_EVENT_TYPES)


def _normalize_condition_ids(values: list[str]) -> list[str]:
    normalized = {value.strip() for value in values if value.strip()}
    return sorted(normalized)


def _preferences_from_row(row: Any) -> PushPreferences:
    return PushPreferences(
        email_enabled=bool(row["email_enabled"]),
        email_to=str(row["email_to"]) if row["email_to"] is not None else None,
        webhook_enabled=bool(row["webhook_enabled"]),
        webhook_url=str(row["webhook_url"]) if row["webhook_url"] is not None else None,
        min_severity=float(row["min_severity"]),
        event_types=[str(value) for value in (row["event_types"] or [])],
        condition_ids=[str(value) for value in (row["condition_ids"] or [])],
        updated_at=_coerce_utc(row["updated_at"]) if row["updated_at"] is not None else None,
    )


def _is_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _coerce_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
