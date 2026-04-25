from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.arb_checker import run_once
from ingest.writers import SIGNAL_EVENTS_COLS


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, results: list[list[tuple[object, ...]]]) -> None:
        self.results = results
        self.insert_calls: list[tuple[str, list[tuple[object, ...]], list[str]]] = []
        self.closed = False

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        return _QueryResult(self.results.pop(0))

    async def insert(
        self, table: str, rows: list[tuple[object, ...]], column_names: list[str]
    ) -> None:
        self.insert_calls.append((table, rows, column_names))

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_run_once_writes_threshold_and_multi_outcome_arb_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [
                ("cond-loose", "", "BTC > 140k by June?", ["tok-loose"], 1, 0, 0),
                ("cond-strict", "", "BTC > 150k by June?", ["tok-strict"], 1, 0, 0),
                ("leg-a", "evt-1", "A", ["tok-a"], 1, 0, 0),
                ("leg-b", "evt-1", "B", ["tok-b"], 1, 0, 0),
                ("leg-c", "evt-1", "C", ["tok-c"], 1, 0, 0),
            ],
            [
                ("cond-loose", "threshold", "BTC", 140_000.0, "above", now),
                ("cond-strict", "threshold", "BTC", 150_000.0, "above", now),
                ("leg-a", "multi_outcome", None, None, None, None),
                ("leg-b", "multi_outcome", None, None, None, None),
                ("leg-c", "multi_outcome", None, None, None, None),
            ],
            [
                ("tok-loose", 0.52, 0.51, 0.53, 100.0, 100.0),
                ("tok-strict", 0.60, 0.60, 0.61, 120.0, 120.0),
                ("tok-a", 0.20, 0.18, 0.20, 80.0, 80.0),
                ("tok-b", 0.30, 0.28, 0.30, 90.0, 90.0),
                ("tok-c", 0.10, 0.09, 0.10, 70.0, 70.0),
            ],
        ]
    )

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr("ingest.workers.arb_checker.get_async_client", _fake_get_async_client)

    count = await run_once()

    assert count == 2
    assert ch.closed is True
    assert len(ch.insert_calls) == 1
    table, rows, cols = ch.insert_calls[0]
    assert table == "signal_events"
    assert cols == SIGNAL_EVENTS_COLS
    assert len(rows) == 2
    by_condition = {
        dict(zip(cols, row, strict=True))["condition_id"]: dict(zip(cols, row, strict=True))
        for row in rows
    }
    assert by_condition["cond-strict"]["event_type"] == "arb"
    multi_leg = next(
        condition_id
        for condition_id in by_condition
        if condition_id.startswith("leg-")
    )
    assert by_condition[multi_leg]["event_type"] == "arb"
