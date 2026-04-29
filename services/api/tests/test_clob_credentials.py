from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest
from api.clob_credentials import (
    ClobCredentialInput,
    get_credential_status,
    load_user_auth,
    update_credentials,
)


class _FakeConn:
    def __init__(self) -> None:
        self.created_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
        self.rotated_at = datetime(2026, 4, 24, 13, tzinfo=UTC)
        self.row: dict[str, object] | None = None
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object):
        self.fetchrow_calls.append((query, args))
        if "INSERT INTO user_api_keys_encrypted" in query:
            self.row = {
                "api_key_ct": args[1],
                "api_secret_ct": args[2],
                "passphrase_ct": args[3],
                "proxy_wallet": args[4],
                "created_at": self.created_at,
                "rotated_at": self.rotated_at,
            }
            return {
                "proxy_wallet": args[4],
                "created_at": self.created_at,
                "rotated_at": self.rotated_at,
            }
        if "SELECT proxy_wallet, created_at, rotated_at" in query:
            if self.row is None:
                return None
            return {
                "proxy_wallet": self.row["proxy_wallet"],
                "created_at": self.row["created_at"],
                "rotated_at": self.row["rotated_at"],
            }
        if "SELECT api_key_ct, api_secret_ct, passphrase_ct" in query:
            if self.row is None:
                return None
            return {
                "api_key_ct": self.row["api_key_ct"],
                "api_secret_ct": self.row["api_secret_ct"],
                "passphrase_ct": self.row["passphrase_ct"],
            }
        return None

    async def execute(self, query: str, *args: object):
        self.execute_calls.append((query, args))
        if "DELETE FROM user_api_keys_encrypted" in query:
            self.row = None
        return "DELETE 1"


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


def _settings_with_key() -> object:
    key = base64.urlsafe_b64encode(b"0" * 32).decode("ascii").rstrip("=")
    return type("Settings", (), {"user_secret_encryption_key_b64": key})()


@pytest.mark.asyncio
async def test_get_credential_status_defaults_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.clob_credentials.ensure_user_by_email", fake_ensure_user_by_email
    )
    pool = _FakePool(_FakeConn())

    status = await get_credential_status(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        settings=_settings_with_key(),  # type: ignore[arg-type]
    )

    assert status.configured is False
    assert status.proxy_wallet is None


@pytest.mark.asyncio
async def test_update_credentials_encrypts_and_loads_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.clob_credentials.ensure_user_by_email", fake_ensure_user_by_email
    )
    conn = _FakeConn()
    pool = _FakePool(conn)
    settings = _settings_with_key()

    status = await update_credentials(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        settings=settings,  # type: ignore[arg-type]
        payload=ClobCredentialInput(
            api_key="api-key",
            api_secret="api-secret",
            passphrase="passphrase",
            proxy_wallet="0xabc",
        ),
    )
    auth = await load_user_auth(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        settings=settings,  # type: ignore[arg-type]
    )

    assert status.configured is True
    assert status.proxy_wallet == "0xabc"
    assert auth is not None
    assert auth.api_key == "api-key"
    assert auth.secret == "api-secret"
    assert auth.passphrase == "passphrase"
    assert conn.row is not None
    assert conn.row["api_key_ct"] != b"api-key"


@pytest.mark.asyncio
async def test_update_credentials_requires_encryption_key_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.clob_credentials.ensure_user_by_email", fake_ensure_user_by_email
    )
    conn = _FakeConn()
    pool = _FakePool(conn)

    with pytest.raises(ValueError, match="USER_SECRET_ENCRYPTION_KEY_B64"):
        await update_credentials(
            pool=pool,  # type: ignore[arg-type]
            email="alice@example.com",
            settings=type(
                "Settings",
                (),
                {
                    "app_env": "development",
                    "user_secret_encryption_key_b64": None,
                },
            )(),  # type: ignore[arg-type]
            payload=ClobCredentialInput(
                api_key="api-key",
                api_secret="api-secret",
                passphrase="passphrase",
            ),
        )


@pytest.mark.asyncio
async def test_update_credentials_requires_encryption_key_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.clob_credentials.ensure_user_by_email", fake_ensure_user_by_email
    )
    pool = _FakePool(_FakeConn())

    with pytest.raises(ValueError, match="USER_SECRET_ENCRYPTION_KEY_B64"):
        await update_credentials(
            pool=pool,  # type: ignore[arg-type]
            email="alice@example.com",
            settings=type(
                "Settings",
                (),
                {
                    "app_env": "production",
                    "user_secret_encryption_key_b64": None,
                },
            )(),  # type: ignore[arg-type]
            payload=ClobCredentialInput(
                api_key="api-key",
                api_secret="api-secret",
                passphrase="passphrase",
            ),
        )


@pytest.mark.asyncio
async def test_update_credentials_clear_deletes_saved_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.clob_credentials.ensure_user_by_email", fake_ensure_user_by_email
    )
    conn = _FakeConn()
    pool = _FakePool(conn)
    settings = _settings_with_key()

    await update_credentials(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        settings=settings,  # type: ignore[arg-type]
        payload=ClobCredentialInput(
            api_key="api-key",
            api_secret="api-secret",
            passphrase="passphrase",
            proxy_wallet="0xabc",
        ),
    )
    cleared = await update_credentials(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        settings=settings,  # type: ignore[arg-type]
        payload=ClobCredentialInput(
            api_key=None,
            api_secret=None,
            passphrase=None,
            proxy_wallet=None,
        ),
    )
    auth = await load_user_auth(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        settings=settings,  # type: ignore[arg-type]
    )

    assert cleared.configured is False
    assert conn.execute_calls
    assert auth is None
