from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.privacy_prefs import (
    PrivacyPreferencesInput,
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


@pytest.mark.asyncio
async def test_get_preferences_defaults_to_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.privacy_prefs.ensure_user_by_email", fake_ensure_user_by_email
    )
    pool = _FakePool(_FakeConn(None))

    prefs = await get_preferences(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
    )

    assert prefs.cross_user_learning_opt_in is False
    assert prefs.updated_at is None


@pytest.mark.asyncio
async def test_update_preferences_persists_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 4, 24, 12, tzinfo=UTC)

    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.privacy_prefs.ensure_user_by_email", fake_ensure_user_by_email
    )
    conn = _FakeConn(
        {
            "cross_user_learning_opt_in": True,
            "updated_at": now,
        }
    )
    pool = _FakePool(conn)

    prefs = await update_preferences(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        payload=PrivacyPreferencesInput(cross_user_learning_opt_in=True),
    )

    assert prefs.cross_user_learning_opt_in is True
    assert prefs.updated_at == now
    assert conn.calls[0][1][1] is True
