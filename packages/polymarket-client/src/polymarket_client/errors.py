"""Polymarket client errors."""

from __future__ import annotations


class PolymarketAPIError(Exception):
    """Base for all Polymarket client errors."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class PolymarketAuthError(PolymarketAPIError):
    """Auth failed (401 / 403)."""


class PolymarketNotFoundError(PolymarketAPIError):
    """Resource not found (404)."""


class PolymarketRateLimitError(PolymarketAPIError):
    """Server-side rate limit hit (429) — usually indicates our client-side
    limiter is mis-sized or a concurrent process is also hitting the API."""

    def __init__(self, message: str, *, retry_after_s: float | None = None, **kwargs: object):
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.retry_after_s = retry_after_s


class PolymarketWSSError(PolymarketAPIError):
    """WebSocket connection / protocol error."""
