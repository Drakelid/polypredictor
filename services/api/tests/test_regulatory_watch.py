from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api import regulatory_watch
from api.external_events import ExternalEvent


def _event(*, source: str, title: str, body: str = "") -> ExternalEvent:
    when = datetime(2026, 4, 25, 12, tzinfo=UTC)
    return ExternalEvent(
        event_kind="news",
        source=source,
        source_uri=f"https://example.com/{source}",
        source_id=f"id-{source}-{title[:8]}",
        related_markets=[],
        author="reporter",
        title=title,
        body=body,
        url=f"https://example.com/{source}/article",
        metadata={},
        event_time=when,
        observed_at=when,
        age_seconds=0.0,
        decay_half_life_minutes=360.0,
        freshness_weight=1.0,
    )


@pytest.mark.asyncio
async def test_regulatory_events_filters_by_default_keywords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        _event(source="rss:bloomberg", title="CFTC opens probe into prediction market"),
        _event(source="rss:coindesk", title="Bitcoin rallies past $200k"),
        _event(
            source="rss:reuters",
            title="SEC clarifies regulation around event contracts",
        ),
    ]

    async def _fake_list(
        ch: object,
        *,
        asked_at: datetime,
        lookback_hours: int,
        limit: int,
        event_kinds: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> list[ExternalEvent]:
        assert event_kinds == ["news", "social"]
        return events

    monkeypatch.setattr(regulatory_watch, "list_external_events", _fake_list)

    matches = await regulatory_watch.regulatory_events_asof(
        ch=object(),
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
        lookback_hours=720,
    )
    titles = [match.event.title for match in matches]
    assert any("CFTC" in title for title in titles)
    assert any("SEC" in title for title in titles)
    assert all("Bitcoin rallies" not in title for title in titles)
    # Each match exposes the actual keyword(s) that fired so the UI can
    # highlight the substring.
    cftc_match = next(m for m in matches if "CFTC" in m.event.title)
    assert "CFTC" in cftc_match.matched_keywords
    assert "prediction market" in cftc_match.matched_keywords


@pytest.mark.asyncio
async def test_regulatory_events_honors_custom_keywords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        _event(source="rss:apnews", title="EU MiCA framework finalized"),
        _event(source="rss:bloomberg", title="CFTC drops case"),  # default kw, NOT custom
    ]

    async def _fake_list(
        ch: object,
        *,
        asked_at: datetime,
        lookback_hours: int,
        limit: int,
        event_kinds: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> list[ExternalEvent]:
        return events

    monkeypatch.setattr(regulatory_watch, "list_external_events", _fake_list)

    matches = await regulatory_watch.regulatory_events_asof(
        ch=object(),
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
        lookback_hours=720,
        keywords=["MiCA"],
    )
    assert len(matches) == 1
    assert matches[0].event.title == "EU MiCA framework finalized"


@pytest.mark.asyncio
async def test_regulatory_events_empty_keywords_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [_event(source="rss:apnews", title="CFTC announces new rule")]

    async def _fake_list(
        ch: object,
        *,
        asked_at: datetime,
        lookback_hours: int,
        limit: int,
        event_kinds: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> list[ExternalEvent]:
        return events

    monkeypatch.setattr(regulatory_watch, "list_external_events", _fake_list)

    matches = await regulatory_watch.regulatory_events_asof(
        ch=object(),
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
        lookback_hours=720,
        keywords=[],  # empty -> defaults
    )
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_regulatory_events_rejects_non_positive_lookback() -> None:
    with pytest.raises(ValueError):
        await regulatory_watch.regulatory_events_asof(
            ch=object(),
            asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
            lookback_hours=0,
        )


@pytest.mark.asyncio
async def test_regulatory_events_returns_empty_when_feed_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_list(
        ch: object,
        *,
        asked_at: datetime,
        lookback_hours: int,
        limit: int,
        event_kinds: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> list[ExternalEvent]:
        return []

    monkeypatch.setattr(regulatory_watch, "list_external_events", _fake_list)
    matches = await regulatory_watch.regulatory_events_asof(
        ch=object(),
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert matches == []
