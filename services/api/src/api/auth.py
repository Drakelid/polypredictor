"""Email/magic-link authentication (M0.6).

Tokens are persisted in Postgres (``auth_magic_links``) so the auth flow
survives restarts and works behind a horizontally-scaled deployment.
Verification is one-shot: the ``used_at`` column is stamped on the first
successful verification and subsequent calls against the same token are
rejected. Expired tokens are rejected on the same path so a stale token
can never be redeemed.

This is intentionally NOT a full session/cookie story — the PRD §M0.6
calls for "email/magic-link sufficient for internal use", and the rest of
the API still relies on the implicit demo-user pattern (see
``ensure_demo_user`` in :mod:`api.users`). The token verification step
returns the email so a downstream session layer (NextAuth or equivalent)
can take it from there.

Pool wiring: the router depends on :func:`get_pool_dependency`, which the
top-level FastAPI app overrides via
``app.dependency_overrides[get_pool_dependency] = real_pool_provider``.
That keeps this module decoupled from ``api.main`` and makes the route
trivially testable with a fake pool.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from asyncpg import Pool
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

_DEFAULT_TOKEN_TTL = timedelta(minutes=15)
_TOKEN_BYTES = 32


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkResponse(BaseModel):
    message: str
    # The token is returned in-band for now (no email service wired). In
    # production an outbound mailer would consume the token and the API
    # would respond with just the message.
    token: str


class MagicLinkVerificationResponse(BaseModel):
    message: str
    email: EmailStr


@dataclass(frozen=True)
class IssuedMagicLink:
    token: str
    email: str
    expires_at: datetime


@dataclass(frozen=True)
class MagicLinkVerification:
    email: str
    verified_at: datetime


async def issue_magic_link(
    *,
    pool: Pool,
    email: str,
    ttl: timedelta = _DEFAULT_TOKEN_TTL,
    now: datetime | None = None,
) -> IssuedMagicLink:
    """Generate a one-shot token and persist it for ``email``.

    Multiple outstanding tokens per email are allowed — verifying any one
    of them succeeds. The expired/used dropper runs out-of-band; we do
    NOT auto-purge here so the function stays write-only fast.
    """
    issued_at = now or datetime.now(tz=UTC)
    expires_at = issued_at + ttl
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    cleaned_email = (email or "").strip()
    if not cleaned_email:
        raise ValueError("email is required")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth_magic_links (token, email, expires_at, created_at)
            VALUES ($1, $2, $3, $4)
            """,
            token,
            cleaned_email,
            expires_at,
            issued_at,
        )
    return IssuedMagicLink(token=token, email=cleaned_email, expires_at=expires_at)


async def verify_magic_link_token(
    *,
    pool: Pool,
    token: str,
    now: datetime | None = None,
) -> MagicLinkVerification:
    """Mark ``token`` as used and return the associated email.

    Raises :class:`ValueError` when the token is unknown, already used, or
    expired. The marker is set atomically so two concurrent verifications
    can't both succeed.
    """
    asked_at = now or datetime.now(tz=UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE auth_magic_links
               SET used_at = $1
             WHERE token = $2
               AND used_at IS NULL
               AND expires_at > $1
            RETURNING email, used_at
            """,
            asked_at,
            (token or "").strip(),
        )
    if row is None:
        raise ValueError("invalid or expired token")
    return MagicLinkVerification(
        email=str(row["email"]),
        verified_at=row["used_at"],
    )


async def purge_expired_magic_links(
    *,
    pool: Pool,
    now: datetime | None = None,
) -> int:
    """Drop tokens whose expiry is in the past or that have already been
    consumed. Returns the row count for observability."""
    asked_at = now or datetime.now(tz=UTC)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM auth_magic_links
             WHERE used_at IS NOT NULL OR expires_at <= $1
            """,
            asked_at,
        )
    if isinstance(result, str) and result.startswith("DELETE"):
        try:
            return int(result.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            return 0
    return 0


def get_pool_dependency() -> Any:
    """Pool dependency placeholder.

    The FastAPI app overrides this via ``app.dependency_overrides`` so the
    auth router gets the real connection pool at runtime.
    """
    raise RuntimeError(
        "auth.get_pool_dependency must be overridden via app.dependency_overrides",
    )


router = APIRouter(tags=["auth"])


@router.post("/v1/auth/request-magic-link", response_model=MagicLinkResponse)
async def request_magic_link(
    payload: MagicLinkRequest,
    pool: Pool = Depends(get_pool_dependency),
) -> MagicLinkResponse:
    issued = await issue_magic_link(pool=pool, email=str(payload.email))
    return MagicLinkResponse(message="Magic link generated", token=issued.token)


@router.get("/v1/auth/verify-magic-link", response_model=MagicLinkVerificationResponse)
async def verify_magic_link(
    token: str,
    pool: Pool = Depends(get_pool_dependency),
) -> MagicLinkVerificationResponse:
    try:
        verified = await verify_magic_link_token(pool=pool, token=token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return MagicLinkVerificationResponse(
        message="Magic link verified",
        email=verified.email,
    )


__all__ = [
    "IssuedMagicLink",
    "MagicLinkRequest",
    "MagicLinkResponse",
    "MagicLinkVerification",
    "MagicLinkVerificationResponse",
    "get_pool_dependency",
    "issue_magic_link",
    "purge_expired_magic_links",
    "router",
    "verify_magic_link_token",
]
