"""Gamma API sub-client — market/event catalog and tag graph.

Base URL: ``https://gamma-api.polymarket.com``
Rate limits (PRD §5.2.1): 300 req/10s on ``/markets``, 500 req/10s on ``/events``.
"""

from __future__ import annotations

from typing import Any

from .cache import TTLCache
from .http import HealthCallback, HttpTransport, parse_maybe_json_list
from .models import Event, Market
from .rate_limiter import GAMMA_DEFAULT, GAMMA_EVENTS, GAMMA_MARKETS, TokenBucket

# Per-endpoint cache TTLs (PRD §7).
TTL_MARKETS = 60.0
TTL_EVENTS = 60.0
TTL_TAGS = 3600.0
TTL_SEARCH = 30.0


class GammaClient:
    def __init__(
        self,
        *,
        base_url: str = "https://gamma-api.polymarket.com",
        cache: TTLCache[Any] | None = None,
        health_cb: HealthCallback | None = None,
    ) -> None:
        buckets = {
            "markets": TokenBucket(GAMMA_MARKETS, name="gamma:/markets"),
            "events": TokenBucket(GAMMA_EVENTS, name="gamma:/events"),
        }
        default = TokenBucket(GAMMA_DEFAULT, name="gamma:default")
        self.transport = HttpTransport(
            base_url=base_url,
            buckets=buckets,
            default_bucket=default,
            cache=cache or TTLCache[Any](),
            default_ttl_s=60.0,
            health_cb=health_cb,
        )

    async def aclose(self) -> None:
        await self.transport.aclose()

    async def __aenter__(self) -> GammaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def list_markets(
        self,
        *,
        tag_slug: str | None = None,
        active: bool | None = True,
        closed: bool | None = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Market]:
        """``GET /markets``. v1 crypto/finance scope filters via ``tag_slug``."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if tag_slug is not None:
            params["tag_slug"] = tag_slug
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()
        raw = await self.transport.get_json(
            "/markets",
            endpoint_class="markets",
            params=params,
            ttl_s=TTL_MARKETS,
        )
        return [_to_market(m) for m in raw]

    async def list_events(
        self,
        *,
        tag_slug: str | None = None,
        active: bool | None = True,
        closed: bool | None = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if tag_slug is not None:
            params["tag_slug"] = tag_slug
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()
        raw = await self.transport.get_json(
            "/events",
            endpoint_class="events",
            params=params,
            ttl_s=TTL_EVENTS,
        )
        events: list[Event] = []
        for ev in raw:
            markets = [_to_market(m) for m in ev.get("markets", [])]
            events.append(
                Event.model_validate({**ev, "markets": [m.model_dump() for m in markets]})
            )
        return events

    async def related_tags(self, tag_id: str) -> list[dict[str, Any]]:
        raw = await self.transport.get_json(
            f"/tags/{tag_id}/related-tags/tags",
            endpoint_class="tags",
            ttl_s=TTL_TAGS,
        )
        return list(raw) if isinstance(raw, list) else []

    async def search(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        raw = await self.transport.get_json(
            "/public-search",
            endpoint_class="search",
            params={"q": query, "limit": limit},
            ttl_s=TTL_SEARCH,
        )
        return dict(raw) if isinstance(raw, dict) else {}


def _to_market(payload: dict[str, Any]) -> Market:
    """Normalize Gamma's ``/markets`` payload: ``clobTokenIds`` arrives as a
    JSON-encoded string in some responses; decode it here so downstream code
    can rely on ``Market.token_ids`` being a plain list of ids.
    """
    token_ids = parse_maybe_json_list(payload.get("clobTokenIds"))
    # Some responses embed tokens differently.
    if not token_ids:
        tokens = payload.get("tokens") or []
        token_ids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
    normalized = {**payload, "token_ids": token_ids}
    return Market.model_validate(normalized)
