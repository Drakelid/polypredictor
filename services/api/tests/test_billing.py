from __future__ import annotations

import json

import pytest
from api import billing
from api.settings import Settings


@pytest.mark.asyncio
async def test_webhook_requires_configured_signature_secret() -> None:
    with pytest.raises(ValueError, match="STRIPE_WEBHOOK_SECRET"):
        await billing.handle_stripe_webhook(
            None,  # type: ignore[arg-type]
            settings=Settings(),
            payload_bytes=b"{}",
            sig_header="",
        )


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature() -> None:
    payload = json.dumps({"type": "invoice.payment_succeeded"}).encode()

    with pytest.raises(ValueError, match="invalid Stripe webhook signature"):
        await billing.handle_stripe_webhook(
            None,  # type: ignore[arg-type]
            settings=Settings(stripe_webhook_secret="whsec_test"),
            payload_bytes=payload,
            sig_header="t=123,v1=bad",
        )
