"""Tests for the api.users helpers (ensure_user_by_email + ensure_demo_user)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from api.settings import Settings
from api.users import ensure_demo_user, ensure_user_by_email


@dataclass
class _UserRow:
    id: UUID
    email: str
    display_name: str | None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class _FakeConn:
    users: dict[str, _UserRow] = field(default_factory=dict)

    async def fetchrow(self, query: str, *args: Any) -> _UserRow:
        # Simulate ON CONFLICT (email) DO UPDATE.
        cleaned_email, display_name = args
        existing = self.users.get(cleaned_email)
        if existing is None:
            row = _UserRow(id=uuid4(), email=cleaned_email, display_name=display_name)
            self.users[cleaned_email] = row
            return row
        if display_name is not None:
            existing.display_name = display_name
        return existing

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _FakePool:
    conn: _FakeConn = field(default_factory=_FakeConn)

    def acquire(self) -> _FakeConn:
        return self.conn


@pytest.mark.asyncio
async def test_ensure_user_by_email_lowercases_and_strips() -> None:
    pool = _FakePool()
    user_id = await ensure_user_by_email(pool, email="  Alice@Example.COM  ")
    assert "alice@example.com" in pool.conn.users
    assert pool.conn.users["alice@example.com"].id == user_id


@pytest.mark.asyncio
async def test_ensure_user_by_email_is_idempotent() -> None:
    pool = _FakePool()
    a = await ensure_user_by_email(pool, email="alice@example.com")
    b = await ensure_user_by_email(pool, email="alice@example.com")
    assert a == b
    assert len(pool.conn.users) == 1


@pytest.mark.asyncio
async def test_ensure_user_by_email_isolates_distinct_users() -> None:
    pool = _FakePool()
    a = await ensure_user_by_email(pool, email="alice@example.com")
    b = await ensure_user_by_email(pool, email="bob@example.com")
    assert a != b
    assert len(pool.conn.users) == 2


@pytest.mark.asyncio
async def test_ensure_user_by_email_rejects_blank() -> None:
    pool = _FakePool()
    with pytest.raises(ValueError, match="email is required"):
        await ensure_user_by_email(pool, email="   ")


@pytest.mark.asyncio
async def test_ensure_user_by_email_rejects_malformed() -> None:
    pool = _FakePool()
    with pytest.raises(ValueError, match="email is required"):
        await ensure_user_by_email(pool, email="not-an-email")


@pytest.mark.asyncio
async def test_ensure_user_by_email_updates_display_name_on_conflict() -> None:
    pool = _FakePool()
    await ensure_user_by_email(pool, email="alice@example.com", display_name="Alice")
    await ensure_user_by_email(pool, email="alice@example.com", display_name="Alice Smith")
    assert pool.conn.users["alice@example.com"].display_name == "Alice Smith"


@pytest.mark.asyncio
async def test_ensure_user_by_email_preserves_display_name_when_none_passed() -> None:
    pool = _FakePool()
    await ensure_user_by_email(pool, email="alice@example.com", display_name="Alice")
    await ensure_user_by_email(pool, email="alice@example.com")  # no display_name
    assert pool.conn.users["alice@example.com"].display_name == "Alice"


@pytest.mark.asyncio
async def test_ensure_demo_user_resolves_via_settings() -> None:
    pool = _FakePool()
    settings = Settings(
        journal_demo_user_email="demo@polypredictor.local",
        journal_demo_user_name="Demo User",
    )
    user_id = await ensure_demo_user(pool, settings)
    assert pool.conn.users["demo@polypredictor.local"].id == user_id
    assert pool.conn.users["demo@polypredictor.local"].display_name == "Demo User"
