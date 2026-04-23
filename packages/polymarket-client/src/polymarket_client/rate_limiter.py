"""Async token-bucket rate limiter.

PRD §7: token bucket sized to 70% of Polymarket's published ceilings. Published
limits are per endpoint class (e.g. ``/book`` at 1500/10s). We model each
endpoint class as its own bucket, keyed ``(base_url, class)``, so bursts on
``/book`` can't starve ``/positions``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass


@dataclass
class BucketConfig:
    """Token-bucket configuration derived from a published rate limit.

    ``published_requests_per_window`` is Polymarket's documented ceiling;
    ``window_seconds`` is the window those requests are counted over; we
    allocate ``headroom`` of the budget to ourselves (0.70 by default).
    """

    published_requests_per_window: int
    window_seconds: float
    headroom: float = 0.70

    @property
    def capacity(self) -> float:
        return self.published_requests_per_window * self.headroom

    @property
    def refill_per_second(self) -> float:
        return self.capacity / self.window_seconds


class TokenBucket:
    """Async token bucket.

    ``acquire(n)`` blocks until ``n`` tokens are available, then consumes them.
    Tokens refill continuously at ``config.refill_per_second``.
    """

    def __init__(self, config: BucketConfig, *, name: str = "") -> None:
        self.config = config
        self.name = name
        self._tokens: float = config.capacity
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()
        # Condition lets waiters wake precisely when tokens arrive instead of
        # polling — far less CPU at low QPS, and no systematic bias under load.
        self._refill_event = asyncio.Event()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(
            self.config.capacity,
            self._tokens + elapsed * self.config.refill_per_second,
        )
        self._last_refill = now

    async def acquire(self, n: float = 1.0) -> None:
        if n > self.config.capacity:
            raise ValueError(
                f"requested {n} tokens but bucket capacity is {self.config.capacity}"
            )
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return
                # How long until we have enough tokens?
                deficit = n - self._tokens
                wait_s = deficit / self.config.refill_per_second
            # Release the lock while we sleep; re-check on wake because other
            # waiters may have consumed fresh tokens first.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._refill_event.wait(), timeout=wait_s)
            self._refill_event.clear()

    def try_acquire(self, n: float = 1.0) -> bool:
        """Non-blocking variant. Returns True if tokens were consumed."""
        # This path is sync-only; callers are expected to be on the event loop
        # but we don't await, so no lock contention issue within a single loop.
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def available(self) -> float:
        self._refill()
        return self._tokens


# -- Published Polymarket limits (PRD §5.2.1) ---------------------------------
# We keep these as named configs so every endpoint class maps to a declared
# bucket, making it easy to audit headroom against the PRD.

GAMMA_MARKETS = BucketConfig(published_requests_per_window=300, window_seconds=10.0)
GAMMA_EVENTS = BucketConfig(published_requests_per_window=500, window_seconds=10.0)
# Default Gamma catch-all (search, tags). Polymarket doesn't publish a specific
# limit for these; we pick a conservative budget.
GAMMA_DEFAULT = BucketConfig(published_requests_per_window=200, window_seconds=10.0)

CLOB_BOOK = BucketConfig(published_requests_per_window=1500, window_seconds=10.0)
CLOB_PRICE = BucketConfig(published_requests_per_window=1500, window_seconds=10.0)
CLOB_MIDPOINT = BucketConfig(published_requests_per_window=1500, window_seconds=10.0)
CLOB_PRICES_HISTORY = BucketConfig(published_requests_per_window=1000, window_seconds=10.0)
CLOB_TRADES = BucketConfig(published_requests_per_window=500, window_seconds=10.0)
CLOB_DEFAULT = BucketConfig(published_requests_per_window=500, window_seconds=10.0)

DATA_DEFAULT = BucketConfig(published_requests_per_window=300, window_seconds=10.0)
