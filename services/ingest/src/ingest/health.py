"""Source-health callback wired into :mod:`polymarket_client.http`.

Every request result (success or failure) becomes a row in ``ingest_health``.
PRD §8 guardrail: source-failure rate < 1% per source per day; this table
powers that alert.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient
from polymarket_client.http import RequestHealth


class HealthSink:
    """Writes ``RequestHealth`` rows into ClickHouse ``ingest_health``.

    Writes are best-effort and non-blocking to the HTTP path — if the sink
    errors, we swallow it so one broken observability dependency can't break
    the whole ingestion loop.
    """

    TABLE = "ingest_health"
    COLS = (
        "source",
        "endpoint",
        "status",
        "latency_ms",
        "http_status",
        "error_message",
        "event_time",
        "observed_at",
    )

    def __init__(self, ch: AsyncClient) -> None:
        self.ch = ch

    @staticmethod
    def _status_enum(h: RequestHealth) -> str:
        if h.error is None and 200 <= h.http_status < 300:
            return "ok"
        if h.http_status == 429:
            return "rate_limited"
        if h.http_status == 0:
            return "timeout"
        return "error"

    async def __call__(self, h: RequestHealth) -> None:
        now = datetime.now(tz=UTC)
        row: tuple[Any, ...] = (
            _source_tag(h.base_url),
            h.endpoint,
            self._status_enum(h),
            int(h.latency_ms),
            int(h.http_status),
            (h.error or "")[:500],
            now,
            now,
        )
        try:
            await self.ch.insert(self.TABLE, [row], column_names=self.COLS)
        except Exception:
            # Intentionally silent — see docstring.
            return


def _source_tag(base_url: str) -> str:
    if "gamma" in base_url:
        return "polymarket.gamma"
    if "data-api" in base_url:
        return "polymarket.data"
    if "clob" in base_url:
        return "polymarket.clob"
    return base_url
