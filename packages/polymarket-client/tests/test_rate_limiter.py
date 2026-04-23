"""Token-bucket correctness tests."""

from __future__ import annotations

import asyncio

import pytest
from polymarket_client.rate_limiter import BucketConfig, TokenBucket


@pytest.mark.asyncio
async def test_capacity_honors_headroom() -> None:
    cfg = BucketConfig(published_requests_per_window=1000, window_seconds=10.0, headroom=0.7)
    bucket = TokenBucket(cfg)
    # 1000 * 0.7 = 700
    assert bucket.config.capacity == pytest.approx(700.0)
    assert bucket.config.refill_per_second == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_acquire_consumes_tokens_immediately_when_available() -> None:
    cfg = BucketConfig(published_requests_per_window=100, window_seconds=10.0, headroom=1.0)
    bucket = TokenBucket(cfg)
    start = asyncio.get_event_loop().time()
    for _ in range(50):
        await bucket.acquire(1)
    elapsed = asyncio.get_event_loop().time() - start
    # 50 of 100 tokens should be instant.
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_acquire_blocks_until_refill() -> None:
    # 10 req / 1s → 10 tokens/s. Drain to zero, next acquire should block ~100ms.
    cfg = BucketConfig(published_requests_per_window=10, window_seconds=1.0, headroom=1.0)
    bucket = TokenBucket(cfg)
    for _ in range(10):
        await bucket.acquire(1)
    start = asyncio.get_event_loop().time()
    await bucket.acquire(1)
    elapsed = asyncio.get_event_loop().time() - start
    # Refill rate is 10/s → 1 token every 100ms; allow generous tolerance.
    assert 0.05 <= elapsed <= 0.4


@pytest.mark.asyncio
async def test_try_acquire_non_blocking() -> None:
    cfg = BucketConfig(published_requests_per_window=2, window_seconds=10.0, headroom=1.0)
    bucket = TokenBucket(cfg)
    assert bucket.try_acquire(1) is True
    assert bucket.try_acquire(1) is True
    assert bucket.try_acquire(1) is False


@pytest.mark.asyncio
async def test_acquire_raises_when_over_capacity() -> None:
    cfg = BucketConfig(published_requests_per_window=10, window_seconds=10.0, headroom=1.0)
    bucket = TokenBucket(cfg)
    with pytest.raises(ValueError):
        await bucket.acquire(11)
