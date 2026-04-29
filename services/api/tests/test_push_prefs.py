from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.push_prefs import (
    DEFAULT_EVENT_TYPES,
    PushPreferencesInput,
    _normalize_condition_ids,
    _normalize_event_types,
    get_preferences,
    update_preferences,
)


class _FakeConn:
    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object):
        self.calls.append((query, args))
        return self.row


class _AcquireCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self.conn)


def test_normalize_event_types_filters_unknown_and_dedupes() -> None:
    result = _normalize_event_types(["arb", "large_print", "arb", "bogus"])

    assert result == ["arb", "large_print"]


def test_normalize_event_types_falls_back_to_defaults() -> None:
    assert _normalize_event_types([]) == list(DEFAULT_EVENT_TYPES)


def test_normalize_condition_ids_dedupes_and_sorts() -> None:
    assert _normalize_condition_ids(["cond-b", "cond-a", "cond-a", ""]) == [
        "cond-a",
        "cond-b",
    ]


@pytest.mark.asyncio
async def test_get_preferences_returns_default_when_row_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.push_prefs.ensure_user_by_email", fake_ensure_user_by_email
    )
    pool = _FakePool(_FakeConn(None))
    email = "demo@local"

    prefs = await get_preferences(pool=pool, email=email)  # type: ignore[arg-type]

    assert prefs.email_enabled is False
    assert prefs.email_to == "demo@local"
    assert prefs.min_severity == pytest.approx(1.0)
    assert prefs.event_types == list(DEFAULT_EVENT_TYPES)
    assert prefs.condition_ids == []


@pytest.mark.asyncio
async def test_update_preferences_normalizes_and_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)

    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.push_prefs.ensure_user_by_email", fake_ensure_user_by_email
    )
    conn = _FakeConn(
        {
            "email_enabled": True,
            "email_to": "alerts@example.com",
            "webhook_enabled": True,
            "webhook_url": "https://example.com/hook",
            "min_severity": 2.0,
            "event_types": ["arb", "large_print"],
            "condition_ids": ["cond-a", "cond-b"],
            "updated_at": now,
        }
    )
    pool = _FakePool(conn)
    email = "demo@local"

    prefs = await update_preferences(
        pool=pool,  # type: ignore[arg-type]
        email=email,
        payload=PushPreferencesInput(
            email_enabled=True,
            email_to="alerts@example.com",
            webhook_enabled=True,
            webhook_url="https://example.com/hook",
            min_severity=2.0,
            event_types=["large_print", "arb", "arb"],
            condition_ids=["cond-b", "cond-a", "cond-a"],
        ),
    )

    assert prefs.event_types == ["arb", "large_print"]
    assert prefs.condition_ids == ["cond-a", "cond-b"]
    args = conn.calls[0][1]
    assert args[6] == ["arb", "large_print"]
    assert args[7] == ["cond-a", "cond-b"]


@pytest.mark.asyncio
async def test_update_preferences_rejects_bad_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.push_prefs.ensure_user_by_email", fake_ensure_user_by_email
    )
    pool = _FakePool(_FakeConn(None))
    email = "demo@local"

    with pytest.raises(ValueError, match="webhook_url"):
        await update_preferences(
            pool=pool,  # type: ignore[arg-type]
            email=email,
            payload=PushPreferencesInput(
                email_enabled=False,
                email_to=None,
                webhook_enabled=True,
                webhook_url="not-a-url",
                min_severity=1.0,
                event_types=[],
                condition_ids=[],
            ),
        )
