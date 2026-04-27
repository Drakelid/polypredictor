from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api import kol_credibility
from api.kol_credibility import (
    DEFAULT_HALF_LIFE_DAYS,
    DEFAULT_NEUTRAL_WEIGHT,
    DEFAULT_WEIGHT_HI,
    DEFAULT_WEIGHT_LO,
    KolEdgeContribution,
    KolPost,
    aggregate_kol_credibility,
    credibility_index_from_rows,
    kol_credibility_index_asof,
    load_first_resolutions,
    load_kol_posts_asof,
)


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, responses: list[list[tuple[object, ...]]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self._responses[len(self.calls) - 1])


_NOW = datetime(2026, 4, 26, 12, tzinfo=UTC)


def _post(
    *,
    author: str,
    text: str,
    related_markets: tuple[str, ...] = ("cond-1",),
    days_ago: float = 1.0,
) -> KolPost:
    return KolPost(
        author=author,
        related_markets=related_markets,
        text=text,
        event_time=_NOW - timedelta(days=days_ago),
    )


# ---------------------------------------------------------------------------
# aggregate_kol_credibility
# ---------------------------------------------------------------------------


def test_aggregate_returns_neutral_weight_for_authors_below_min_samples() -> None:
    posts = [_post(author="alice", text="rally and surge")]
    resolutions = {"cond-1": 1}

    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=_NOW,
        min_samples=3,
    )

    assert len(rows) == 1
    assert rows[0].author == "alice"
    assert rows[0].sample_count == 1
    assert rows[0].correct_count == 1
    assert rows[0].credibility_weight == pytest.approx(DEFAULT_NEUTRAL_WEIGHT)


def test_aggregate_rewards_consistently_correct_author() -> None:
    posts = [
        _post(author="alice", text="rally surge soar", related_markets=("c1",), days_ago=1.0),
        _post(author="alice", text="bullish growth rebound", related_markets=("c2",), days_ago=2.0),
        _post(author="alice", text="strong outperform expanding", related_markets=("c3",), days_ago=3.0),
    ]
    resolutions = {"c1": 1, "c2": 1, "c3": 1}

    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=_NOW,
        min_samples=3,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.sample_count == 3
    assert row.correct_count == 3
    assert row.credibility_weight == pytest.approx(DEFAULT_WEIGHT_HI)
    assert row.raw_hit_rate == pytest.approx(1.0)


def test_aggregate_penalizes_consistently_wrong_author() -> None:
    posts = [
        _post(author="bob", text="rally surge", related_markets=("c1",), days_ago=1.0),
        _post(author="bob", text="bullish growth", related_markets=("c2",), days_ago=2.0),
        _post(author="bob", text="strong outperform", related_markets=("c3",), days_ago=3.0),
    ]
    # Always called bullish, but markets all resolved NO -> wrong every time.
    resolutions = {"c1": 0, "c2": 0, "c3": 0}

    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=_NOW,
        min_samples=3,
    )

    row = rows[0]
    assert row.correct_count == 0
    assert row.credibility_weight == pytest.approx(DEFAULT_WEIGHT_LO)
    assert row.raw_hit_rate == pytest.approx(0.0)


def test_aggregate_skips_neutral_sentiment() -> None:
    # A post with no scored tokens contributes 0 sentiment and is skipped.
    posts = [_post(author="carol", text="apple banana table chair", days_ago=1.0)]
    resolutions = {"cond-1": 1}

    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=_NOW,
    )

    assert rows == []


def test_aggregate_skips_unresolved_markets() -> None:
    posts = [
        _post(author="alice", text="rally surge", related_markets=("c1", "c2"), days_ago=1.0),
    ]
    # Only c1 is resolved.
    resolutions = {"c1": 1}

    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=_NOW,
        min_samples=1,
    )

    assert len(rows) == 1
    assert rows[0].sample_count == 1


def test_aggregate_decay_weights_recent_posts_more() -> None:
    posts = [
        _post(author="dave", text="rally surge", related_markets=("c1",), days_ago=0.0),
        _post(author="dave", text="bearish crash", related_markets=("c2",), days_ago=180.0),
    ]
    resolutions = {"c1": 1, "c2": 1}  # recent post correct, old wrong

    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=_NOW,
        half_life_days=30.0,
        min_samples=2,
    )

    row = rows[0]
    # Recent correct post dominates the weighted edge.
    assert row.weighted_edge > 0.5
    assert row.credibility_weight > DEFAULT_NEUTRAL_WEIGHT


def test_aggregate_min_samples_threshold_keeps_single_correct_call_neutral() -> None:
    posts = [_post(author="eve", text="rally surge", days_ago=1.0)]
    resolutions = {"cond-1": 1}

    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=_NOW,
        min_samples=5,
    )

    assert rows[0].credibility_weight == pytest.approx(DEFAULT_NEUTRAL_WEIGHT)


def test_aggregate_rejects_inverted_weight_band() -> None:
    with pytest.raises(ValueError):
        aggregate_kol_credibility(
            [],
            {},
            asked_at=_NOW,
            weight_lo=1.5,
            weight_hi=0.5,
        )


def test_aggregate_handles_negative_sentiment_correctly() -> None:
    # NO-predicting post against a NO-resolving market should be correct.
    posts = [
        _post(author="frank", text="crash bearish dump", related_markets=("c1",), days_ago=1.0),
        _post(author="frank", text="crash dump weak", related_markets=("c2",), days_ago=2.0),
        _post(author="frank", text="loss bearish weak", related_markets=("c3",), days_ago=3.0),
    ]
    resolutions = {"c1": 0, "c2": 0, "c3": 0}

    rows = aggregate_kol_credibility(
        posts,
        resolutions,
        asked_at=_NOW,
        min_samples=3,
    )

    row = rows[0]
    assert row.correct_count == 3
    assert row.credibility_weight == pytest.approx(DEFAULT_WEIGHT_HI)


# ---------------------------------------------------------------------------
# credibility_index_from_rows
# ---------------------------------------------------------------------------


def test_credibility_index_from_rows_lowercases_authors() -> None:
    rows = [
        KolEdgeContribution(
            author="Alice",
            sample_count=5,
            correct_count=4,
            weighted_edge=0.6,
            raw_hit_rate=0.8,
            credibility_weight=1.3,
        ),
        KolEdgeContribution(
            author="BOB",
            sample_count=5,
            correct_count=1,
            weighted_edge=-0.6,
            raw_hit_rate=0.2,
            credibility_weight=0.7,
        ),
    ]

    index = credibility_index_from_rows(rows)

    assert index == {"alice": 1.3, "bob": 0.7}


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_kol_posts_asof_filters_to_kol_metadata_and_related_markets() -> None:
    ch = _FakeClickHouse(
        [
            [
                ("alice", "rally", "great surge", ["cond-1"], _NOW - timedelta(days=1)),
                ("", "skipped", "no author", ["cond-2"], _NOW - timedelta(days=1)),
                ("bob", None, "", ["cond-3"], _NOW - timedelta(days=1)),
                ("carol", "ok", "rally", [], _NOW - timedelta(days=1)),
            ]
        ]
    )

    posts = await load_kol_posts_asof(
        ch,  # type: ignore[arg-type]
        asked_at=_NOW,
        lookback_days=30,
    )

    # Only the alice row has author + text + related_markets.
    assert len(posts) == 1
    assert posts[0].author == "alice"
    assert posts[0].related_markets == ("cond-1",)


@pytest.mark.asyncio
async def test_load_first_resolutions_returns_outcome_map() -> None:
    ch = _FakeClickHouse(
        [
            [
                ("c1", "YES"),
                ("c2", "NO"),
            ]
        ]
    )

    out = await load_first_resolutions(
        ch,  # type: ignore[arg-type]
        condition_ids=["c1", "c2"],
        asked_at=_NOW,
    )

    assert out == {"c1": 1, "c2": 0}


@pytest.mark.asyncio
async def test_load_first_resolutions_short_circuits_on_empty_input() -> None:
    ch = _FakeClickHouse([])
    out = await load_first_resolutions(
        ch,  # type: ignore[arg-type]
        condition_ids=[],
        asked_at=_NOW,
    )
    assert out == {}
    assert ch.calls == []


@pytest.mark.asyncio
async def test_kol_credibility_index_asof_round_trips_to_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts = [
        _post(author="ALICE", text="rally surge", related_markets=("c1",), days_ago=1.0),
        _post(author="alice", text="bullish growth", related_markets=("c2",), days_ago=2.0),
        _post(author="alice", text="strong outperform", related_markets=("c3",), days_ago=3.0),
    ]
    resolutions = {"c1": 1, "c2": 1, "c3": 1}

    async def _fake_posts(ch, *, asked_at, lookback_days):
        return posts

    async def _fake_resolutions(ch, *, condition_ids, asked_at):
        return resolutions

    monkeypatch.setattr(kol_credibility, "load_kol_posts_asof", _fake_posts)
    monkeypatch.setattr(kol_credibility, "load_first_resolutions", _fake_resolutions)

    index = await kol_credibility_index_asof(
        object(),  # type: ignore[arg-type]
        asked_at=_NOW,
        min_samples=3,
    )

    # Each post is from a different case-form of "alice" — but case-folding
    # collapses them onto one author so all 3 contributions land together.
    # That author has 3 correct calls => credibility_weight == weight_hi.
    assert index == {"alice": pytest.approx(DEFAULT_WEIGHT_HI)}


@pytest.mark.asyncio
async def test_run_kol_credibility_reports_author_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts = [
        _post(author="alice", text="rally surge", related_markets=("c1",)),
        _post(author="alice", text="bullish", related_markets=("c2",)),
        _post(author="alice", text="strong", related_markets=("c3",)),
        _post(author="bob", text="bullish", related_markets=("c1",)),
    ]
    resolutions = {"c1": 1, "c2": 1, "c3": 1}

    async def _fake_posts(ch, *, asked_at, lookback_days):
        return posts

    async def _fake_resolutions(ch, *, condition_ids, asked_at):
        return resolutions

    monkeypatch.setattr(kol_credibility, "load_kol_posts_asof", _fake_posts)
    monkeypatch.setattr(kol_credibility, "load_first_resolutions", _fake_resolutions)

    report = await kol_credibility.run_kol_credibility(
        object(),  # type: ignore[arg-type]
        asked_at=_NOW,
        min_samples=3,
        half_life_days=DEFAULT_HALF_LIFE_DAYS,
    )

    assert report.total_authors == 2
    assert report.authors_with_min_samples == 1
    by_author = {row.author: row for row in report.rows}
    assert by_author["alice"].credibility_weight == pytest.approx(DEFAULT_WEIGHT_HI)
    # bob has only 1 sample; below the threshold => neutral weight.
    assert by_author["bob"].credibility_weight == pytest.approx(DEFAULT_NEUTRAL_WEIGHT)
