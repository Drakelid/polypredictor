"""Redis-backed dead-letter queue.

Failed ingestion attempts get pushed to ``dlq:{stream}`` as JSON blobs.
Replay tool is a separate CLI (``ingest.cli.dlq_replay``).
"""

from __future__ import annotations

import json
import time
from typing import Any

import redis.asyncio as redis
import structlog

log = structlog.get_logger(__name__)


class DeadLetterQueue:
    def __init__(self, redis_url: str, *, stream: str = "ingest") -> None:
        self._redis = redis.from_url(redis_url)
        self._key = f"dlq:{stream}"

    async def aclose(self) -> None:
        await self._redis.aclose()  # type: ignore[attr-defined]

    async def push(
        self,
        *,
        source: str,
        reason: str,
        payload: dict[str, Any],
    ) -> None:
        entry = {
            "source": source,
            "reason": reason,
            "payload": payload,
            "ts": time.time(),
        }
        try:
            await self._redis.lpush(self._key, json.dumps(entry, default=str))
        except Exception as exc:
            # DLQ is best-effort — we log but don't raise, to avoid masking
            # the original failure that triggered the push.
            log.error("dlq.push_failed", error=repr(exc), source=source, reason=reason)

    async def depth(self) -> int:
        try:
            return int(await self._redis.llen(self._key))
        except Exception as exc:
            log.error("dlq.depth_failed", error=repr(exc))
            return -1

    async def pop(self) -> dict[str, Any] | None:
        raw = await self._redis.rpop(self._key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
