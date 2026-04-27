from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.rss_ingest import (
    FeedEntry,
    FeedSpec,
    build_external_event_rows,
    load_feed_specs,
    parse_feed_document,
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


def test_load_feed_specs_reads_json_file(tmp_path) -> None:
    path = tmp_path / "feeds.json"
    path.write_text(
        '[{"source":"rss:test","url":"https://example.com/rss.xml","event_kind":"news"}]',
        encoding="utf-8",
    )

    specs = load_feed_specs(str(path))

    assert specs == [
        FeedSpec(source="rss:test", url="https://example.com/rss.xml", event_kind="news")
    ]


def test_parse_feed_document_supports_rss_items() -> None:
    xml = """<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Bitcoin ETF inflows rise</title>
          <description>BTC demand increased after ETF news.</description>
          <link>https://example.com/story-1</link>
          <guid>story-1</guid>
          <pubDate>Fri, 24 Apr 2026 10:15:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """

    entries = parse_feed_document(
        xml,
        source="rss:test",
        source_uri="https://example.com/rss.xml",
        event_kind="news",
    )

    assert len(entries) == 1
    assert entries[0].source_id == "story-1"
    assert entries[0].title == "Bitcoin ETF inflows rise"
    assert entries[0].url == "https://example.com/story-1"


@pytest.mark.asyncio
async def test_build_external_event_rows_matches_related_markets_and_dedupes() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [
                ("cond-btc", "Will Bitcoin ETF inflows rise?", "btc-etf-inflows", ["btc", "etf"], 1, 0, 0),
                ("cond-fed", "Will the Fed cut in June?", "fed-cut-june", ["fed", "rates"], 1, 0, 0),
            ],
            [("rss:test", "existing-story")],
        ]
    )

    async def _fake_fetcher(spec: FeedSpec) -> list[FeedEntry]:
        return [
            FeedEntry(
                source=spec.source,
                source_uri=spec.url,
                source_id="new-story",
                event_kind="news",
                author="Reporter",
                title="Bitcoin ETF inflows rise as demand returns",
                body="BTC funds saw fresh ETF demand across major products.",
                url="https://example.com/new-story",
                event_time=observed_at,
                metadata={"feed": spec.url},
            ),
            FeedEntry(
                source=spec.source,
                source_uri=spec.url,
                source_id="existing-story",
                event_kind="news",
                author="Reporter",
                title="Old story",
                body="Already seen",
                url="https://example.com/old-story",
                event_time=observed_at,
                metadata={},
            ),
        ]

    rows = await build_external_event_rows(
        ch,
        observed_at=observed_at,
        specs=[FeedSpec(source="rss:test", url="https://example.com/rss.xml")],
        timeout_s=5.0,
        max_markets=100,
        max_related_markets=3,
        fetcher=_fake_fetcher,
    )

    assert len(rows) == 1
    payload = dict(zip(EXTERNAL_EVENTS_COLS, rows[0], strict=True))
    assert payload["source"] == "rss:test"
    assert payload["source_id"] == "new-story"
    assert payload["related_markets"] == ["cond-btc"]


def test_load_feed_specs_parses_scheduled_flag(tmp_path) -> None:
    path = tmp_path / "feeds.json"
    path.write_text(
        """[
            {"source": "rss:fed", "url": "https://example.com/fed.xml", "event_kind": "macro", "scheduled": true},
            {"source": "rss:news", "url": "https://example.com/news.xml"}
        ]""",
        encoding="utf-8",
    )

    specs = load_feed_specs(str(path))

    assert specs[0].source == "rss:fed"
    assert specs[0].scheduled is True
    assert specs[0].event_kind == "macro"
    # Default is scheduled=False when the field is absent.
    assert specs[1].source == "rss:news"
    assert specs[1].scheduled is False


@pytest.mark.asyncio
async def test_build_external_event_rows_promotes_scheduled_flag_to_metadata() -> None:
    """A FeedSpec with scheduled=True must surface metadata.scheduled=True
    on every row from that feed so api.event_time picks them up as catalysts."""
    import json as json_module

    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [],  # latest_active_markets
            [],  # existing_source_ids -> nothing seen yet
        ]
    )

    async def _fake_fetcher(spec: FeedSpec) -> list[FeedEntry]:
        return [
            FeedEntry(
                source=spec.source,
                source_uri=spec.url,
                source_id="fomc-2026-04-30",
                event_kind=spec.event_kind,
                author="Federal Reserve",
                title="FOMC statement, April 30, 2026",
                body="Federal Reserve issues FOMC statement",
                url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260430a.htm",
                event_time=observed_at,
                metadata={},
            )
        ]

    rows = await build_external_event_rows(
        ch,
        observed_at=observed_at,
        specs=[
            FeedSpec(
                source="rss:fed_press_monetary",
                url="https://www.federalreserve.gov/feeds/press_monetary.xml",
                event_kind="macro",
                scheduled=True,
            )
        ],
        timeout_s=5.0,
        max_markets=10,
        max_related_markets=1,
        fetcher=_fake_fetcher,
    )

    assert len(rows) == 1
    payload = dict(zip(EXTERNAL_EVENTS_COLS, rows[0], strict=True))
    metadata = json_module.loads(str(payload["metadata"]))
    assert metadata["scheduled"] is True
    assert payload["event_kind"] == "macro"


@pytest.mark.asyncio
async def test_build_external_event_rows_does_not_force_scheduled_when_disabled() -> None:
    """A FeedSpec with scheduled=False must NOT inject metadata.scheduled."""
    import json as json_module

    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse([[], []])

    async def _fake_fetcher(spec: FeedSpec) -> list[FeedEntry]:
        return [
            FeedEntry(
                source=spec.source,
                source_uri=spec.url,
                source_id="news-1",
                event_kind="news",
                author=None,
                title="Bitcoin rallies",
                body="Demand surged",
                url="https://example.com/news-1",
                event_time=observed_at,
                metadata={"feed": "coindesk"},
            )
        ]

    rows = await build_external_event_rows(
        ch,
        observed_at=observed_at,
        specs=[FeedSpec(source="rss:coindesk", url="https://example.com/rss.xml")],
        timeout_s=5.0,
        max_markets=10,
        max_related_markets=1,
        fetcher=_fake_fetcher,
    )

    assert len(rows) == 1
    payload = dict(zip(EXTERNAL_EVENTS_COLS, rows[0], strict=True))
    metadata = json_module.loads(str(payload["metadata"]))
    assert "scheduled" not in metadata
    assert metadata["feed"] == "coindesk"
