"""As-of reads over ``external_events`` with source-specific freshness decay."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient

from .settings import Settings, get_settings


@dataclass(frozen=True)
class ExternalEvent:
    event_kind: str
    source: str
    source_uri: str
    source_id: str
    related_markets: list[str]
    author: str
    title: str
    body: str
    url: str
    metadata: dict[str, Any] = field(default_factory=dict)
    event_time: datetime = field(default_factory=lambda: datetime.fromtimestamp(0))
    observed_at: datetime = field(default_factory=lambda: datetime.fromtimestamp(0))
    age_seconds: float = 0.0
    decay_half_life_minutes: float = 0.0
    freshness_weight: float = 1.0


_SELECT = (
    "event_kind, source, source_uri, source_id, related_markets, author, "
    "title, body, url, metadata, event_time, observed_at"
)
_EVENT_TIME_KINDS = {"macro", "onchain"}


def _parse_metadata(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_scheduled_event(*, event_kind: str, metadata: dict[str, Any]) -> bool:
    return event_kind.lower() in _EVENT_TIME_KINDS and bool(metadata.get("scheduled"))


def source_decay_half_life_minutes(
    source: str,
    event_kind: str,
    *,
    settings: Settings | None = None,
) -> float:
    cfg = settings or get_settings()
    normalized_source = source.lower()
    normalized_kind = event_kind.lower()
    if normalized_source.startswith("reddit:"):
        return cfg.external_decay_reddit_half_life_minutes
    if normalized_source.startswith("rss:"):
        return cfg.external_decay_rss_half_life_minutes
    if normalized_source.startswith("x:") or normalized_source == "x":
        return cfg.external_decay_x_half_life_minutes
    if normalized_source.startswith("discord:") or normalized_source == "discord":
        return cfg.external_decay_discord_half_life_minutes
    if normalized_kind == "macro" or normalized_source.startswith("macro:"):
        return cfg.external_decay_macro_half_life_minutes
    if normalized_kind == "onchain" or normalized_source.startswith("onchain:"):
        return cfg.external_decay_onchain_half_life_minutes
    return cfg.external_decay_default_half_life_minutes


def freshness_weight(
    *,
    source: str,
    event_kind: str,
    event_time: datetime,
    asked_at: datetime,
    settings: Settings | None = None,
) -> tuple[float, float, float]:
    half_life_minutes = max(
        source_decay_half_life_minutes(source, event_kind, settings=settings),
        1.0,
    )
    age_seconds = max((asked_at - event_time).total_seconds(), 0.0)
    half_life_seconds = half_life_minutes * 60.0
    weight = math.exp(-math.log(2.0) * age_seconds / half_life_seconds)
    return (age_seconds, half_life_minutes, weight)


def _row_to_event(
    row: tuple[object, ...],
    *,
    asked_at: datetime,
    settings: Settings | None = None,
) -> ExternalEvent:
    event_kind = str(row[0])
    source = str(row[1])
    event_time = row[10]  # type: ignore[assignment]
    age_seconds, half_life_minutes, weight = freshness_weight(
        source=source,
        event_kind=event_kind,
        event_time=event_time,
        asked_at=asked_at,
        settings=settings,
    )
    return ExternalEvent(
        event_kind=event_kind,
        source=source,
        source_uri=str(row[2]),
        source_id=str(row[3]),
        related_markets=[str(value) for value in row[4]],
        author=str(row[5]),
        title=str(row[6]),
        body=str(row[7]),
        url=str(row[8]),
        metadata=_parse_metadata(row[9]),
        event_time=event_time,
        observed_at=row[11],  # type: ignore[arg-type]
        age_seconds=age_seconds,
        decay_half_life_minutes=half_life_minutes,
        freshness_weight=weight,
    )


async def list_external_events(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_hours: int = 72,
    limit: int = 100,
    event_kinds: list[str] | None = None,
    sources: list[str] | None = None,
    condition_id: str | None = None,
    include_scheduled: bool = False,
) -> list[ExternalEvent]:
    start = asked_at - timedelta(hours=lookback_hours)
    where = [
        "observed_at <= {asof:DateTime64(3)}",
        "observed_at >= {start:DateTime64(3)}",
    ]
    parameters: dict[str, Any] = {"asof": asked_at, "start": start, "limit": limit}
    if event_kinds:
        where.append("event_kind IN {kinds:Array(String)}")
        parameters["kinds"] = list(event_kinds)
    if sources:
        where.append("source IN {sources:Array(String)}")
        parameters["sources"] = list(sources)
    if condition_id:
        where.append("has(related_markets, {cond:String})")
        parameters["cond"] = condition_id
    query = f"""
        SELECT {_SELECT}
        FROM external_events
        WHERE {' AND '.join(where)}
        ORDER BY observed_at DESC
        LIMIT {{limit:UInt32}}
    """
    result = await ch.query(query, parameters=parameters)
    events = [_row_to_event(row, asked_at=asked_at) for row in result.result_rows]
    if include_scheduled:
        return events
    return [
        event
        for event in events
        if not _is_scheduled_event(event_kind=event.event_kind, metadata=event.metadata)
    ]
