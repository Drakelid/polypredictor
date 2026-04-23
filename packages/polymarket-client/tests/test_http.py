"""HttpTransport tests — cache, rate-limit, error mapping.

Uses respx to mock the httpx transport rather than hitting Polymarket live.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from polymarket_client.cache import TTLCache
from polymarket_client.errors import (
    PolymarketAuthError,
    PolymarketNotFoundError,
    PolymarketRateLimitError,
)
from polymarket_client.http import HttpTransport
from polymarket_client.rate_limiter import BucketConfig, TokenBucket


def _transport() -> HttpTransport:
    cfg = BucketConfig(published_requests_per_window=1000, window_seconds=10.0)
    bucket = TokenBucket(cfg)
    return HttpTransport(
        base_url="https://example.test",
        buckets={"default": bucket},
        default_bucket=bucket,
        cache=TTLCache(),
        default_ttl_s=60.0,
        max_retries=1,
    )


@pytest.mark.asyncio
@respx.mock
async def test_cache_hit_skips_network() -> None:
    route = respx.get("https://example.test/foo").mock(return_value=httpx.Response(200, json={"x": 1}))
    t = _transport()
    try:
        first = await t.get_json("/foo", endpoint_class="default")
        second = await t.get_json("/foo", endpoint_class="default")
    finally:
        await t.aclose()
    assert first == {"x": 1}
    assert second == {"x": 1}
    assert route.call_count == 1  # second call served from cache


@pytest.mark.asyncio
@respx.mock
async def test_different_params_do_not_share_cache() -> None:
    respx.get("https://example.test/foo", params={"a": "1"}).mock(
        return_value=httpx.Response(200, json={"n": 1})
    )
    respx.get("https://example.test/foo", params={"a": "2"}).mock(
        return_value=httpx.Response(200, json={"n": 2})
    )
    t = _transport()
    try:
        r1 = await t.get_json("/foo", endpoint_class="default", params={"a": 1})
        r2 = await t.get_json("/foo", endpoint_class="default", params={"a": 2})
    finally:
        await t.aclose()
    assert r1 == {"n": 1}
    assert r2 == {"n": 2}


@pytest.mark.asyncio
@respx.mock
async def test_404_raises_not_found() -> None:
    respx.get("https://example.test/missing").mock(return_value=httpx.Response(404, text="nope"))
    t = _transport()
    try:
        with pytest.raises(PolymarketNotFoundError):
            await t.get_json("/missing", endpoint_class="default")
    finally:
        await t.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_auth() -> None:
    respx.get("https://example.test/private").mock(return_value=httpx.Response(401))
    t = _transport()
    try:
        with pytest.raises(PolymarketAuthError):
            await t.get_json("/private", endpoint_class="default")
    finally:
        await t.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_raises_rate_limit_with_retry_after() -> None:
    respx.get("https://example.test/limited").mock(
        return_value=httpx.Response(429, headers={"retry-after": "3"})
    )
    t = _transport()
    try:
        with pytest.raises(PolymarketRateLimitError) as ei:
            await t.get_json("/limited", endpoint_class="default")
        assert ei.value.retry_after_s == 3.0
    finally:
        await t.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_5xx_retries_then_succeeds() -> None:
    route = respx.get("https://example.test/flaky").mock(
        side_effect=[
            httpx.Response(502),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    t = _transport()
    try:
        data = await t.get_json("/flaky", endpoint_class="default", bypass_cache=True)
    finally:
        await t.aclose()
    assert data == {"ok": True}
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_bypass_cache_always_hits_network() -> None:
    route = respx.get("https://example.test/live").mock(return_value=httpx.Response(200, json={"v": 1}))
    t = _transport()
    try:
        await t.get_json("/live", endpoint_class="default", bypass_cache=True)
        await t.get_json("/live", endpoint_class="default", bypass_cache=True)
    finally:
        await t.aclose()
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_conditional_cache_reuses_etagged_response_after_304() -> None:
    route = respx.get("https://example.test/revalidate").mock(
        side_effect=[
            httpx.Response(200, json={"v": 1}, headers={"etag": '"etag-1"'}),
            httpx.Response(304),
        ]
    )
    t = _transport()
    try:
        first = await t.get_json(
            "/revalidate",
            endpoint_class="default",
            ttl_s=0,
            conditional_cache=True,
        )
        second = await t.get_json(
            "/revalidate",
            endpoint_class="default",
            ttl_s=60,
            conditional_cache=True,
        )
    finally:
        await t.aclose()
    assert first == {"v": 1}
    assert second == {"v": 1}
    assert route.call_count == 2
    assert route.calls.last.request.headers["if-none-match"] == '"etag-1"'


@pytest.mark.asyncio
@respx.mock
async def test_conditional_cache_reuses_last_modified_response_after_304() -> None:
    route = respx.get("https://example.test/revalidate-lm").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"v": 2},
                headers={"last-modified": "Wed, 21 Oct 2015 07:28:00 GMT"},
            ),
            httpx.Response(304),
        ]
    )
    t = _transport()
    try:
        await t.get_json(
            "/revalidate-lm",
            endpoint_class="default",
            ttl_s=0,
            conditional_cache=True,
        )
        second = await t.get_json(
            "/revalidate-lm",
            endpoint_class="default",
            ttl_s=60,
            conditional_cache=True,
        )
    finally:
        await t.aclose()
    assert second == {"v": 2}
    assert route.call_count == 2
    assert route.calls.last.request.headers["if-modified-since"] == "Wed, 21 Oct 2015 07:28:00 GMT"
