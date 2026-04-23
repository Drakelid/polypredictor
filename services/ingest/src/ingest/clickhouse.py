"""ClickHouse writer for the ingestion services.

Batch-inserts are the norm: every worker buffers rows in-memory and flushes
either on count or on interval. All inserts carry ``observed_at`` — the PIT
key (PRD §6.6).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import clickhouse_connect
import structlog
from clickhouse_connect.driver.asyncclient import AsyncClient

from .settings import get_settings

log = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _sync_client() -> Any:
    s = get_settings()
    return clickhouse_connect.get_client(
        host=s.clickhouse_host,
        port=s.clickhouse_port,
        username=s.clickhouse_user,
        password=s.clickhouse_password,
        database=s.clickhouse_db,
    )


async def get_async_client() -> AsyncClient:
    s = get_settings()
    return await clickhouse_connect.get_async_client(
        host=s.clickhouse_host,
        port=s.clickhouse_port,
        username=s.clickhouse_user,
        password=s.clickhouse_password,
        database=s.clickhouse_db,
    )


class BatchInserter:
    """Buffers rows and flushes to ClickHouse on count-or-time threshold.

    Each inserter targets one table. Inserts are idempotent in principle —
    downstream tables use ``ReplacingMergeTree(observed_at)`` — but in practice
    we avoid retries after a partial success. This class is async-safe for a
    single producer; multi-producer fan-in should use separate inserters.
    """

    def __init__(
        self,
        ch: AsyncClient,
        *,
        table: str,
        columns: Sequence[str],
        max_batch: int = 500,
        max_interval_s: float = 5.0,
    ) -> None:
        self.ch = ch
        self.table = table
        self.columns = list(columns)
        self.max_batch = max_batch
        self.max_interval_s = max_interval_s
        self._buffer: list[tuple[Any, ...]] = []
        self._lock = asyncio.Lock()
        self._last_flush = time.monotonic()

    async def add(self, row: tuple[Any, ...]) -> None:
        async with self._lock:
            self._buffer.append(row)
            full = len(self._buffer) >= self.max_batch
            stale = time.monotonic() - self._last_flush >= self.max_interval_s
        if full or stale:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            rows = self._buffer
            self._buffer = []
            self._last_flush = time.monotonic()
        if not rows:
            return
        try:
            await self.ch.insert(self.table, rows, column_names=self.columns)
        except Exception as exc:
            # The DLQ will capture truly broken payloads at the caller level;
            # here we log and move on to avoid wedging the worker loop.
            log.error(
                "clickhouse.insert_failed",
                table=self.table,
                rows=len(rows),
                error=repr(exc),
            )
