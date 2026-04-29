"""Email/magic-link authentication + signed-cookie sessions (M0.6).

Tokens are persisted in Postgres (``auth_magic_links``) so the auth flow
survives restarts and works behind a horizontally-scaled deployment.
Verification is one-shot: the ``used_at`` column is stamped on the first
successful verification and subsequent calls against the same token are
rejected. Expired tokens are rejected on the same path so a stale token
can never be redeemed.

On verification the route sets a signed session cookie
(:func:`create_session_token` / :func:`set_session_cookie`). Routes that
need an authenticated user use :func:`current_user_email` (strict — 401
on missing/invalid cookie) or :func:`current_user_email_or_demo` (soft,
gated by the ``auth_enforce_user_routes`` setting so the dashboard keeps
working before its login UI ships). Admin routes use
:func:`current_admin_email`, which checks the email against
``settings.admin_emails``.

Per-user data isolation: every user-scoped module function in this
package now takes an ``email`` parameter and resolves the user via
``ensure_user_by_email``. The legacy ``ensure_demo_user`` helper is kept
in :mod:`api.users` for system-level services (e.g. the lifespan-attached
journal-autosync) that operate on the demo tenant by design.

Pool wiring: the router depends on :func:`get_pool_dependency`, which the
top-level FastAPI app overrides via
``app.dependency_overrides[get_pool_dependency] = real_pool_provider``.
That keeps this module decoupled from ``api.main`` and makes the route
trivially testable with a fake pool.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from asyncpg import Pool
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr

from .settings import Settings, get_settings

_DEFAULT_TOKEN_TTL = timedelta(minutes=15)
_TOKEN_BYTES = 32
_SESSION_VERSION = 1


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkResponse(BaseModel):
    message: str
    # The token is returned in-band for now (no email service wired). In
    # production an outbound mailer would consume the token and the API
    # would respond with just the message.
    token: str | None = None


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


def _decode_secret_b64(raw: str, *, name: str) -> bytes:
    padded = raw.strip() + "=" * (-len(raw.strip()) % 4)
    try:
        secret = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:  # pragma: no cover - defensive decode path
        raise ValueError(f"{name} must be valid base64") from exc
    if len(secret) < 32:
        raise ValueError(f"{name} must decode to at least 32 bytes")
    return secret


def session_secret(settings: Settings) -> bytes:
    raw = (settings.auth_session_secret_b64 or "").strip()
    if raw:
        return _decode_secret_b64(raw, name="AUTH_SESSION_SECRET_B64")
    fallback = (settings.user_secret_encryption_key_b64 or "").strip()
    if fallback:
        return _decode_secret_b64(fallback, name="USER_SECRET_ENCRYPTION_KEY_B64")
    raise ValueError("AUTH_SESSION_SECRET_B64 must be configured")


def create_session_token(
    *,
    email: str,
    settings: Settings,
    issued_at: datetime | None = None,
) -> str:
    now = issued_at or datetime.now(tz=UTC)
    expires_at = int(now.timestamp()) + settings.auth_session_ttl_seconds
    payload = {
        "v": _SESSION_VERSION,
        "email": email.strip().lower(),
        "exp": expires_at,
        "nonce": secrets.token_urlsafe(16),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    sig = hmac.new(session_secret(settings), payload_b64.encode("ascii"), hashlib.sha256)
    sig_b64 = base64.urlsafe_b64encode(sig.digest()).decode().rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def verify_session_token(
    token: str,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> str:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid session token") from exc
    expected = hmac.new(
        session_secret(settings),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        provided = base64.urlsafe_b64decode(
            (sig_b64 + "=" * (-len(sig_b64) % 4)).encode("ascii")
        )
    except Exception as exc:
        raise ValueError("invalid session token") from exc
    if not hmac.compare_digest(expected, provided):
        raise ValueError("invalid session token")
    try:
        payload_bytes = base64.urlsafe_b64decode(
            (payload_b64 + "=" * (-len(payload_b64) % 4)).encode("ascii")
        )
        payload = json.loads(payload_bytes)
    except Exception as exc:
        raise ValueError("invalid session token") from exc
    expires_at = int(payload.get("exp") or 0)
    checked_at = now or datetime.now(tz=UTC)
    if expires_at <= int(checked_at.timestamp()):
        raise ValueError("session expired")
    email = str(payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("invalid session token")
    return email


def set_session_cookie(response: Response, *, email: str, settings: Settings) -> None:
    token = create_session_token(email=email, settings=settings)
    response.set_cookie(
        settings.auth_session_cookie_name,
        token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.auth_session_cookie_secure,
        samesite="lax",
    )


async def current_user_email(request: Request) -> str:
    """Strict per-user auth: a valid session cookie is required.

    Used by routes that already drive a magic-link login flow (today: the
    operator side of credentials/journal/billing). Raises 401 on a missing
    or invalid cookie.
    """
    settings = get_settings()
    token = request.cookies.get(settings.auth_session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    try:
        return verify_session_token(token, settings=settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


async def current_user_email_or_demo(request: Request) -> str:
    """Soft per-user auth gated by ``auth_enforce_user_routes``.

    During the rollout window — while the dashboard still doesn't drive a
    magic-link login — flip ``AUTH_ENFORCE_USER_ROUTES=false`` and this
    dependency resolves to ``settings.journal_demo_user_email`` so the
    routes keep responding. Once the frontend can drive a session, set the
    flag to ``true`` and the dependency becomes equivalent to
    :func:`current_user_email`.

    A *valid* cookie always wins, regardless of the flag — passing one in
    a permissive deployment will pin the request to the cookie's email,
    not the demo user.
    """
    settings = get_settings()
    token = request.cookies.get(settings.auth_session_cookie_name)
    if token:
        try:
            return verify_session_token(token, settings=settings)
        except ValueError as exc:
            if settings.auth_enforce_user_routes:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
                ) from exc
            # When enforcement is off, an invalid cookie falls through to
            # the demo user instead of 401-ing. This mirrors the "demo
            # user fallback" behavior the routes had before this helper
            # existed; a real cookie is preferred when present.
    if settings.auth_enforce_user_routes:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return settings.journal_demo_user_email.strip().lower()


async def current_admin_email(email: str = Depends(current_user_email)) -> str:
    settings = get_settings()
    allowed = {
        item.strip().lower()
        for item in settings.admin_emails.split(",")
        if item.strip()
    }
    if allowed and email.lower() not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return email


router = APIRouter(tags=["auth"])


@router.post("/v1/auth/request-magic-link", response_model=MagicLinkResponse)
async def request_magic_link(
    payload: MagicLinkRequest,
    pool: Pool = Depends(get_pool_dependency),
) -> MagicLinkResponse:
    settings = get_settings()
    issued = await issue_magic_link(pool=pool, email=str(payload.email))
    token = issued.token if settings.auth_magic_link_in_band else None
    return MagicLinkResponse(message="Magic link generated", token=token)


@router.get("/v1/auth/verify-magic-link", response_model=MagicLinkVerificationResponse)
async def verify_magic_link(
    token: str,
    response: Response,
    pool: Pool = Depends(get_pool_dependency),
) -> MagicLinkVerificationResponse:
    try:
        verified = await verify_magic_link_token(pool=pool, token=token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    try:
        set_session_cookie(response, email=verified.email, settings=get_settings())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
    "create_session_token",
    "current_admin_email",
    "current_user_email",
    "current_user_email_or_demo",
    "get_pool_dependency",
    "issue_magic_link",
    "purge_expired_magic_links",
    "router",
    "session_secret",
    "set_session_cookie",
    "verify_magic_link_token",
    "verify_session_token",
]
