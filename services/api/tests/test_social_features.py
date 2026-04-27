from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from api.social_features import (
    SocialPost,
    compute_social_features,
    social_features_batch_asof,
    social_features_for_market,
)


def _post(
    *,
    title: str = "",
    body: str = "",
    event_time: datetime,
    score: int | None = None,
    source: str = "reddit:r/CryptoCurrency",
) -> SocialPost:
    return SocialPost(
        source=source,
        title=title,
        body=body,
        event_time=event_time,
        metadata={"score": score} if score is not None else {},
    )


def test_compute_social_features_handles_no_posts() -> None:
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    out = compute_social_features(
        asked_at=asked_at,
        condition_id="cond-1",
        posts_7d=[],
    )
    assert out.post_count_24h == 0
    assert out.post_count_7d == 0
    assert out.novelty_score_latest == 0.0
    assert out.sentiment_dispersion_24h == 0.0


def test_compute_social_features_splits_24h_from_baseline() -> None:
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    posts = [
        _post(title="ETH crash and panic", event_time=asked_at - timedelta(days=4)),
        _post(title="ETH crash again", event_time=asked_at - timedelta(days=3)),
        _post(title="ETH bullish rally today", event_time=asked_at - timedelta(hours=6)),
        _post(title="ETH strong demand", event_time=asked_at - timedelta(hours=1)),
    ]
    out = compute_social_features(
        asked_at=asked_at,
        condition_id="cond-1",
        posts_7d=posts,
    )
    assert out.post_count_24h == 2
    assert out.post_count_7d == 4
    # 24h posts are positive sentiment, baseline is negative -> tone shift positive
    assert out.tone_shift_24h_vs_7d > 0.0


def test_compute_social_features_dispersion_high_when_disagreement() -> None:
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    posts = [
        _post(title="bullish rally", event_time=asked_at - timedelta(hours=1)),
        _post(title="crash and dump", event_time=asked_at - timedelta(hours=2)),
    ]
    out = compute_social_features(
        asked_at=asked_at,
        condition_id="cond-1",
        posts_7d=posts,
    )
    assert out.post_count_24h == 2
    # Polarized posts -> non-zero dispersion
    assert out.sentiment_dispersion_24h > 0.0


def test_compute_social_features_uses_score_as_reach_proxy() -> None:
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    posts = [
        _post(
            title="bullish",
            event_time=asked_at - timedelta(hours=2),
            score=120,
        ),
        _post(
            title="rally",
            event_time=asked_at - timedelta(hours=1),
            score=80,
        ),
    ]
    out = compute_social_features(
        asked_at=asked_at,
        condition_id="cond-1",
        posts_7d=posts,
    )
    assert out.reach_volume_24h == pytest.approx(200.0)


def test_compute_social_features_falls_back_to_unit_reach_when_score_absent() -> None:
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    posts = [
        _post(title="bullish", event_time=asked_at - timedelta(hours=2)),
        _post(title="rally", event_time=asked_at - timedelta(hours=1)),
    ]
    out = compute_social_features(
        asked_at=asked_at,
        condition_id="cond-1",
        posts_7d=posts,
    )
    # No score metadata -> 1.0 per post.
    assert out.reach_volume_24h == pytest.approx(2.0)


def test_compute_social_features_novelty_for_latest_vs_24h_history() -> None:
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    posts = [
        _post(title="bitcoin etf inflow", event_time=asked_at - timedelta(hours=3)),
        _post(title="bitcoin etf inflow", event_time=asked_at - timedelta(hours=2)),
        _post(title="solana ecosystem update", event_time=asked_at - timedelta(hours=1)),
    ]
    out = compute_social_features(
        asked_at=asked_at,
        condition_id="cond-1",
        posts_7d=posts,
    )
    # Latest post 'solana ecosystem update' has zero token overlap with the
    # repeated 'bitcoin etf inflow' history -> novelty 1.0.
    assert out.novelty_score_latest == pytest.approx(1.0)


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.last_query: tuple[str, dict[str, object] | None] | None = None

    async def query(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> _QueryResult:
        self.last_query = (query, parameters)
        return _QueryResult(self.rows)


@pytest.mark.asyncio
async def test_social_features_for_market_filters_to_social_kind() -> None:
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    rows = [
        (
            "reddit:r/CryptoCurrency",
            "BTC bullish rally",
            "more body text",
            json.dumps({"score": 50}),
            asked_at - timedelta(hours=2),
        ),
        (
            "reddit:r/CryptoCurrency",
            "BTC bullish demand",
            "demand returns",
            json.dumps({"score": 70}),
            asked_at - timedelta(hours=1),
        ),
    ]
    ch = _FakeClickHouse(rows)
    features = await social_features_for_market(
        ch,
        condition_id="cond-btc",
        asked_at=asked_at,
    )
    assert features.post_count_24h == 2
    assert features.reach_volume_24h == pytest.approx(120.0)
    assert ch.last_query is not None
    query, params = ch.last_query
    assert "event_kind = 'social'" in query
    assert "has(related_markets, {cond:String})" in query
    assert params == {
        "cond": "cond-btc",
        "start": asked_at - timedelta(days=7),
        "asof": asked_at,
    }


@pytest.mark.asyncio
async def test_social_features_for_market_handles_empty_metadata() -> None:
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    rows = [
        (
            "reddit:r/wallstreetbets",
            "rally",
            "",
            "",  # empty metadata string
            asked_at - timedelta(hours=4),
        ),
    ]
    ch = _FakeClickHouse(rows)
    features = await social_features_for_market(
        ch,
        condition_id="cond-1",
        asked_at=asked_at,
    )
    assert features.post_count_24h == 1
    # Score absent -> falls back to 1.0 per post.
    assert features.reach_volume_24h == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_social_features_batch_asof_buckets_posts_per_market() -> None:
    """Each row carries a related_markets array; the batched query buckets
    posts into the matching condition_ids without spending a query per market."""
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    rows = [
        (
            "reddit:r/CryptoCurrency",
            "BTC bullish",
            "demand strong",
            json.dumps({"score": 50}),
            asked_at - timedelta(hours=2),
            ["cond-btc"],
        ),
        (
            "reddit:r/CryptoCurrency",
            "ETH crash",
            "selloff",
            json.dumps({"score": 80}),
            asked_at - timedelta(hours=1),
            ["cond-eth"],
        ),
        (
            "reddit:r/CryptoCurrency",
            "BTC and ETH both rally",
            "broad strength",
            json.dumps({"score": 30}),
            asked_at - timedelta(minutes=30),
            ["cond-btc", "cond-eth"],
        ),
    ]
    ch = _FakeClickHouse(rows)

    features_by_cid = await social_features_batch_asof(
        ch,
        condition_ids=["cond-btc", "cond-eth", "cond-empty"],
        asked_at=asked_at,
    )
    assert set(features_by_cid.keys()) == {"cond-btc", "cond-eth", "cond-empty"}
    assert features_by_cid["cond-btc"].post_count_24h == 2
    assert features_by_cid["cond-eth"].post_count_24h == 2
    # The market with no posts still gets a default snapshot — no special-case.
    assert features_by_cid["cond-empty"].post_count_24h == 0
    assert features_by_cid["cond-empty"].reach_volume_24h == 0.0


@pytest.mark.asyncio
async def test_social_features_batch_asof_uses_single_query() -> None:
    """The batched helper must hit ClickHouse exactly once regardless of
    how many condition_ids are passed in — that's the whole point."""
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])
    ch.query_count = 0  # type: ignore[attr-defined]
    original_query = ch.query

    async def _counting_query(query: str, parameters=None):
        ch.query_count += 1  # type: ignore[attr-defined]
        return await original_query(query, parameters)

    ch.query = _counting_query  # type: ignore[method-assign]

    await social_features_batch_asof(
        ch,
        condition_ids=["cond-1", "cond-2", "cond-3", "cond-4", "cond-5"],
        asked_at=asked_at,
    )
    assert ch.query_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_social_features_batch_asof_empty_input_skips_query() -> None:
    """Empty input must not hit the database at all — saves a round trip
    when the list path renders an empty page."""
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])
    ch.query_count = 0  # type: ignore[attr-defined]
    original_query = ch.query

    async def _counting_query(query: str, parameters=None):
        ch.query_count += 1  # type: ignore[attr-defined]
        return await original_query(query, parameters)

    ch.query = _counting_query  # type: ignore[method-assign]

    out = await social_features_batch_asof(
        ch,
        condition_ids=[],
        asked_at=asked_at,
    )
    assert out == {}
    assert ch.query_count == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_social_features_batch_asof_uses_hasAny_filter() -> None:
    """The batched query must use ClickHouse's hasAny so a single
    related_markets array can match any of the requested condition_ids."""
    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])
    await social_features_batch_asof(
        ch,
        condition_ids=["cond-a", "cond-b"],
        asked_at=asked_at,
    )
    assert ch.last_query is not None
    query, params = ch.last_query
    assert "hasAny(related_markets, {conds:Array(String)})" in query
    assert params["conds"] == ["cond-a", "cond-b"]
