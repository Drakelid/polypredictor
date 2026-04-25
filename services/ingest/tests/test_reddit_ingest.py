from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.reddit_ingest import (
    RedditPost,
    build_external_event_rows,
    parse_listing_document,
    parse_subreddits,
)
from ingest.writers import EXTERNAL_EVENTS_COLS


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, results: list[list[tuple[object, ...]]]) -> None:
        self.results = results

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        return _QueryResult(self.results.pop(0))


def test_parse_subreddits_normalizes_and_dedupes() -> None:
    assert parse_subreddits("CryptoCurrency, r/wallstreetbets, CryptoCurrency") == [
        "CryptoCurrency",
        "wallstreetbets",
    ]


def test_parse_listing_document_supports_reddit_posts() -> None:
    payload = """
    {
      "data": {
        "children": [
          {
            "kind": "t3",
            "data": {
              "id": "abc123",
              "author": "alice",
              "title": "Bitcoin ETF chatter spikes",
              "selftext": "Retail is suddenly obsessed with BTC ETF flow again.",
              "permalink": "/r/CryptoCurrency/comments/abc123/example/",
              "url": "https://www.reddit.com/r/CryptoCurrency/comments/abc123/example/",
              "subreddit": "CryptoCurrency",
              "score": 42,
              "upvote_ratio": 0.91,
              "num_comments": 17,
              "domain": "self.CryptoCurrency",
              "is_self": true,
              "over_18": false,
              "link_flair_text": "DISCUSSION",
              "created_utc": 1777032000
            }
          }
        ]
      }
    }
    """

    posts = parse_listing_document(
        payload,
        subreddit="CryptoCurrency",
        source_uri="https://www.reddit.com/r/CryptoCurrency/new.json?limit=1",
    )

    assert len(posts) == 1
    assert posts[0].source == "reddit:CryptoCurrency"
    assert posts[0].source_id == "abc123"
    assert posts[0].metadata["score"] == 42
    assert posts[0].source_uri.endswith("/comments/abc123/example/")


@pytest.mark.asyncio
async def test_build_external_event_rows_matches_related_markets_and_dedupes() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [
                ("cond-btc", "Will Bitcoin ETF approvals drive BTC higher?", "btc-etf", ["btc", "etf"], 1, 0, 0),
                ("cond-fed", "Will the Fed cut in June?", "fed-cut-june", ["fed", "rates"], 1, 0, 0),
            ],
            [("reddit:CryptoCurrency", "existing-post")],
        ]
    )

    async def _fake_fetcher(subreddit: str) -> list[RedditPost]:
        return [
            RedditPost(
                source=f"reddit:{subreddit}",
                source_uri="https://www.reddit.com/r/CryptoCurrency/comments/newpost/example/",
                source_id="new-post",
                event_kind="social",
                author="alice",
                title="Bitcoin ETF demand returns",
                body="BTC ETF talk is back across retail traders.",
                url="https://www.reddit.com/r/CryptoCurrency/comments/newpost/example/",
                event_time=observed_at,
                metadata={"subreddit": subreddit, "score": 10, "num_comments": 3},
            ),
            RedditPost(
                source=f"reddit:{subreddit}",
                source_uri="https://www.reddit.com/r/CryptoCurrency/comments/existing/example/",
                source_id="existing-post",
                event_kind="social",
                author="bob",
                title="Old post",
                body="Already seen",
                url="https://www.reddit.com/r/CryptoCurrency/comments/existing/example/",
                event_time=observed_at,
                metadata={"subreddit": subreddit},
            ),
        ]

    rows = await build_external_event_rows(
        ch,
        observed_at=observed_at,
        subreddits=["CryptoCurrency"],
        limit=10,
        timeout_s=5.0,
        max_markets=100,
        max_related_markets=3,
        fetcher=_fake_fetcher,
    )

    assert len(rows) == 1
    payload = dict(zip(EXTERNAL_EVENTS_COLS, rows[0], strict=True))
    assert payload["source"] == "reddit:CryptoCurrency"
    assert payload["source_id"] == "new-post"
    assert payload["event_kind"] == "social"
    assert payload["related_markets"] == ["cond-btc"]
