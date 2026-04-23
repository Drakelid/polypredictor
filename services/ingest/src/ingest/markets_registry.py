"""In-process registry of the active market set.

Workers that need "what are we currently tracking?" read from here, rather than
hitting ClickHouse on every tick. The Gamma discovery worker is the single
writer; other workers are read-only consumers.

The registry is intentionally simple — a dict plus asyncio.Lock. When we
outgrow a single process, move to Redis with the same interface.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class MarketRef:
    condition_id: str
    token_ids: list[str]
    volume_usdc: float
    liquidity_usdc: float
    active: bool
    closed: bool


class MarketsRegistry:
    def __init__(self) -> None:
        self._markets: dict[str, MarketRef] = {}
        self._lock = asyncio.Lock()

    async def replace(self, refs: list[MarketRef]) -> None:
        async with self._lock:
            self._markets = {r.condition_id: r for r in refs if r.active and not r.closed}

    async def all(self) -> list[MarketRef]:
        async with self._lock:
            return list(self._markets.values())

    async def top_n_by_volume(self, n: int) -> list[MarketRef]:
        async with self._lock:
            return sorted(
                self._markets.values(),
                key=lambda m: m.volume_usdc,
                reverse=True,
            )[:n]

    async def tail_by_volume(self, skip: int) -> list[MarketRef]:
        async with self._lock:
            return sorted(
                self._markets.values(),
                key=lambda m: m.volume_usdc,
                reverse=True,
            )[skip:]

    def size(self) -> int:
        return len(self._markets)
