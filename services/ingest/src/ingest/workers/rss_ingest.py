"""RSS / Atom ingestion for M4.2 news events.

This worker ingests configurable public feeds into ``external_events`` and
attaches simple market links based on lexical overlap with active market text.

Current scope is intentionally conservative:

* public RSS / Atom only
* immutable append-only event rows
* no summarization or sentiment yet
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx
import structlog

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..settings import get_settings
from ..writers import EXTERNAL_EVENTS_COLS, external_event_row, utcnow

log = structlog.get_logger(__name__)

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "will",
    "into",
    "than",
    "over",
    "after",
    "amid",
    "about",
    "have",
    "has",
    "its",
    "their",
    "your",
    "next",
    "more",
    "what",
    "when",
    "where",
    "which",
    "who",
    "how",
    "why",
    "you",
    "are",
    "was",
    "were",
    "been",
    "being",
    "can",
    "could",
    "would",
    "should",
    "may",
    "might",
    "market",
    "markets",
}


@dataclass(frozen=True)
class FeedSpec:
    source: str
    url: str
    event_kind: str = "news"


@dataclass(frozen=True)
class FeedEntry:
    source: str
    source_uri: str
    source_id: str
    event_kind: str
    author: str | None
    title: str | None
    body: str | None
    url: str | None
    event_time: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MarketRef:
    condition_id: str
    token_set: set[str]


def _default_feed_path(settings_path: str | None) -> Path:
    if settings_path:
        configured = Path(settings_path)
        if configured.is_file():
            return configured
    example = Path("services/ingest/data/rss_feeds.example.json")
    return example


def load_feed_specs(path: str | None) -> list[FeedSpec]:
    file_path = _default_feed_path(path)
    if not file_path.is_file():
        return []
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    specs: list[FeedSpec] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        url = str(item.get("url") or "").strip()
        event_kind = str(item.get("event_kind") or "news").strip() or "news"
        if not source or not url:
            continue
        specs.append(FeedSpec(source=source, url=url, event_kind=event_kind))
    return specs


def parse_feed_document(
    xml_text: str,
    *,
    source: str,
    source_uri: str,
    event_kind: str,
) -> list[FeedEntry]:
    root = ElementTree.fromstring(xml_text)
    tag = _local_name(root.tag)
    if tag == "rss":
        return _parse_rss(root, source=source, source_uri=source_uri, event_kind=event_kind)
    if tag == "feed":
        return _parse_atom(root, source=source, source_uri=source_uri, event_kind=event_kind)
    return []


def _parse_rss(
    root: ElementTree.Element,
    *,
    source: str,
    source_uri: str,
    event_kind: str,
) -> list[FeedEntry]:
    channel = root.find("./channel")
    if channel is None:
        return []
    out: list[FeedEntry] = []
    for item in channel.findall("./item"):
        title = _text(item.find("./title"))
        body = _text(item.find("./description")) or _text(item.find("./content"))
        url = _text(item.find("./link"))
        source_id = (
            _text(item.find("./guid"))
            or url
            or title
            or ""
        )
        if not source_id:
            continue
        out.append(
            FeedEntry(
                source=source,
                source_uri=source_uri,
                source_id=source_id,
                event_kind=event_kind,
                author=_text(item.find("./author")) or _text(item.find("./creator")),
                title=title,
                body=body,
                url=url,
                event_time=_parse_datetime(_text(item.find("./pubDate"))),
                metadata={},
            )
        )
    return out


def _parse_atom(
    root: ElementTree.Element,
    *,
    source: str,
    source_uri: str,
    event_kind: str,
) -> list[FeedEntry]:
    out: list[FeedEntry] = []
    for item in root.findall("./{*}entry"):
        title = _text(item.find("./{*}title"))
        body = _text(item.find("./{*}summary")) or _text(item.find("./{*}content"))
        link = item.find("./{*}link")
        url = link.attrib.get("href") if link is not None else None
        source_id = _text(item.find("./{*}id")) or url or title or ""
        if not source_id:
            continue
        author_node = item.find("./{*}author/{*}name")
        out.append(
            FeedEntry(
                source=source,
                source_uri=source_uri,
                source_id=source_id,
                event_kind=event_kind,
                author=_text(author_node),
                title=title,
                body=body,
                url=url,
                event_time=_parse_datetime(
                    _text(item.find("./{*}updated")) or _text(item.find("./{*}published"))
                ),
                metadata={},
            )
        )
    return out


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=UTC)
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            return datetime.now(tz=UTC)


async def _fetch_feed(client: httpx.AsyncClient, spec: FeedSpec) -> list[FeedEntry]:
    response = await client.get(spec.url)
    response.raise_for_status()
    return parse_feed_document(
        response.text,
        source=spec.source,
        source_uri=spec.url,
        event_kind=spec.event_kind,
    )


async def _latest_active_markets(
    ch: Any,
    *,
    asked_at: datetime,
    limit: int,
) -> list[MarketRef]:
    query = """
        SELECT condition_id, question, slug, tags, active, closed, archived
        FROM markets_snapshots
        WHERE observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
        ORDER BY volume_usdc DESC
        LIMIT {limit:UInt32}
    """
    result = await ch.query(query, parameters={"asof": asked_at, "limit": limit})
    out: list[MarketRef] = []
    for condition_id, question, slug, tags, active, closed, archived in result.result_rows:
        if not bool(active) or bool(closed) or bool(archived):
            continue
        tokens = _tokens(" ".join([str(question or ""), str(slug or ""), " ".join(tags or [])]))
        if not tokens:
            continue
        out.append(MarketRef(condition_id=str(condition_id), token_set=tokens))
    return out


async def _existing_source_ids(
    ch: Any,
    *,
    sources: list[str],
    source_ids: list[str],
    start: datetime,
    asked_at: datetime,
) -> set[tuple[str, str]]:
    if not sources or not source_ids:
        return set()
    query = """
        SELECT source, source_id
        FROM external_events
        WHERE source IN {sources:Array(String)}
          AND source_id IN {source_ids:Array(String)}
          AND observed_at >= {start:DateTime64(3)}
          AND observed_at <= {asof:DateTime64(3)}
    """
    result = await ch.query(
        query,
        parameters={
            "sources": sources,
            "source_ids": source_ids,
            "start": start,
            "asof": asked_at,
        },
    )
    return {(str(source), str(source_id)) for source, source_id in result.result_rows}


def _match_related_markets(
    entry: FeedEntry,
    markets: list[MarketRef],
    *,
    max_related_markets: int,
) -> list[str]:
    text_tokens = _tokens(" ".join([entry.title or "", entry.body or ""]))
    if not text_tokens:
        return []
    scored: list[tuple[int, str]] = []
    for market in markets:
        overlap = len(text_tokens & market.token_set)
        if overlap >= 2:
            scored.append((overlap, market.condition_id))
    scored.sort(reverse=True)
    return [condition_id for _, condition_id in scored[:max_related_markets]]


def _tokens(value: str) -> set[str]:
    out: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9$]{3,}", value.lower()):
        cleaned = token.strip("$")
        if not cleaned or cleaned in _STOPWORDS:
            continue
        out.add(cleaned)
    aliases = {
        "bitcoin": "btc",
        "ethereum": "eth",
        "solana": "sol",
        "federal": "fed",
    }
    expanded = {aliases.get(token, token) for token in out}
    return expanded | {alias for alias, target in aliases.items() if target in expanded}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: ElementTree.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    text = re.sub(r"<[^>]+>", " ", node.text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


async def build_external_event_rows(
    ch: Any,
    *,
    observed_at: datetime,
    specs: list[FeedSpec],
    timeout_s: float,
    max_markets: int,
    max_related_markets: int,
    fetcher: Any | None = None,
) -> list[tuple[object, ...]]:
    if not specs:
        return []
    markets = await _latest_active_markets(ch, asked_at=observed_at, limit=max_markets)
    async with httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=True,
        headers={"user-agent": "PolyPredictor/0.0.1"},
    ) as client:
        entries: list[FeedEntry] = []
        for spec in specs:
            source_entries = await (
                fetcher(spec) if fetcher is not None else _fetch_feed(client, spec)
            )
            entries.extend(source_entries)
    existing = await _existing_source_ids(
        ch,
        sources=sorted({entry.source for entry in entries}),
        source_ids=sorted({entry.source_id for entry in entries}),
        start=observed_at - timedelta(days=30),
        asked_at=observed_at,
    )
    rows: list[tuple[object, ...]] = []
    for entry in entries:
        if (entry.source, entry.source_id) in existing:
            continue
        rows.append(
            external_event_row(
                event_kind=entry.event_kind,
                source=entry.source,
                source_uri=entry.source_uri,
                source_id=entry.source_id,
                related_markets=_match_related_markets(
                    entry,
                    markets,
                    max_related_markets=max_related_markets,
                ),
                author=entry.author,
                title=entry.title,
                body=entry.body,
                url=entry.url,
                metadata=entry.metadata,
                event_time=entry.event_time,
                observed_at=observed_at,
            )
        )
    return rows


async def run_once() -> int:
    settings = get_settings()
    specs = load_feed_specs(settings.rss_feed_sources_file)
    if not specs:
        return 0
    ch = await get_async_client()
    try:
        observed_at = utcnow()
        rows = await build_external_event_rows(
            ch,
            observed_at=observed_at,
            specs=specs,
            timeout_s=settings.rss_feed_timeout_s,
            max_markets=settings.rss_feed_max_markets,
            max_related_markets=settings.rss_feed_max_related_markets,
        )
        if not rows:
            log.info("rss_ingest.done", events=0)
            return 0
        await ch.insert("external_events", rows, column_names=EXTERNAL_EVENTS_COLS)
        log.info("rss_ingest.done", events=len(rows))
        return len(rows)
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    dlq = DeadLetterQueue(settings.redis_url, stream="rss_ingest")
    try:
        while True:
            try:
                await run_once()
            except Exception as exc:
                log.error("rss_ingest.failed", error=repr(exc))
                await dlq.push(
                    source="rss",
                    reason="rss_ingest_failed",
                    payload={"error": repr(exc)},
                )
            await asyncio.sleep(settings.rss_interval_s)
    finally:
        await dlq.aclose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
