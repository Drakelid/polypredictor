"""X/Twitter Basic API filtered-stream ingestion worker (M4.1).

This worker connects to the Twitter/X v2 filtered-stream endpoint
(``https://api.twitter.com/2/tweets/search/stream``) using a Bearer Token
from the operator's Basic tier account and writes matching tweets into
``external_events``, reusing the same schema as ``reddit_ingest`` and
``rss_ingest``.  The KOL-loader index is applied so that tweets from
tracked KOL accounts are stamped with ``metadata.kol_categories`` and
``metadata.is_kol``, feeding the credibility-weighted social-feature path.

Configuration
-------------
* ``X_BEARER_TOKEN`` — required; obtained from the X Developer Portal.
* ``X_FILTERED_STREAM_RULES_FILE`` — path to a JSON file describing the
  filter rules to add on start-up (see ``x_stream_rules.example.json``).
* ``x_ingest_max_markets`` / ``x_ingest_max_related_markets`` — same role
  as the Reddit equivalents.

When ``X_BEARER_TOKEN`` is absent the worker exits cleanly (no-op) so the
standard ingest supervisor does not fail on un-configured deployments.

Rate limits
-----------
The Basic tier filtered-stream allows 50 rules and up to 500,000 reads/month.
We rely on the server-sent event stream (no polling loop) so our read load is
entirely Twitter-push-driven.  Reconnects use exponential backoff per the X
developer guidelines: wait 5s, 10s, 20s … up to 320s.
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
from ..kol_loader import kol_categories_for_author, load_kol_index
from ..provider_credentials import settings_with_stored_provider_credentials
from ..settings import get_settings
from ..writers import EXTERNAL_EVENTS_COLS, external_event_row, utcnow
from .rss_ingest import (
    _existing_source_ids,
    _latest_active_markets,
    _match_related_markets,
)

log = structlog.get_logger(__name__)

_STREAM_URL = "https://api.twitter.com/2/tweets/search/stream"
_RULES_URL = "https://api.twitter.com/2/tweets/search/stream/rules"

# Tweet fields returned by the v2 API that we want in every response.
_TWEET_FIELDS = ",".join(
    [
        "author_id",
        "created_at",
        "entities",
        "lang",
        "public_metrics",
        "possibly_sensitive",
        "referenced_tweets",
        "source",
    ]
)
_USER_FIELDS = "id,name,username,public_metrics,verified"
_EXPANSIONS = "author_id,referenced_tweets.id"


@dataclass(frozen=True)
class StreamRule:
    """A single filtered-stream rule."""

    value: str
    tag: str


@dataclass(frozen=True)
class XTweet:
    """A parsed tweet from the filtered stream."""

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


# ---------------------------------------------------------------------------
# Rule management
# ---------------------------------------------------------------------------


def load_stream_rules(rules_file: str | None) -> list[StreamRule]:
    """Load filter rules from a JSON config file.

    The file should be a JSON array of objects with ``value`` and ``tag``
    keys, e.g.::

        [
          {"value": "from:APompliano OR from:WClementeIII", "tag": "crypto_kol"},
          {"value": "#Bitcoin -is:retweet lang:en", "tag": "btc_hashtag"}
        ]

    Returns an empty list when ``rules_file`` is ``None`` or the file
    cannot be read.
    """
    if not rules_file:
        return []
    try:
        with open(rules_file, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, list):
            return []
        rules: list[StreamRule] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            tag = str(item.get("tag") or "").strip()
            if value:
                rules.append(StreamRule(value=value, tag=tag or value[:32]))
        return rules
    except Exception as exc:
        log.warning("x_ingest.rules_load_failed", error=repr(exc))
        return []


async def _get_active_rules(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch the currently active filtered-stream rules."""
    resp = await client.get(_RULES_URL)
    resp.raise_for_status()
    data = resp.json()
    return list(data.get("data") or [])


async def _delete_all_rules(
    client: httpx.AsyncClient, rules: list[dict[str, Any]]
) -> None:
    """Delete a list of rules by their IDs."""
    ids = [str(r["id"]) for r in rules if r.get("id")]
    if not ids:
        return
    resp = await client.post(_RULES_URL, json={"delete": {"ids": ids}})
    resp.raise_for_status()


async def _add_rules(client: httpx.AsyncClient, rules: list[StreamRule]) -> None:
    """Add filter rules to the stream."""
    if not rules:
        return
    payload = {"add": [{"value": r.value, "tag": r.tag} for r in rules]}
    resp = await client.post(_RULES_URL, json=payload)
    resp.raise_for_status()


async def sync_rules(client: httpx.AsyncClient, desired: list[StreamRule]) -> None:
    """Replace all active rules with ``desired`` (delete-then-add)."""
    existing = await _get_active_rules(client)
    if existing:
        await _delete_all_rules(client, existing)
    await _add_rules(client, desired)
    log.info("x_ingest.rules_synced", count=len(desired))


# ---------------------------------------------------------------------------
# Tweet parsing
# ---------------------------------------------------------------------------


def _tweet_url(tweet_id: str, username: str | None) -> str:
    if username:
        return f"https://twitter.com/{username}/status/{tweet_id}"
    return f"https://twitter.com/i/web/status/{tweet_id}"


def parse_stream_line(
    line: bytes,
    *,
    users_by_id: dict[str, dict[str, Any]],
) -> XTweet | None:
    """Parse one server-sent event line from the filtered stream.

    Returns ``None`` for keep-alive empty lines or unrecognised payloads.
    """
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    tweet_id = str(data.get("id") or "").strip()
    body = str(data.get("text") or "").strip()
    if not tweet_id or not body:
        return None

    # Resolve author username from includes
    author_id = str(data.get("author_id") or "").strip()
    author_info = users_by_id.get(author_id, {})
    username = str(author_info.get("username") or "").strip() or None

    raw_created = data.get("created_at")
    try:
        event_time = datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        event_time = datetime.now(tz=UTC)

    metrics = data.get("public_metrics") or {}
    impression_count = int(metrics.get("impression_count") or 0)
    retweet_count = int(metrics.get("retweet_count") or 0)
    like_count = int(metrics.get("like_count") or 0)
    reply_count = int(metrics.get("reply_count") or 0)

    url = _tweet_url(tweet_id, username)
    # Extract matched rule tags from matching_rules
    rule_tags: list[str] = [
        str(r.get("tag") or "")
        for r in (payload.get("matching_rules") or [])
        if r.get("tag")
    ]

    return XTweet(
        source="x",
        source_uri=url,
        source_id=tweet_id,
        event_kind="social",
        author=username,
        title=None,  # tweets have no separate title; full text is in body
        body=body,
        url=url,
        event_time=event_time,
        metadata={
            "author_id": author_id,
            "username": username,
            "impression_count": impression_count,
            "retweet_count": retweet_count,
            "like_count": like_count,
            "reply_count": reply_count,
            "lang": str(data.get("lang") or ""),
            "possibly_sensitive": bool(data.get("possibly_sensitive")),
            "source_app": str(data.get("source") or ""),
            "rule_tags": rule_tags,
        },
    )


# ---------------------------------------------------------------------------
# Row builder (testable without a live HTTP connection)
# ---------------------------------------------------------------------------


async def build_external_event_rows_from_tweets(
    ch: Any,
    *,
    tweets: list[XTweet],
    observed_at: datetime,
    max_markets: int,
    max_related_markets: int,
    kol_index: dict[str, list[str]] | None = None,
) -> list[tuple[object, ...]]:
    """Convert a batch of parsed tweets into ``external_events`` rows."""
    if not tweets:
        return []
    markets = await _latest_active_markets(ch, asked_at=observed_at, limit=max_markets)
    existing = await _existing_source_ids(
        ch,
        sources=["x"],
        source_ids=sorted({t.source_id for t in tweets}),
        start=observed_at - timedelta(days=7),
        asked_at=observed_at,
    )
    rows: list[tuple[object, ...]] = []
    for tweet in tweets:
        if ("x", tweet.source_id) in existing:
            continue
        kol_categories = kol_categories_for_author(tweet.author, kol_index)
        if kol_categories:
            metadata = {
                **tweet.metadata,
                "kol_categories": kol_categories,
                "is_kol": True,
            }
        else:
            metadata = tweet.metadata
        rows.append(
            external_event_row(
                event_kind=tweet.event_kind,
                source=tweet.source,
                source_uri=tweet.source_uri,
                source_id=tweet.source_id,
                related_markets=_match_related_markets(
                    tweet,  # type: ignore[arg-type]
                    markets,
                    max_related_markets=max_related_markets,
                ),
                author=tweet.author,
                title=tweet.title,
                body=tweet.body,
                url=tweet.url,
                metadata=metadata,
                event_time=tweet.event_time,
                observed_at=observed_at,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Stream loop
# ---------------------------------------------------------------------------

_BACKOFF_INITIAL_S = 5.0
_BACKOFF_MAX_S = 320.0


async def _stream_forever(
    bearer_token: str,
    rules: list[StreamRule],
    *,
    settings: Any,
    ch: Any,
    dlq: DeadLetterQueue,
    kol_index: dict[str, list[str]] | None,
) -> None:
    """Connect to the filtered stream, ingest tweets, reconnect on failure."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    backoff = _BACKOFF_INITIAL_S

    while True:
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=httpx.Timeout(60.0, read=None)
            ) as client:
                await sync_rules(client, rules)

                params: dict[str, str] = {
                    "tweet.fields": _TWEET_FIELDS,
                    "user.fields": _USER_FIELDS,
                    "expansions": _EXPANSIONS,
                }
                async with client.stream("GET", _STREAM_URL, params=params) as resp:
                    resp.raise_for_status()
                    backoff = _BACKOFF_INITIAL_S  # reset on successful connect
                    log.info("x_ingest.stream_connected")

                    tweet_batch: list[XTweet] = []
                    async for line in resp.aiter_lines():
                        observed_at = utcnow()
                        # Build users index from most recent response includes
                        # (the v2 stream puts user objects in the same JSON blob)
                        try:
                            payload = json.loads(line) if line.strip() else {}
                        except json.JSONDecodeError:
                            payload = {}
                        includes = payload.get("includes") or {}
                        users_by_id: dict[str, dict[str, Any]] = {
                            str(u.get("id") or ""): u
                            for u in (includes.get("users") or [])
                            if isinstance(u, dict)
                        }
                        tweet = parse_stream_line(
                            line.encode("utf-8"),
                            users_by_id=users_by_id,
                        )
                        if tweet is not None:
                            tweet_batch.append(tweet)

                        # Flush every 10 tweets or on each non-empty message
                        if len(tweet_batch) >= 10:
                            rows = await build_external_event_rows_from_tweets(
                                ch,
                                tweets=tweet_batch,
                                observed_at=observed_at,
                                max_markets=settings.x_ingest_max_markets,
                                max_related_markets=settings.x_ingest_max_related_markets,
                                kol_index=kol_index,
                            )
                            if rows:
                                await ch.insert(
                                    "external_events",
                                    rows,
                                    column_names=EXTERNAL_EVENTS_COLS,
                                )
                                log.info("x_ingest.flushed", events=len(rows))
                            tweet_batch = []

        except Exception as exc:
            log.error("x_ingest.stream_error", error=repr(exc), backoff=backoff)
            await dlq.push(
                source="x",
                reason="stream_error",
                payload={"error": repr(exc)},
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX_S)


async def run_forever() -> None:
    """Entry point: start the filtered-stream worker."""
    logging.basicConfig(level=get_settings().log_level)
    settings = await settings_with_stored_provider_credentials(get_settings())
    bearer_token = settings.x_bearer_token
    if not bearer_token:
        log.info("x_ingest.no_bearer_token_configured_skip")
        return

    rules = load_stream_rules(settings.x_filtered_stream_rules_file)
    if not rules:
        log.info("x_ingest.no_rules_configured_skip")
        return

    kol_index = load_kol_index(settings.reddit_kol_lists_file)
    ch = await get_async_client()
    dlq = DeadLetterQueue(settings.redis_url, stream="x_ingest")
    try:
        await _stream_forever(
            bearer_token,
            rules,
            settings=settings,
            ch=ch,
            dlq=dlq,
            kol_index=kol_index,
        )
    finally:
        await dlq.aclose()
        await ch.close()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
