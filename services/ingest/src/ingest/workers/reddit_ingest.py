"""Reddit ingestion for M4.2 retail-sentiment inputs.

This worker ingests public subreddit listing JSON into ``external_events`` and
reuses the RSS worker's lexical market linker so downstream consumers only need
one immutable external-events feed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..settings import get_settings
from ..writers import EXTERNAL_EVENTS_COLS, external_event_row, utcnow
from .rss_ingest import (
    _existing_source_ids,
    _latest_active_markets,
    _match_related_markets,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RedditPost:
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


def parse_subreddits(raw: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in raw.split(","):
        subreddit = value.strip().lstrip("r/").lstrip("/")
        if not subreddit or subreddit in seen:
            continue
        seen.add(subreddit)
        out.append(subreddit)
    return out


def listing_url(subreddit: str, *, limit: int) -> str:
    return f"https://www.reddit.com/r/{subreddit}/new.json?limit={int(limit)}"


def parse_listing_document(
    payload_text: str,
    *,
    subreddit: str,
    source_uri: str,
    event_kind: str = "social",
) -> list[RedditPost]:
    payload = json.loads(payload_text)
    data = payload.get("data") if isinstance(payload, dict) else None
    children = data.get("children") if isinstance(data, dict) else None
    if not isinstance(children, list):
        return []
    out: list[RedditPost] = []
    canonical_subreddit = subreddit.strip()
    for child in children:
        if not isinstance(child, dict):
            continue
        post = child.get("data")
        if not isinstance(post, dict):
            continue
        source_id = str(post.get("id") or "").strip()
        permalink = str(post.get("permalink") or "").strip()
        if not source_id or not permalink:
            continue
        canonical_url = f"https://www.reddit.com{permalink}"
        raw_created = post.get("created_utc")
        try:
            created_at = datetime.fromtimestamp(float(raw_created), tz=UTC)
        except (TypeError, ValueError):
            created_at = datetime.now(tz=UTC)
        out.append(
            RedditPost(
                source=f"reddit:{canonical_subreddit}",
                source_uri=canonical_url,
                source_id=source_id,
                event_kind=event_kind,
                author=str(post.get("author") or "").strip() or None,
                title=str(post.get("title") or "").strip() or None,
                body=str(post.get("selftext") or "").strip() or None,
                url=str(post.get("url_overridden_by_dest") or post.get("url") or "").strip()
                or canonical_url,
                event_time=created_at,
                metadata={
                    "subreddit": str(post.get("subreddit") or canonical_subreddit),
                    "permalink": permalink,
                    "score": int(post.get("score") or 0),
                    "upvote_ratio": float(post.get("upvote_ratio") or 0.0),
                    "num_comments": int(post.get("num_comments") or 0),
                    "domain": str(post.get("domain") or ""),
                    "is_self": bool(post.get("is_self")),
                    "over_18": bool(post.get("over_18")),
                    "link_flair_text": str(post.get("link_flair_text") or ""),
                    "listing_uri": source_uri,
                },
            )
        )
    return out


async def _fetch_listing(
    client: httpx.AsyncClient,
    *,
    subreddit: str,
    limit: int,
) -> list[RedditPost]:
    response = await client.get(listing_url(subreddit, limit=limit))
    response.raise_for_status()
    return parse_listing_document(
        response.text,
        subreddit=subreddit,
        source_uri=str(response.url),
    )


async def build_external_event_rows(
    ch: Any,
    *,
    observed_at: datetime,
    subreddits: list[str],
    limit: int,
    timeout_s: float,
    max_markets: int,
    max_related_markets: int,
    fetcher: Any | None = None,
) -> list[tuple[object, ...]]:
    if not subreddits:
        return []
    markets = await _latest_active_markets(ch, asked_at=observed_at, limit=max_markets)
    async with httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=True,
        headers={"user-agent": "PolyPredictor/0.0.1"},
    ) as client:
        posts: list[RedditPost] = []
        for subreddit in subreddits:
            subreddit_posts = await (
                fetcher(subreddit)
                if fetcher is not None
                else _fetch_listing(client, subreddit=subreddit, limit=limit)
            )
            posts.extend(subreddit_posts)
    existing = await _existing_source_ids(
        ch,
        sources=sorted({post.source for post in posts}),
        source_ids=sorted({post.source_id for post in posts}),
        start=observed_at - timedelta(days=30),
        asked_at=observed_at,
    )
    rows: list[tuple[object, ...]] = []
    for post in posts:
        if (post.source, post.source_id) in existing:
            continue
        rows.append(
            external_event_row(
                event_kind=post.event_kind,
                source=post.source,
                source_uri=post.source_uri,
                source_id=post.source_id,
                related_markets=_match_related_markets(
                    post,
                    markets,
                    max_related_markets=max_related_markets,
                ),
                author=post.author,
                title=post.title,
                body=post.body,
                url=post.url,
                metadata=post.metadata,
                event_time=post.event_time,
                observed_at=observed_at,
            )
        )
    return rows


async def run_once() -> int:
    settings = get_settings()
    subreddits = parse_subreddits(settings.reddit_subreddits)
    if not subreddits:
        return 0
    ch = await get_async_client()
    try:
        observed_at = utcnow()
        rows = await build_external_event_rows(
            ch,
            observed_at=observed_at,
            subreddits=subreddits,
            limit=settings.reddit_fetch_limit,
            timeout_s=settings.reddit_timeout_s,
            max_markets=settings.reddit_max_markets,
            max_related_markets=settings.reddit_max_related_markets,
        )
        if not rows:
            log.info("reddit_ingest.done", events=0)
            return 0
        await ch.insert("external_events", rows, column_names=EXTERNAL_EVENTS_COLS)
        log.info("reddit_ingest.done", events=len(rows))
        return len(rows)
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    dlq = DeadLetterQueue(settings.redis_url, stream="reddit_ingest")
    try:
        while True:
            try:
                await run_once()
            except Exception as exc:
                log.error("reddit_ingest.failed", error=repr(exc))
                await dlq.push(
                    source="reddit",
                    reason="reddit_ingest_failed",
                    payload={"error": repr(exc)},
                )
            await asyncio.sleep(settings.reddit_interval_s)
    finally:
        await dlq.aclose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
