"""Simple email/magic-link authentication stub.

This module defines a minimal FastAPI router providing endpoints for
requesting and verifying magic-link based authentication. It is not
intended for production use — no database or email integration is
included, and tokens are stored in memory. The design mirrors the
planned internal-use authentication flow in M0.6 of the PRD: users
request a magic link by submitting their email and then verify
themselves via the token. In a real deployment the token would be
emailed to the user; here it is returned directly in the response for
testing purposes.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Dict

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkResponse(BaseModel):
    message: str
    token: str


class MagicLinkVerificationResponse(BaseModel):
    message: str
    email: EmailStr


router = APIRouter(tags=["auth"])

# In-memory store mapping tokens to (email, expiry)
_MAGIC_LINKS: Dict[str, tuple[str, datetime]] = {}

# Token validity period (e.g. 15 minutes)
_TOKEN_TTL = timedelta(minutes=15)


@router.post("/v1/auth/request-magic-link", response_model=MagicLinkResponse)
async def request_magic_link(payload: MagicLinkRequest) -> MagicLinkResponse:
    """Generate a magic link token for the given email.

    A random token is created and stored with an expiry. In a real
    implementation the token would be emailed to the user; for testing
    the token is returned in the response body.
    """
    token = secrets.token_urlsafe(32)
    _MAGIC_LINKS[token] = (payload.email, datetime.utcnow() + _TOKEN_TTL)
    # In production, send email with token instead of returning it.
    return MagicLinkResponse(message="Magic link generated", token=token)


@router.get("/v1/auth/verify-magic-link", response_model=MagicLinkVerificationResponse)
async def verify_magic_link(token: str) -> MagicLinkVerificationResponse:
    """Verify a magic link token.

    Checks that the token exists and has not expired, then deletes it to
    prevent reuse. Returns the associated email on success.
    """
    entry = _MAGIC_LINKS.get(token)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )
    email, expiry = entry
    if datetime.utcnow() > expiry:
        del _MAGIC_LINKS[token]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token has expired",
        )
    # Remove token after successful verification to enforce one-time use.
    del _MAGIC_LINKS[token]
    return MagicLinkVerificationResponse(
        message="Magic link verified",
        email=email,
    )
