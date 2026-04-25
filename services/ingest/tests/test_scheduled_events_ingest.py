from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.scheduled_events_ingest import (
    ScheduledEventSpec,
    build_external_event_rows,
    load_scheduled_event_specs,
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


def test_load_scheduled_event_specs_reads_json_file(tmp_path) -> None:
    path = tmp_path / "scheduled_events.json"
    path.write_text(
        """
        [
          {
            "event_kind": "macro",
            "source": "macro:fomc",
            "source_id": "fomc-2026-06-17",
            "title": "FOMC rate decision",
            "body": "Fed decision",
            "event_time": "2026-06-17T18:00:00Z"
          }
        ]
        """,
        encoding="utf-8",
    )

    specs = load_scheduled_event_specs(str(path))

    assert len(specs) == 1
    assert specs[0].source == "macro:fomc"
    assert specs[0].source_id == "fomc-2026-06-17"


def test_load_scheduled_event_specs_parses_consensus_and_actual_values(tmp_path) -> None:
    path = tmp_path / "scheduled_events.json"
    path.write_text(
        """
        [
          {
            "event_kind": "macro",
            "source": "macro:cpi",
            "source_id": "cpi-2026-05-13",
            "title": "US CPI release",
            "body": "BLS CPI release",
            "event_time": "2026-05-13T12:30:00Z",
            "consensus_value": 2.8,
            "actual_value": 3.1,
            "value_unit": "pct_yoy"
          }
        ]
        """,
        encoding="utf-8",
    )

    specs = load_scheduled_event_specs(str(path))

    assert len(specs) == 1
    assert specs[0].consensus_value == pytest.approx(2.8)
    assert specs[0].actual_value == pytest.approx(3.1)
    assert specs[0].value_unit == "pct_yoy"


@pytest.mark.asyncio
async def test_build_external_event_rows_matches_related_markets_and_dedupes() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [
                ("cond-fomc", "Will the Fed cut 25bps at the June FOMC?", "fed-cut-june", ["fed", "fomc"], 1, 0, 0),
                ("cond-cpi", "Will CPI print below 2.8% in June?", "cpi-june", ["cpi", "inflation"], 1, 0, 0),
            ],
            [("macro:fomc", "existing-fomc")],
        ]
    )
    specs = [
        ScheduledEventSpec(
            event_kind="macro",
            source="macro:fomc",
            source_id="new-fomc",
            title="FOMC rate decision",
            body="Fed rate decision",
            url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            event_time=observed_at,
            related_markets=[],
            match_terms=["fomc", "fed", "25bps"],
        ),
        ScheduledEventSpec(
            event_kind="macro",
            source="macro:fomc",
            source_id="existing-fomc",
            title="Old FOMC rate decision",
            body="already seen",
            url=None,
            event_time=observed_at,
            related_markets=[],
            match_terms=["fomc", "fed"],
        ),
    ]

    rows = await build_external_event_rows(
        ch,
        observed_at=observed_at,
        specs=specs,
        max_markets=100,
        max_related_markets=3,
    )

    assert len(rows) == 1
    payload = dict(zip(EXTERNAL_EVENTS_COLS, rows[0], strict=True))
    assert payload["source"] == "macro:fomc"
    assert payload["source_id"] == "new-fomc"
    assert payload["related_markets"] == ["cond-fomc"]
