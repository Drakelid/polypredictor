"""Load operator provider credentials stored by the API settings UI."""

from __future__ import annotations

import base64
from typing import Any

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .settings import IngestSettings

_AES_NONCE_LEN = 12


_PROVIDER_FIELD_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "glassnode": (("api_key", "glassnode_api_key"),),
    "dune": (("api_key", "dune_api_key"),),
    "x_api": (("bearer_token", "x_bearer_token"),),
}

_STORAGE_FIELDS = ("api_key_ct", "api_secret_ct", "passphrase_ct")


async def settings_with_stored_provider_credentials(
    settings: IngestSettings,
) -> IngestSettings:
    """Overlay missing env credentials with encrypted values from Postgres.

    Environment variables remain authoritative. Stored provider credentials fill
    only blank settings so emergency env overrides still work.
    """
    if not (settings.user_secret_encryption_key_b64 or "").strip():
        return settings

    providers = [
        provider
        for provider, fields in _PROVIDER_FIELD_MAP.items()
        if any(not getattr(settings, setting_name) for _, setting_name in fields)
    ]
    if not providers:
        return settings

    try:
        key = _decode_encryption_key(settings)
        conn = await asyncpg.connect(dsn=settings.postgres_dsn)
    except Exception:
        return settings

    try:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (provider)
                   provider, api_key_ct, api_secret_ct, passphrase_ct
            FROM user_api_keys_encrypted
            WHERE provider = ANY($1::text[])
            ORDER BY provider, rotated_at DESC NULLS LAST, created_at DESC
            """,
            providers,
        )
    finally:
        await conn.close()

    updates: dict[str, str] = {}
    for row in rows:
        provider = str(row["provider"])
        fields = _PROVIDER_FIELD_MAP.get(provider, ())
        for idx, (_stored_name, setting_name) in enumerate(fields):
            if getattr(settings, setting_name):
                continue
            value = _decrypt_optional(row[_STORAGE_FIELDS[idx]], key)
            if value:
                updates[setting_name] = value
    return settings.model_copy(update=updates) if updates else settings


def _decode_encryption_key(settings: IngestSettings) -> bytes:
    raw = (settings.user_secret_encryption_key_b64 or "").strip()
    padded = raw + "=" * (-len(raw) % 4)
    key = base64.urlsafe_b64decode(padded.encode("ascii"))
    if len(key) != 32:
        raise ValueError("USER_SECRET_ENCRYPTION_KEY_B64 must decode to 32 bytes")
    return key


def _decrypt_optional(ciphertext_blob: Any, key: bytes) -> str | None:
    if ciphertext_blob is None:
        return None
    blob = bytes(ciphertext_blob)
    if len(blob) <= _AES_NONCE_LEN:
        return None
    nonce = blob[:_AES_NONCE_LEN]
    ciphertext = blob[_AES_NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")
