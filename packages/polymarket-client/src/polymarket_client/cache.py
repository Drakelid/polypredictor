"""Tiny in-memory TTL cache for Polymarket responses.

PRD §7: per-endpoint TTLs — ``/markets`` 60s, ``/midpoint`` 2s, ``/book`` 1s,
``/holders`` 300s, ``/v1/leaderboard`` 1h.

This is intentionally a process-local cache. If we later need to share cache
across workers, swap the store to Redis behind the same interface.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CacheEntry(Generic[T]):
    value: T
    expires_at: float
    etag: str | None = None
    last_modified: str | None = None


class TTLCache(Generic[T]):
    """Async-safe TTL cache with explicit per-key TTL."""

    def __init__(self, *, max_entries: int = 10_000) -> None:
        self._store: dict[str, CacheEntry[T]] = {}
        self._max_entries = max_entries
        self._lock = asyncio.Lock()

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    async def get(self, key: str) -> T | None:
        entry = await self.get_entry(key)
        return entry.value if entry is not None else None

    async def get_entry(self, key: str, *, allow_stale: bool = False) -> CacheEntry[T] | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if allow_stale:
                return entry
            if self.is_expired(entry):
                del self._store[key]
                return None
            return entry

    def is_expired(self, entry: CacheEntry[T]) -> bool:
        return entry.expires_at <= self._now()

    async def set(
        self,
        key: str,
        value: T,
        *,
        ttl_s: float,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        async with self._lock:
            if len(self._store) >= self._max_entries:
                # Drop expired entries first; if that doesn't make room, drop the
                # soonest-to-expire entry. Good-enough bounded-size policy for
                # the read-heavy patterns our callers use.
                self._evict_locked()
            self._store[key] = CacheEntry(
                value=value,
                expires_at=self._now() + ttl_s,
                etag=etag,
                last_modified=last_modified,
            )

    async def refresh(self, key: str, *, ttl_s: float) -> bool:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            self._store[key] = CacheEntry(
                value=entry.value,
                expires_at=self._now() + ttl_s,
                etag=entry.etag,
                last_modified=entry.last_modified,
            )
            return True

    def _evict_locked(self) -> None:
        now = self._now()
        expired = [k for k, e in self._store.items() if e.expires_at <= now]
        for k in expired:
            del self._store[k]
        if len(self._store) < self._max_entries:
            return
        # Drop the 10% nearest to expiry to amortize eviction cost.
        ordered = sorted(self._store.items(), key=lambda kv: kv[1].expires_at)
        drop_n = max(1, len(ordered) // 10)
        for k, _ in ordered[:drop_n]:
            del self._store[k]

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    def size(self) -> int:
        return len(self._store)


def make_cache_key(base_url: str, path: str, params: dict[str, Any] | None) -> str:
    """Deterministic cache key: ``<base_url>|<path>|<k=v&...sorted>``.

    Arrays/list params are sorted tuple-ized so ordering doesn't split the
    cache. ``None`` params are skipped entirely.
    """
    if not params:
        return f"{base_url}|{path}|"
    normalized = []
    for k in sorted(params):
        v = params[k]
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        normalized.append(f"{k}={v}")
    return f"{base_url}|{path}|{'&'.join(normalized)}"
