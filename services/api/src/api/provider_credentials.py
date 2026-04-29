"""Encrypted user-scoped provider credential storage.

This module handles provider credentials that are configured from the web
settings UI. Values are write-only from the API surface: callers can see
whether each field is configured, but plaintext secrets are never returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from asyncpg import Pool

from .clob_credentials import _decode_encryption_key, _decrypt_secret, _encrypt_secret
from .settings import Settings
from .users import ensure_user_by_email


@dataclass(frozen=True)
class CredentialFieldSpec:
    name: str
    label: str
    secret: bool = True
    required: bool = False
    placeholder: str = ""


@dataclass(frozen=True)
class ProviderCredentialSpec:
    provider: str
    label: str
    description: str
    fields: tuple[CredentialFieldSpec, ...]


@dataclass(frozen=True)
class ProviderCredentialStatus:
    provider: str
    label: str
    description: str
    fields: tuple[CredentialFieldSpec, ...]
    configured: bool
    configured_fields: tuple[str, ...]
    created_at: datetime | None
    rotated_at: datetime | None


PROVIDER_SPECS: tuple[ProviderCredentialSpec, ...] = (
    ProviderCredentialSpec(
        provider="polymarket_clob",
        label="Polymarket CLOB",
        description="User WebSocket auto-journal sync.",
        fields=(
            CredentialFieldSpec("api_key", "API Key", required=True, placeholder="pk_live_..."),
            CredentialFieldSpec("api_secret", "API Secret", required=True, placeholder="secret"),
            CredentialFieldSpec("passphrase", "Passphrase", required=True, placeholder="passphrase"),
            CredentialFieldSpec("proxy_wallet", "Proxy Wallet", secret=False, placeholder="0x..."),
        ),
    ),
    ProviderCredentialSpec(
        provider="cme_fedwatch",
        label="CME FedWatch",
        description="OAuth credentials for rate-market baselines.",
        fields=(
            CredentialFieldSpec("api_id", "API ID", required=True, placeholder="client id"),
            CredentialFieldSpec("api_secret", "API Secret", required=True, placeholder="secret"),
        ),
    ),
    ProviderCredentialSpec(
        provider="x_api",
        label="X / Twitter API",
        description="Bearer token for the filtered social stream worker.",
        fields=(
            CredentialFieldSpec("bearer_token", "Bearer Token", required=True, placeholder="token"),
        ),
    ),
    ProviderCredentialSpec(
        provider="reddit",
        label="Reddit API",
        description="Client credentials for Reddit social ingestion.",
        fields=(
            CredentialFieldSpec("client_id", "Client ID", required=True, placeholder="client id"),
            CredentialFieldSpec("client_secret", "Client Secret", required=True, placeholder="secret"),
        ),
    ),
    ProviderCredentialSpec(
        provider="glassnode",
        label="Glassnode",
        description="API key for on-chain metrics.",
        fields=(
            CredentialFieldSpec("api_key", "API Key", required=True, placeholder="key"),
        ),
    ),
    ProviderCredentialSpec(
        provider="dune",
        label="Dune",
        description="API key for Dune on-chain query results.",
        fields=(
            CredentialFieldSpec("api_key", "API Key", required=True, placeholder="key"),
        ),
    ),
    ProviderCredentialSpec(
        provider="stripe",
        label="Stripe",
        description="Billing secret, webhook secret, and price ID.",
        fields=(
            CredentialFieldSpec("secret_key", "Secret Key", required=True, placeholder="sk_live_..."),
            CredentialFieldSpec(
                "webhook_secret",
                "Webhook Secret",
                required=False,
                placeholder="whsec_...",
            ),
            CredentialFieldSpec("price_id", "Price ID", required=False, placeholder="price_..."),
        ),
    ),
)

_SPECS_BY_PROVIDER = {spec.provider: spec for spec in PROVIDER_SPECS}
_STORAGE_FIELDS = ("api_key_ct", "api_secret_ct", "passphrase_ct")


async def list_credential_statuses(
    *,
    pool: Pool,
    email: str,
    settings: Settings,
) -> list[ProviderCredentialStatus]:
    user_id = await ensure_user_by_email(pool, email=email)
    providers = tuple(_SPECS_BY_PROVIDER)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT provider, api_key_ct, api_secret_ct, passphrase_ct, created_at, rotated_at
            FROM user_api_keys_encrypted
            WHERE user_id = $1
              AND provider = ANY($2::text[])
            """,
            user_id,
            list(providers),
        )
    row_by_provider = {str(row["provider"]): row for row in rows}
    key = _decode_encryption_key(settings) if row_by_provider else None
    return [
        _status_from_row(spec, row_by_provider.get(spec.provider), key)
        for spec in PROVIDER_SPECS
    ]


async def update_provider_credentials(
    *,
    pool: Pool,
    email: str,
    settings: Settings,
    provider: str,
    values: dict[str, str | None],
) -> ProviderCredentialStatus:
    spec = _SPECS_BY_PROVIDER.get(provider)
    if spec is None:
        raise ValueError(f"unsupported credential provider: {provider}")
    cleaned = {
        field.name: (values.get(field.name) or "").strip()
        for field in spec.fields
    }
    if all(not value for value in cleaned.values()):
        return await _delete_provider_credentials(
            pool=pool, email=email, spec=spec
        )

    missing = [
        field.label
        for field in spec.fields
        if field.required and not cleaned.get(field.name)
    ]
    if missing:
        raise ValueError(f"missing required credential fields: {', '.join(missing)}")

    key = _decode_encryption_key(settings)
    encrypted_values = _encrypt_values_for_storage(spec, cleaned, key)
    user_id = await ensure_user_by_email(pool, email=email)
    async with pool.acquire() as conn:
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
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (user_id, provider) DO UPDATE
            SET api_key_ct = EXCLUDED.api_key_ct,
                api_secret_ct = EXCLUDED.api_secret_ct,
                passphrase_ct = EXCLUDED.passphrase_ct,
                proxy_wallet = EXCLUDED.proxy_wallet,
                rotated_at = NOW()
            RETURNING provider, api_key_ct, api_secret_ct, passphrase_ct, created_at, rotated_at
            """,
            user_id,
            spec.provider,
            encrypted_values[0],
            encrypted_values[1],
            encrypted_values[2],
            cleaned.get("proxy_wallet") or None,
        )
    assert row is not None
    return _status_from_row(spec, row, key)


async def load_provider_credentials(
    *,
    pool: Pool,
    email: str,
    settings: Settings,
    provider: str,
) -> dict[str, str]:
    """Load decrypted credentials for internal server-side provider clients.

    Server-side callers (e.g. billing) supply ``email`` explicitly. For
    system-level providers (Stripe, CME) the caller passes
    ``settings.journal_demo_user_email`` so the system tenant owns the
    keys; user-scoped providers receive the authenticated email.
    """
    spec = _SPECS_BY_PROVIDER.get(provider)
    if spec is None:
        raise ValueError(f"unsupported credential provider: {provider}")
    user_id = await ensure_user_by_email(pool, email=email)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT api_key_ct, api_secret_ct, passphrase_ct
            FROM user_api_keys_encrypted
            WHERE user_id = $1
              AND provider = $2
            ORDER BY rotated_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            user_id,
            provider,
        )
    if row is None:
        return {}
    key = _decode_encryption_key(settings)
    values: dict[str, str] = {}
    for idx, field in enumerate(spec.fields[:3]):
        blob = row[_STORAGE_FIELDS[idx]]
        if blob is None:
            continue
        value = _decrypt_secret(blob, key)
        if value:
            values[field.name] = value
    return values


async def _delete_provider_credentials(
    *,
    pool: Pool,
    email: str,
    spec: ProviderCredentialSpec,
) -> ProviderCredentialStatus:
    user_id = await ensure_user_by_email(pool, email=email)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM user_api_keys_encrypted
            WHERE user_id = $1
              AND provider = $2
            """,
            user_id,
            spec.provider,
        )
    return ProviderCredentialStatus(
        provider=spec.provider,
        label=spec.label,
        description=spec.description,
        fields=spec.fields,
        configured=False,
        configured_fields=(),
        created_at=None,
        rotated_at=None,
    )


def _encrypt_values_for_storage(
    spec: ProviderCredentialSpec,
    values: dict[str, str],
    key: bytes,
) -> tuple[bytes, bytes, bytes]:
    ordered_values = [values.get(field.name, "") for field in spec.fields[:3]]
    while len(ordered_values) < len(_STORAGE_FIELDS):
        ordered_values.append("")
    return (
        _encrypt_secret(ordered_values[0], key),
        _encrypt_secret(ordered_values[1], key),
        _encrypt_secret(ordered_values[2], key),
    )


def _status_from_row(
    spec: ProviderCredentialSpec,
    row: Any | None,
    key: bytes | None,
) -> ProviderCredentialStatus:
    if row is None or key is None:
        return ProviderCredentialStatus(
            provider=spec.provider,
            label=spec.label,
            description=spec.description,
            fields=spec.fields,
            configured=False,
            configured_fields=(),
            created_at=None,
            rotated_at=None,
        )

    configured_fields: list[str] = []
    for idx, field in enumerate(spec.fields[:3]):
        blob = row[_STORAGE_FIELDS[idx]]
        if blob is not None and _decrypt_secret(blob, key):
            configured_fields.append(field.name)

    return ProviderCredentialStatus(
        provider=spec.provider,
        label=spec.label,
        description=spec.description,
        fields=spec.fields,
        configured=bool(configured_fields),
        configured_fields=tuple(configured_fields),
        created_at=_coerce_utc(row["created_at"]) if row["created_at"] is not None else None,
        rotated_at=_coerce_utc(row["rotated_at"]) if row["rotated_at"] is not None else None,
    )


def _coerce_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
