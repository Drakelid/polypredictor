"""CLOB API sub-client — orderbook, midpoint, price, prices-history, trades.

Base URL: ``https://clob.polymarket.com``
Rate limits (PRD §5.2.1): 1500 req/10s on ``/book``, ``/price``, ``/midpoint``;
1000 req/10s on ``/prices-history``.
"""

from __future__ import annotations

from typing import Any

from .cache import TTLCache
from .http import HealthCallback, HttpTransport
from .models import Book, BookLevel, Midpoint, PriceHistoryBucket, PricePoint, Trade
from .rate_limiter import (
    CLOB_BOOK,
    CLOB_DEFAULT,
    CLOB_MIDPOINT,
    CLOB_PRICE,
    CLOB_PRICES_HISTORY,
    CLOB_TRADES,
    TokenBucket,
)

# Per-endpoint cache TTLs (PRD §7).
TTL_BOOK = 1.0
TTL_MIDPOINT = 2.0
TTL_PRICE = 2.0
TTL_PRICES_HISTORY = 30.0
TTL_TRADES = 5.0


class ClobClient:
    def __init__(
        self,
        *,
        base_url: str = "https://clob.polymarket.com",
        cache: TTLCache[Any] | None = None,
        health_cb: HealthCallback | None = None,
    ) -> None:
        buckets = {
            "book": TokenBucket(CLOB_BOOK, name="clob:/book"),
            "price": TokenBucket(CLOB_PRICE, name="clob:/price"),
            "midpoint": TokenBucket(CLOB_MIDPOINT, name="clob:/midpoint"),
            "prices-history": TokenBucket(
                CLOB_PRICES_HISTORY, name="clob:/prices-history"
            ),
            "trades": TokenBucket(CLOB_TRADES, name="clob:/trades"),
        }
        default = TokenBucket(CLOB_DEFAULT, name="clob:default")
        self.transport = HttpTransport(
            base_url=base_url,
            buckets=buckets,
            default_bucket=default,
            cache=cache or TTLCache[Any](),
            default_ttl_s=5.0,
            health_cb=health_cb,
        )

    async def aclose(self) -> None:
        await self.transport.aclose()

    async def __aenter__(self) -> ClobClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- Orderbook ---------------------------------------------------------

    async def book(self, token_id: str) -> Book:
        raw = await self.transport.get_json(
            "/book",
            endpoint_class="book",
            params={"token_id": token_id},
            ttl_s=TTL_BOOK,
        )
        return _to_book(token_id, raw)

    async def books(self, token_ids: list[str]) -> list[Book]:
        """``POST /books`` batches multiple token ids. The public endpoint also
        accepts comma-separated GET params on some deployments; we stick to GET
        with repeated params for portability.
        """
        if not token_ids:
            return []
        raw = await self.transport.get_json(
            "/books",
            endpoint_class="book",
            params={"token_id": token_ids},
            ttl_s=TTL_BOOK,
        )
        if isinstance(raw, list):
            return [_to_book(b.get("asset_id") or b.get("token_id") or "", b) for b in raw]
        return []

    # --- Midpoint / price -------------------------------------------------

    async def midpoint(self, token_id: str) -> Midpoint:
        raw = await self.transport.get_json(
            "/midpoint",
            endpoint_class="midpoint",
            params={"token_id": token_id},
            ttl_s=TTL_MIDPOINT,
        )
        return Midpoint(token_id=token_id, mid=float(raw.get("mid", 0.0)))

    async def midpoints(self, token_ids: list[str]) -> list[Midpoint]:
        if not token_ids:
            return []
        raw = await self.transport.get_json(
            "/midpoints",
            endpoint_class="midpoint",
            params={"token_id": token_ids},
            ttl_s=TTL_MIDPOINT,
        )
        out: list[Midpoint] = []
        if isinstance(raw, dict):
            for tok, mid in raw.items():
                out.append(Midpoint(token_id=tok, mid=float(mid)))
        return out

    async def price(self, token_id: str, side: str = "BUY") -> PricePoint:
        """Best bid (``BUY``) or best ask (``SELL``) for a token."""
        raw = await self.transport.get_json(
            "/price",
            endpoint_class="price",
            params={"token_id": token_id, "side": side},
            ttl_s=TTL_PRICE,
        )
        return PricePoint(token_id=token_id, side=side, price=float(raw.get("price", 0.0)))

    # --- History + trades -------------------------------------------------

    async def prices_history(
        self,
        token_id: str,
        *,
        interval: str = "1h",
        fidelity: int = 60,
    ) -> list[PriceHistoryBucket]:
        raw = await self.transport.get_json(
            "/prices-history",
            endpoint_class="prices-history",
            params={
                "market": token_id,  # public endpoint accepts either market or token id
                "interval": interval,
                "fidelity": fidelity,
            },
            ttl_s=TTL_PRICES_HISTORY,
        )
        history = raw.get("history", raw) if isinstance(raw, dict) else raw
        if not isinstance(history, list):
            return []
        return [PriceHistoryBucket(t=int(p["t"]), p=float(p["p"])) for p in history if "t" in p]

    async def trades(
        self,
        *,
        market: str | None = None,
        token_id: str | None = None,
        limit: int = 100,
    ) -> list[Trade]:
        params: dict[str, Any] = {"limit": limit}
        if market is not None:
            params["market"] = market
        if token_id is not None:
            params["token_id"] = token_id
        raw = await self.transport.get_json(
            "/trades",
            endpoint_class="trades",
            params=params,
            ttl_s=TTL_TRADES,
        )
        if not isinstance(raw, list):
            return []
        out: list[Trade] = []
        for t in raw:
            try:
                out.append(Trade.model_validate(t))
            except Exception:
                # Skip malformed rows rather than failing the whole batch.
                continue
        return out


def _to_book(token_id: str, payload: dict[str, Any]) -> Book:
    bids = [BookLevel(price=float(x["price"]), size=float(x["size"])) for x in payload.get("bids", [])]
    asks = [BookLevel(price=float(x["price"]), size=float(x["size"])) for x in payload.get("asks", [])]
    return Book(
        token_id=token_id,
        market=payload.get("market"),
        bids=bids,
        asks=asks,
        hash=payload.get("hash"),
        timestamp=payload.get("timestamp"),
    )
