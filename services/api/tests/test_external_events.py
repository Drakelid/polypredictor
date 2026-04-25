from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api.external_events import (
    freshness_weight,
    list_external_events,
    source_decay_half_life_minutes,
)
from api.settings import Settings


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self.rows)


def _row(
    *,
    event_kind: str = "social",
    source: str = "reddit:CryptoCurrency",
    source_uri: str = "https://www.reddit.com/r/CryptoCurrency/comments/abc123/example/",
    source_id: str = "abc123",
    related_markets: list[str] | None = None,
    author: str = "alice",
    title: str = "Bitcoin ETF chatter spikes",
    body: str = "Retail discussion picks up again.",
    url: str = "https://www.reddit.com/r/CryptoCurrency/comments/abc123/example/",
    metadata: str = '{"score":42}',
    event_time: datetime | None = None,
) -> tuple[object, ...]:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    return (
        event_kind,
        source,
        source_uri,
        source_id,
        related_markets or ["cond-btc"],
        author,
        title,
        body,
        url,
        metadata,
        event_time or observed_at,
        observed_at,
    )


def test_source_decay_half_life_minutes_uses_source_specific_defaults() -> None:
    settings = Settings()

    assert source_decay_half_life_minutes(
        "reddit:CryptoCurrency", "social", settings=settings
    ) == pytest.approx(180.0)
    assert source_decay_half_life_minutes(
        "rss:coindesk", "news", settings=settings
    ) == pytest.approx(360.0)
    assert source_decay_half_life_minutes(
        "macro:fomc", "macro", settings=settings
    ) == pytest.approx(1440.0)


def test_freshness_weight_decays_reddit_faster_than_rss() -> None:
    settings = Settings()
    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    event_time = asked_at - timedelta(hours=4)

    _, _, reddit_weight = freshness_weight(
        source="reddit:CryptoCurrency",
        event_kind="social",
        event_time=event_time,
        asked_at=asked_at,
        settings=settings,
    )
    _, _, rss_weight = freshness_weight(
        source="rss:coindesk",
        event_kind="news",
        event_time=event_time,
        asked_at=asked_at,
        settings=settings,
    )

    assert reddit_weight < rss_weight


@pytest.mark.asyncio
async def test_list_external_events_returns_decay_fields_and_condition_filter() -> None:
    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            _row(
                source="reddit:CryptoCurrency",
                event_time=asked_at - timedelta(hours=2),
            )
        ]
    )

    events = await list_external_events(
        ch,
        asked_at=asked_at,
        lookback_hours=24,
        condition_id="cond-btc",
    )

    assert len(events) == 1
    event = events[0]
    assert event.related_markets == ["cond-btc"]
    assert event.decay_half_life_minutes == pytest.approx(180.0)
    assert 0 < event.freshness_weight < 1
    assert ch.calls[0][1]["cond"] == "cond-btc"


@pytest.mark.asyncio
async def test_list_external_events_hides_scheduled_macro_and_onchain_by_default() -> None:
    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            _row(
                event_kind="macro",
                source="macro:fomc",
                metadata='{"scheduled": true}',
                event_time=asked_at - timedelta(hours=2),
            ),
            _row(
                event_kind="onchain",
                source="onchain:unlocks",
                metadata='{"scheduled": true}',
                event_time=asked_at - timedelta(hours=1),
            ),
            _row(
                event_kind="onchain",
                source="onchain:glassnode",
                metadata='{"scheduled": false}',
                event_time=asked_at - timedelta(minutes=30),
            ),
        ]
    )

    events = await list_external_events(ch, asked_at=asked_at, lookback_hours=24)

    assert len(events) == 1
    assert events[0].source == "onchain:glassnode"


@pytest.mark.asyncio
async def test_list_external_events_can_include_scheduled_items() -> None:
    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            _row(
                event_kind="macro",
                source="macro:fomc",
                metadata='{"scheduled": true}',
                event_time=asked_at - timedelta(hours=2),
            ),
        ]
    )

    events = await list_external_events(
        ch,
        asked_at=asked_at,
        lookback_hours=24,
        include_scheduled=True,
    )

    assert len(events) == 1
    assert events[0].source == "macro:fomc"
