"""Shared HTTP transport for Polymarket sub-clients.

Responsibilities:
  * Acquire rate-limit tokens from the correct bucket before sending.
  * Check + populate the TTL response cache.
  * Retry 5xx / network errors with jittered exponential backoff.
  * Raise typed errors (``PolymarketAuthError``, ``PolymarketNotFoundError``,
    ``PolymarketRateLimitError``).
  * Emit per-request health metrics via a user-supplied callback (for
    writing into ClickHouse ``ingest_health`` without coupling this package
    to the storage layer).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .cache import TTLCache, make_cache_key
from .errors import (
    PolymarketAPIError,
    PolymarketAuthError,
    PolymarketNotFoundError,
    PolymarketRateLimitError,
)
from .rate_limiter import TokenBucket

# --- Types ------------------------------------------------------------------

HealthCallback = Callable[["RequestHealth"], Awaitable[None] | None]


@dataclass(frozen=True)
class RequestHealth:
    base_url: str
    endpoint: str
    http_status: int
    latency_ms: int
    cache_hit: bool
    error: str | None = None


# --- Transport --------------------------------------------------------------


class HttpTransport:
    """Owns an ``httpx.AsyncClient`` plus a bucket dictionary and a cache."""

    def __init__(
        self,
        *,
        base_url: str,
        buckets: dict[str, TokenBucket],
        default_bucket: TokenBucket,
        cache: TTLCache[Any],
        default_ttl_s: float = 5.0,
        timeout_s: float = 10.0,
        max_retries: int = 3,
        health_cb: HealthCallback | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.buckets = buckets
        self.default_bucket = default_bucket
        self.cache = cache
        self.default_ttl_s = default_ttl_s
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.health_cb = health_cb
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HttpTransport:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _pick_bucket(self, endpoint_class: str) -> TokenBucket:
        return self.buckets.get(endpoint_class, self.default_bucket)

    async def _emit_health(self, h: RequestHealth) -> None:
        if self.health_cb is None:
            return
        result = self.health_cb(h)
        if asyncio.iscoroutine(result):
            await result

    async def get_json(
        self,
        path: str,
        *,
        endpoint_class: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        ttl_s: float | None = None,
        bypass_cache: bool = False,
    ) -> Any:
        """GET with rate limiting, caching and typed errors.

        ``endpoint_class`` selects the token bucket (see PRD §5.2.1 ceilings).
        ``ttl_s`` overrides the default cache TTL per-endpoint.
        """
        cache_key = make_cache_key(self.base_url, path, params)
        if not bypass_cache:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                await self._emit_health(
                    RequestHealth(
                        base_url=self.base_url,
                        endpoint=path,
                        http_status=200,
                        latency_ms=0,
                        cache_hit=True,
                    )
                )
                return cached

        bucket = self._pick_bucket(endpoint_class)
        await bucket.acquire(1.0)

        effective_ttl = ttl_s if ttl_s is not None else self.default_ttl_s
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            try:
                resp = await self._client.get(path, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                await self._emit_health(
                    RequestHealth(
                        base_url=self.base_url,
                        endpoint=path,
                        http_status=0,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        cache_hit=False,
                        error=repr(exc),
                    )
                )
                if attempt == self.max_retries:
                    raise PolymarketAPIError(f"network error after retries: {exc!r}") from exc
                await self._sleep_backoff(attempt)
                continue

            latency_ms = int((time.perf_counter() - started) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                if not bypass_cache and effective_ttl > 0:
                    await self.cache.set(cache_key, data, ttl_s=effective_ttl)
                await self._emit_health(
                    RequestHealth(
                        base_url=self.base_url,
                        endpoint=path,
                        http_status=200,
                        latency_ms=latency_ms,
                        cache_hit=False,
                    )
                )
                return data

            await self._emit_health(
                RequestHealth(
                    base_url=self.base_url,
                    endpoint=path,
                    http_status=resp.status_code,
                    latency_ms=latency_ms,
                    cache_hit=False,
                    error=_safe_body_excerpt(resp),
                )
            )
            self._raise_for_status(resp)
            # _raise_for_status covers 4xx; 5xx flows to retry.
            if attempt == self.max_retries:
                raise PolymarketAPIError(
                    f"{resp.status_code} after retries",
                    status_code=resp.status_code,
                    body=_safe_body_excerpt(resp),
                )
            await self._sleep_backoff(attempt)
        # Unreachable; loop always returns or raises.
        raise PolymarketAPIError("exhausted retries") from last_exc

    async def _sleep_backoff(self, attempt: int) -> None:
        # 0.25s, 0.75s, 2.25s with ±20% jitter.
        base = 0.25 * (3**attempt)
        jitter = base * (0.8 + 0.4 * random.random())
        await asyncio.sleep(jitter)

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        code = resp.status_code
        if code == 401 or code == 403:
            raise PolymarketAuthError(
                f"auth error {code}",
                status_code=code,
                body=_safe_body_excerpt(resp),
            )
        if code == 404:
            raise PolymarketNotFoundError(
                "not found",
                status_code=code,
                body=_safe_body_excerpt(resp),
            )
        if code == 429:
            retry_after = resp.headers.get("retry-after")
            retry_s: float | None
            try:
                retry_s = float(retry_after) if retry_after else None
            except ValueError:
                retry_s = None
            raise PolymarketRateLimitError(
                "server-side rate limited (429)",
                status_code=code,
                body=_safe_body_excerpt(resp),
                retry_after_s=retry_s,
            )
        if 400 <= code < 500:
            raise PolymarketAPIError(
                f"client error {code}",
                status_code=code,
                body=_safe_body_excerpt(resp),
            )
        # 5xx falls through to caller's retry loop.


def _safe_body_excerpt(resp: httpx.Response, limit: int = 500) -> str:
    try:
        text = resp.text
    except Exception:
        return "<unreadable>"
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


# --- Polymarket sometimes returns JSON-as-string fields ---------------------


def parse_maybe_json_list(value: Any) -> list[Any]:
    """Some Gamma fields (``clobTokenIds``) arrive as a JSON string literal.
    Accept either a native list or a JSON-encoded string of one.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []
