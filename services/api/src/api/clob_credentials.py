"""Encrypted Polymarket CLOB credential storage for user WSS (M7.1)."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from asyncpg import Pool
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from polymarket_client import UserAuth

from .settings import Settings
from .users import ensure_demo_user

_AES_NONCE_LEN = 12


@dataclass(frozen=True)
class ClobCredentialStatus:
    configured: bool
    proxy_wallet: str | None
    created_at: datetime | None
    rotated_at: datetime | None


@dataclass(frozen=True)
class ClobCredentialInput:
    api_key: str | None
    api_secret: str | None
    passphrase: str | None
    proxy_wallet: str | None = None


async def get_credential_status(
    *,
    pool: Pool,
    settings: Settings,
) -> ClobCredentialStatus:
    user_id = await ensure_demo_user(pool, settings)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT proxy_wallet, created_at, rotated_at
            FROM user_api_keys_encrypted
            WHERE user_id = $1
              AND provider = 'polymarket_clob'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            user_id,
        )
    if row is None:
        return ClobCredentialStatus(
            configured=False,
            proxy_wallet=None,
            created_at=None,
            rotated_at=None,
        )
    return _status_from_row(row)


async def update_credentials(
    *,
    pool: Pool,
    settings: Settings,
    payload: ClobCredentialInput,
) -> ClobCredentialStatus:
    user_id = await ensure_demo_user(pool, settings)
    api_key = (payload.api_key or "").strip()
    api_secret = (payload.api_secret or "").strip()
    passphrase = (payload.passphrase or "").strip()
    proxy_wallet = (payload.proxy_wallet or "").strip() or None
    async with pool.acquire() as conn:
        if not api_key and not api_secret and not passphrase:
            await conn.execute(
                """
                DELETE FROM user_api_keys_encrypted
                WHERE user_id = $1
                  AND provider = 'polymarket_clob'
                """,
                user_id,
            )
            return ClobCredentialStatus(
                configured=False,
                proxy_wallet=None,
                created_at=None,
                rotated_at=None,
            )
        if not api_key or not api_secret or not passphrase:
            raise ValueError("api_key, api_secret, and passphrase are required together")
        key = _decode_encryption_key(settings)
        row = await conn.fetchrow(
            """
            INSERT INTO user_api_keys_encrypted (
                user_id,
                provider,
                api_key_ct,
                api_secret_ct,
                passphrase_ct,
                proxy_wallet,
                rotated_at
            )
            VALUES ($1, 'polymarket_clob', $2, $3, $4, $5, NOW())
            ON CONFLICT (user_id, provider) DO UPDATE
            SET api_key_ct = EXCLUDED.api_key_ct,
                api_secret_ct = EXCLUDED.api_secret_ct,
                passphrase_ct = EXCLUDED.passphrase_ct,
                proxy_wallet = EXCLUDED.proxy_wallet,
                rotated_at = NOW()
            RETURNING proxy_wallet, created_at, rotated_at
            """,
            user_id,
            _encrypt_secret(api_key, key),
            _encrypt_secret(api_secret, key),
            _encrypt_secret(passphrase, key),
            proxy_wallet,
        )
    assert row is not None
    return _status_from_row(row)


async def load_user_auth(
    *,
    pool: Pool,
    settings: Settings,
) -> UserAuth | None:
    user_id = await ensure_demo_user(pool, settings)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT api_key_ct, api_secret_ct, passphrase_ct
            FROM user_api_keys_encrypted
            WHERE user_id = $1
              AND provider = 'polymarket_clob'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            user_id,
        )
    if row is None:
        return None
    key = _decode_encryption_key(settings)
    return UserAuth(
        api_key=_decrypt_secret(row["api_key_ct"], key),
        secret=_decrypt_secret(row["api_secret_ct"], key),
        passphrase=_decrypt_secret(row["passphrase_ct"], key),
    )


def _decode_encryption_key(settings: Settings) -> bytes:
    raw = (settings.user_secret_encryption_key_b64 or "").strip()
    if not raw:
        raise ValueError("USER_SECRET_ENCRYPTION_KEY_B64 must be configured")
    padded = raw + "=" * (-len(raw) % 4)
    try:
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:  # pragma: no cover - defensive decode path
        raise ValueError("USER_SECRET_ENCRYPTION_KEY_B64 must be valid base64") from exc
    if len(key) != 32:
        raise ValueError("USER_SECRET_ENCRYPTION_KEY_B64 must decode to 32 bytes")
    return key


def _encrypt_secret(value: str, key: bytes) -> bytes:
    nonce = os.urandom(_AES_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, value.encode("utf-8"), None)
    return nonce + ciphertext


def _decrypt_secret(ciphertext_blob: bytes, key: bytes) -> str:
    blob = bytes(ciphertext_blob)
    if len(blob) <= _AES_NONCE_LEN:
        raise ValueError("ciphertext blob is invalid")
    nonce = blob[:_AES_NONCE_LEN]
    ciphertext = blob[_AES_NONCE_LEN:]
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def _status_from_row(row: Any) -> ClobCredentialStatus:
    return ClobCredentialStatus(
        configured=True,
        proxy_wallet=str(row["proxy_wallet"]) if row["proxy_wallet"] is not None else None,
        created_at=_coerce_utc(row["created_at"]) if row["created_at"] is not None else None,
        rotated_at=_coerce_utc(row["rotated_at"]) if row["rotated_at"] is not None else None,
    )


def _coerce_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
