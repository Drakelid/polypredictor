from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from api import auth


@dataclass
class _MagicLinkRow:
    token: str
    email: str
    expires_at: datetime
    created_at: datetime
    used_at: datetime | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class _FakeConn:
    def __init__(self, rows: dict[str, _MagicLinkRow]) -> None:
        self._rows = rows
        self.executions: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executions.append((query, args))
        if "INSERT INTO auth_magic_links" in query:
            token, email, expires_at, created_at = args
            self._rows[token] = _MagicLinkRow(
                token=token,
                email=email,
                expires_at=expires_at,
                created_at=created_at,
            )
            return "INSERT 0 1"
        if "DELETE FROM auth_magic_links" in query:
            (asked_at,) = args
            removed = 0
            for token, row in list(self._rows.items()):
                if row.used_at is not None or row.expires_at <= asked_at:
                    del self._rows[token]
                    removed += 1
            return f"DELETE {removed}"
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> _MagicLinkRow | None:
        if "UPDATE auth_magic_links" in query:
            asked_at, token = args
            row = self._rows.get(token)
            if row is None or row.used_at is not None or row.expires_at <= asked_at:
                return None
            row.used_at = asked_at
            return row
        return None

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _FakePool:
    rows: dict[str, _MagicLinkRow] = field(default_factory=dict)

    def acquire(self) -> _FakeConn:
        return _FakeConn(self.rows)


@pytest.mark.asyncio
async def test_issue_magic_link_persists_token() -> None:
    pool = _FakePool()
    issued = await auth.issue_magic_link(pool=pool, email="alice@example.com")
    assert issued.email == "alice@example.com"
    assert issued.token
    assert issued.expires_at > datetime.now(tz=UTC)
    assert pool.rows[issued.token].email == "alice@example.com"


@pytest.mark.asyncio
async def test_issue_magic_link_rejects_blank_email() -> None:
    pool = _FakePool()
    with pytest.raises(ValueError):
        await auth.issue_magic_link(pool=pool, email="   ")


@pytest.mark.asyncio
async def test_verify_magic_link_consumes_token_once() -> None:
    pool = _FakePool()
    issued = await auth.issue_magic_link(pool=pool, email="alice@example.com")

    verified = await auth.verify_magic_link_token(pool=pool, token=issued.token)
    assert verified.email == "alice@example.com"

    # Second verification must fail — the token is one-shot.
    with pytest.raises(ValueError):
        await auth.verify_magic_link_token(pool=pool, token=issued.token)


@pytest.mark.asyncio
async def test_verify_magic_link_rejects_expired_token() -> None:
    pool = _FakePool()
    # Issue a token in the past so its expiry has elapsed already.
    long_ago = datetime.now(tz=UTC) - timedelta(hours=1)
    issued = await auth.issue_magic_link(
        pool=pool,
        email="alice@example.com",
        ttl=timedelta(minutes=15),
        now=long_ago,
    )
    with pytest.raises(ValueError):
        await auth.verify_magic_link_token(pool=pool, token=issued.token)


@pytest.mark.asyncio
async def test_verify_magic_link_rejects_unknown_token() -> None:
    pool = _FakePool()
    with pytest.raises(ValueError):
        await auth.verify_magic_link_token(pool=pool, token="not-a-real-token")


@pytest.mark.asyncio
async def test_purge_expired_drops_used_and_expired_rows() -> None:
    pool = _FakePool()
    fresh = await auth.issue_magic_link(pool=pool, email="alice@example.com")
    expired = await auth.issue_magic_link(
        pool=pool,
        email="bob@example.com",
        now=datetime.now(tz=UTC) - timedelta(hours=2),
    )
    used = await auth.issue_magic_link(pool=pool, email="carol@example.com")
    await auth.verify_magic_link_token(pool=pool, token=used.token)

    removed = await auth.purge_expired_magic_links(pool=pool)
    assert removed == 2
    assert fresh.token in pool.rows
    assert expired.token not in pool.rows
    assert used.token not in pool.rows


@pytest.mark.asyncio
async def test_two_concurrent_verifications_only_one_succeeds() -> None:
    """The UPDATE ... WHERE used_at IS NULL pattern is the atomic single-shot
    contract. Simulate by calling verify twice in sequence — the second must
    miss the row since the first stamped used_at."""
    pool = _FakePool()
    issued = await auth.issue_magic_link(pool=pool, email="alice@example.com")
    first = await auth.verify_magic_link_token(pool=pool, token=issued.token)
    assert first.email == "alice@example.com"
    with pytest.raises(ValueError):
        await auth.verify_magic_link_token(pool=pool, token=issued.token)
