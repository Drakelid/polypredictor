"""Top-level :class:`PolymarketClient` — groups the three REST sub-clients
together, sharing a single process-local cache so repeated reads across
sub-clients hit the same store.

WSS management lives in :mod:`polymarket_client.wss` and is instantiated
separately because its lifecycle is long-lived and event-driven.
"""

from __future__ import annotations

from typing import Any

from .cache import TTLCache
from .clob import ClobClient
from .data import DataClient
from .gamma import GammaClient
from .http import HealthCallback


class PolymarketClient:
    """Bundle of Gamma + CLOB + Data sub-clients.

    Use as an async context manager::

        async with PolymarketClient() as pm:
            markets = await pm.gamma.list_markets(tag_slug="crypto")
    """

    def __init__(
        self,
        *,
        gamma_base_url: str = "https://gamma-api.polymarket.com",
        clob_base_url: str = "https://clob.polymarket.com",
        data_base_url: str = "https://data-api.polymarket.com",
        cache: TTLCache[Any] | None = None,
        health_cb: HealthCallback | None = None,
    ) -> None:
        shared_cache: TTLCache[Any] = cache or TTLCache[Any](max_entries=50_000)
        self.gamma = GammaClient(base_url=gamma_base_url, cache=shared_cache, health_cb=health_cb)
        self.clob = ClobClient(base_url=clob_base_url, cache=shared_cache, health_cb=health_cb)
        self.data = DataClient(base_url=data_base_url, cache=shared_cache, health_cb=health_cb)
        self._cache = shared_cache

    async def aclose(self) -> None:
        await self.gamma.aclose()
        await self.clob.aclose()
        await self.data.aclose()

    async def __aenter__(self) -> PolymarketClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
