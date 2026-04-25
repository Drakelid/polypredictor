"""Scheduled catalyst ingestion for M4.3 event-time signals.

This worker ingests curated calendar items into ``external_events``. The first
iteration is intentionally file-backed so the event-time layer can be exercised
without pretending every source already has a production-safe public API.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..settings import get_settings
from ..writers import EXTERNAL_EVENTS_COLS, external_event_row, utcnow
from .rss_ingest import (
    FeedEntry,
    _existing_source_ids,
    _latest_active_markets,
    _match_related_markets,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ScheduledEventSpec:
    event_kind: str
    source: str
    source_id: str
    title: str
    body: str
    url: str | None
    event_time: datetime
    related_markets: list[str]
    match_terms: list[str]
    release_time: datetime | None = None
    consensus_value: float | None = None
    actual_value: float | None = None
    value_unit: str | None = None
    author: str | None = None


def _default_events_path(settings_path: str | None) -> Path:
    if settings_path:
        configured = Path(settings_path)
        if configured.is_file():
            return configured
    return Path("services/ingest/data/scheduled_events.example.json")


def _parse_event_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def load_scheduled_event_specs(path: str | None) -> list[ScheduledEventSpec]:
    file_path = _default_events_path(path)
    if not file_path.is_file():
        return []
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    specs: list[ScheduledEventSpec] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        source_id = str(item.get("source_id") or "").strip()
        title = str(item.get("title") or "").strip()
        event_time = _parse_event_time(item.get("event_time"))
        if not source or not source_id or not title or event_time is None:
            continue
        specs.append(
            ScheduledEventSpec(
                event_kind=str(item.get("event_kind") or "macro").strip() or "macro",
                source=source,
                source_id=source_id,
                title=title,
                body=str(item.get("body") or "").strip(),
                url=str(item.get("url") or "").strip() or None,
                event_time=event_time,
                related_markets=[
                    str(value).strip()
                    for value in item.get("related_markets", [])
                    if str(value).strip()
                ],
                match_terms=[
                    str(value).strip()
                    for value in item.get("match_terms", [])
                    if str(value).strip()
                ],
                release_time=_parse_event_time(item.get("release_time")),
                consensus_value=_parse_float(item.get("consensus_value")),
                actual_value=_parse_float(item.get("actual_value")),
                value_unit=str(item.get("value_unit") or "").strip() or None,
                author=str(item.get("author") or "").strip() or None,
            )
        )
    return specs


async def build_external_event_rows(
    ch: Any,
    *,
    observed_at: datetime,
    specs: list[ScheduledEventSpec],
    max_markets: int,
    max_related_markets: int,
) -> list[tuple[object, ...]]:
    if not specs:
        return []
    markets = await _latest_active_markets(ch, asked_at=observed_at, limit=max_markets)
    existing = await _existing_source_ids(
        ch,
        sources=sorted({spec.source for spec in specs}),
        source_ids=sorted({spec.source_id for spec in specs}),
        start=observed_at - timedelta(days=365),
        asked_at=observed_at,
    )
    rows: list[tuple[object, ...]] = []
    for spec in specs:
        if (spec.source, spec.source_id) in existing:
            continue
        related_markets = list(spec.related_markets)
        if not related_markets:
            related_markets = _match_related_markets(
                FeedEntry(
                    source=spec.source,
                    source_uri=spec.url or "",
                    source_id=spec.source_id,
                    event_kind=spec.event_kind,
                    author=spec.author,
                    title=spec.title,
                    body=" ".join([spec.body, *spec.match_terms]),
                    url=spec.url,
                    event_time=spec.event_time,
                    metadata={},
                ),
                markets,
                max_related_markets=max_related_markets,
            )
        rows.append(
            external_event_row(
                event_kind=spec.event_kind,
                source=spec.source,
                source_uri=spec.url or spec.source,
                source_id=spec.source_id,
                related_markets=related_markets,
                author=spec.author,
                title=spec.title,
                body=spec.body,
                url=spec.url,
                metadata={
                    "match_terms": spec.match_terms,
                    "scheduled": True,
                    "release_time": (
                        (spec.release_time or spec.event_time).isoformat()
                        if (spec.release_time or spec.event_time) is not None
                        else None
                    ),
                    "consensus_value": spec.consensus_value,
                    "actual_value": spec.actual_value,
                    "value_unit": spec.value_unit,
                },
                event_time=spec.event_time,
                observed_at=observed_at,
            )
        )
    return rows


async def run_once() -> int:
    settings = get_settings()
    specs = load_scheduled_event_specs(settings.scheduled_events_file)
    if not specs:
        return 0
    ch = await get_async_client()
    try:
        observed_at = utcnow()
        rows = await build_external_event_rows(
            ch,
            observed_at=observed_at,
            specs=specs,
            max_markets=settings.scheduled_events_max_markets,
            max_related_markets=settings.scheduled_events_max_related_markets,
        )
        if not rows:
            log.info("scheduled_events_ingest.done", events=0)
            return 0
        await ch.insert("external_events", rows, column_names=EXTERNAL_EVENTS_COLS)
        log.info("scheduled_events_ingest.done", events=len(rows))
        return len(rows)
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    dlq = DeadLetterQueue(settings.redis_url, stream="scheduled_events_ingest")
    try:
        while True:
            try:
                await run_once()
            except Exception as exc:
                log.error("scheduled_events_ingest.failed", error=repr(exc))
                await dlq.push(
                    source="scheduled_events",
                    reason="scheduled_events_ingest_failed",
                    payload={"error": repr(exc)},
                )
            await asyncio.sleep(settings.scheduled_events_interval_s)
    finally:
        await dlq.aclose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
