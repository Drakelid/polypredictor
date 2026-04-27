"""Billing / subscription stub (v1 launch, PRD §11.1).

Provides a Stripe Checkout session creation endpoint and a lightweight
subscription-status tracker.  When ``STRIPE_SECRET_KEY`` is absent the
module operates in mock mode so staging environments don't require live Stripe
credentials.

Schema (Postgres)
-----------------
::

    CREATE TABLE IF NOT EXISTS billing_subscriptions (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email           TEXT NOT NULL UNIQUE,
        stripe_customer_id  TEXT,
        stripe_subscription_id TEXT,
        status          TEXT NOT NULL DEFAULT 'inactive',
        plan            TEXT NOT NULL DEFAULT 'monthly_49',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );

Endpoints wired in ``main.py``
--------------------------------
* ``POST /v1/billing/checkout``  — create a Stripe Checkout session (or mock).
* ``GET  /v1/billing/status``    — return the caller's subscription status.
* ``POST /v1/billing/webhook``   — Stripe webhook handler (signature check).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

log = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS billing_subscriptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                   TEXT NOT NULL UNIQUE,
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,
    status                  TEXT NOT NULL DEFAULT 'inactive',
    plan                    TEXT NOT NULL DEFAULT 'monthly_49',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# ---------------------------------------------------------------------------
# Settings helpers (read directly from env to avoid circular imports)
# ---------------------------------------------------------------------------


def _stripe_key() -> str | None:
    return os.environ.get("STRIPE_SECRET_KEY")


def _stripe_price_id() -> str | None:
    return os.environ.get("STRIPE_PRICE_ID")


def _stripe_webhook_secret() -> str | None:
    return os.environ.get("STRIPE_WEBHOOK_SECRET")


def _monthly_price_usd() -> float:
    try:
        return float(os.environ.get("BILLING_MONTHLY_PRICE_USD", "49.0"))
    except ValueError:
        return 49.0


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BillingSubscription:
    id: UUID
    email: str
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    status: str
    plan: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CheckoutSession:
    session_id: str
    url: str
    is_mock: bool


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _ensure_table(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)


def _row_to_sub(row: asyncpg.Record) -> BillingSubscription:
    return BillingSubscription(
        id=row["id"],
        email=row["email"],
        stripe_customer_id=row["stripe_customer_id"],
        stripe_subscription_id=row["stripe_subscription_id"],
        status=row["status"],
        plan=row["plan"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


async def create_checkout_session(
    pool: asyncpg.Pool,
    *,
    email: str,
    success_url: str,
    cancel_url: str,
) -> CheckoutSession:
    """Create a Stripe Checkout session for the $49/month plan.

    Falls back to a mock session when ``STRIPE_SECRET_KEY`` is absent so
    development and staging environments work without live credentials.
    """
    await _ensure_table(pool)
    stripe_key = _stripe_key()
    price_id = _stripe_price_id()

    if stripe_key and price_id:
        return await _stripe_checkout(
            email=email,
            stripe_key=stripe_key,
            price_id=price_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    else:
        log.warning("billing.mock_checkout email=%s", email)
        mock_id = "mock_cs_" + hashlib.sha256(email.encode()).hexdigest()[:16]
        return CheckoutSession(
            session_id=mock_id,
            url=f"{success_url}?session_id={mock_id}&mock=1",
            is_mock=True,
        )


async def _stripe_checkout(
    *,
    email: str,
    stripe_key: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
) -> CheckoutSession:
    """Call the Stripe Checkout Sessions API."""
    import httpx

    payload = {
        "mode": "subscription",
        "customer_email": email,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    async with httpx.AsyncClient(
        auth=(stripe_key, ""),
        timeout=15.0,
    ) as client:
        resp = await client.post(
            "https://api.stripe.com/v1/checkout/sessions",
            data=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    return CheckoutSession(
        session_id=str(data["id"]),
        url=str(data["url"]),
        is_mock=False,
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def get_subscription_status(
    pool: asyncpg.Pool, *, email: str
) -> BillingSubscription | None:
    await _ensure_table(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM billing_subscriptions WHERE email = $1",
            email.strip().lower(),
        )
    return _row_to_sub(row) if row else None


# ---------------------------------------------------------------------------
# Webhook handler
# ---------------------------------------------------------------------------


def _verify_stripe_signature(
    payload_bytes: bytes, sig_header: str, secret: str
) -> bool:
    """Verify the ``Stripe-Signature`` header."""
    try:
        parts = {k: v for k, v in (p.split("=", 1) for p in sig_header.split(","))}
        timestamp = parts.get("t", "")
        sig = parts.get("v1", "")
        signed = f"{timestamp}.".encode() + payload_bytes
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


async def handle_stripe_webhook(
    pool: asyncpg.Pool,
    *,
    payload_bytes: bytes,
    sig_header: str,
) -> str:
    """Process a Stripe webhook event and update subscription state."""
    secret = _stripe_webhook_secret()
    if secret and not _verify_stripe_signature(payload_bytes, sig_header, secret):
        raise ValueError("invalid Stripe webhook signature")

    try:
        event = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON body") from exc

    event_type = str(event.get("type") or "")
    data_obj = (event.get("data") or {}).get("object") or {}

    email: str | None = None
    stripe_customer_id: str | None = str(data_obj.get("customer") or "") or None
    stripe_subscription_id: str | None = str(data_obj.get("id") or "") or None
    status_map = {
        "customer.subscription.updated": str(data_obj.get("status") or "active"),
        "customer.subscription.deleted": "canceled",
        "invoice.payment_succeeded": "active",
        "invoice.payment_failed": "past_due",
    }
    new_status = status_map.get(event_type)
    if new_status is None:
        return "ignored"

    # Attempt to resolve email from customer metadata.
    if data_obj.get("customer_email"):
        email = str(data_obj["customer_email"]).strip().lower()
    elif data_obj.get("customer_details", {}).get("email"):
        email = str(data_obj["customer_details"]["email"]).strip().lower()

    if not email:
        log.warning("billing.webhook_no_email event_type=%s", event_type)
        return "no_email"

    await _ensure_table(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO billing_subscriptions
                (email, stripe_customer_id, stripe_subscription_id, status,
                 plan, updated_at)
            VALUES ($1, $2, $3, $4, 'monthly_49', now())
            ON CONFLICT (email) DO UPDATE
                SET stripe_customer_id      = EXCLUDED.stripe_customer_id,
                    stripe_subscription_id  = EXCLUDED.stripe_subscription_id,
                    status                  = EXCLUDED.status,
                    updated_at              = now()
            """,
            email,
            stripe_customer_id,
            stripe_subscription_id,
            new_status,
        )
    log.info("billing.subscription_updated email=%s status=%s", email, new_status)
    return "processed"
