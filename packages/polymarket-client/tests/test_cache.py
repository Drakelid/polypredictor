"""TTLCache tests."""

from __future__ import annotations

import asyncio

import pytest
from polymarket_client.cache import TTLCache, make_cache_key


@pytest.mark.asyncio
async def test_set_get_roundtrip() -> None:
    cache: TTLCache[int] = TTLCache()
    await cache.set("k", 42, ttl_s=10.0)
    assert await cache.get("k") == 42


@pytest.mark.asyncio
async def test_expired_entry_returns_none() -> None:
    cache: TTLCache[str] = TTLCache()
    await cache.set("k", "v", ttl_s=0.05)
    await asyncio.sleep(0.08)
    assert await cache.get("k") is None


@pytest.mark.asyncio
async def test_missing_key_returns_none() -> None:
    cache: TTLCache[str] = TTLCache()
    assert await cache.get("missing") is None


@pytest.mark.asyncio
async def test_clear() -> None:
    cache: TTLCache[int] = TTLCache()
    await cache.set("a", 1, ttl_s=60.0)
    await cache.set("b", 2, ttl_s=60.0)
    await cache.clear()
    assert await cache.get("a") is None
    assert await cache.get("b") is None


def test_make_cache_key_stable_under_param_reorder() -> None:
    k1 = make_cache_key("https://x", "/p", {"b": 2, "a": 1})
    k2 = make_cache_key("https://x", "/p", {"a": 1, "b": 2})
    assert k1 == k2


def test_make_cache_key_list_params() -> None:
    k1 = make_cache_key("https://x", "/p", {"ids": ["a", "b"]})
    k2 = make_cache_key("https://x", "/p", {"ids": ["a", "b"]})
    assert k1 == k2
    assert "ids=a,b" in k1


def test_make_cache_key_skips_none() -> None:
    k = make_cache_key("https://x", "/p", {"a": 1, "b": None})
    assert "b=" not in k
    assert "a=1" in k
