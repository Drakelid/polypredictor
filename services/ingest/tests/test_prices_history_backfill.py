from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.prices_history_backfill import (
    HistoryBackfillTarget,
    backfill_target,
    select_missing_history_targets,
)
from polymarket_client import PriceHistoryBucket


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(
        self,
        *,
        latest_rows: list[tuple[object, ...]] | None = None,
        existing_rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.latest_rows = latest_rows or []
        self.existing_rows = existing_rows or []
        self.insert_calls: list[tuple[str, list[tuple[object, ...]], list[str]]] = []

    async def query(self, query: str, parameters: dict[str, object] | None = None) -> _QueryResult:
        if "FROM markets_snapshots" in query:
            return _QueryResult(self.latest_rows)
        if "FROM prices_history" in query:
            return _QueryResult(self.existing_rows)
        raise AssertionError(f"unexpected query: {query}")

    async def insert(
        self, table: str, rows: list[tuple[object, ...]], column_names: list[str]
    ) -> None:
        self.insert_calls.append((table, rows, column_names))


class _FakeClob:
    def __init__(self, history: list[PriceHistoryBucket]) -> None:
        self.history = history
        self.calls: list[tuple[str, str, int]] = []

    async def prices_history(
        self, token_id: str, *, interval: str = "1h", fidelity: int = 60
    ) -> list[PriceHistoryBucket]:
        self.calls.append((token_id, interval, fidelity))
        return self.history


class _FakePm:
    def __init__(self, history: list[PriceHistoryBucket]) -> None:
        self.clob = _FakeClob(history)


@pytest.mark.asyncio
async def test_select_missing_history_targets_prioritizes_volume_and_missing_pairs() -> None:
    ch = _FakeClickHouse(
        latest_rows=[
            ("cond-high", ["tok-a", "tok-b"], 1000.0, 1, 0, 0),
            ("cond-low", ["tok-c"], 100.0, 1, 0, 0),
            ("cond-closed", ["tok-z"], 5000.0, 1, 1, 0),
        ],
        existing_rows=[
            ("tok-a", "1h"),
            ("tok-a", "6h"),
            ("tok-a", "1d"),
            ("tok-b", "1h"),
        ],
    )

    targets = await select_missing_history_targets(ch, intervals=("1h", "6h", "1d"), limit=4)

    assert targets == [
        HistoryBackfillTarget("cond-high", "tok-b", "6h"),
        HistoryBackfillTarget("cond-high", "tok-b", "1d"),
        HistoryBackfillTarget("cond-low", "tok-c", "1h"),
        HistoryBackfillTarget("cond-low", "tok-c", "6h"),
    ]


@pytest.mark.asyncio
async def test_backfill_target_fetches_history_and_inserts_rows() -> None:
    ch = _FakeClickHouse()
    pm = _FakePm(
        [
            PriceHistoryBucket(t=int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()), p=0.41),
            PriceHistoryBucket(t=int(datetime(2026, 1, 1, 1, tzinfo=UTC).timestamp()), p=0.43),
        ]
    )

    inserted = await backfill_target(
        pm,
        ch,
        HistoryBackfillTarget("cond-1", "tok-1", "1h"),
        fidelity=60,
    )

    assert inserted == 2
    assert pm.clob.calls == [("tok-1", "1h", 60)]
    assert len(ch.insert_calls) == 1
    table, rows, columns = ch.insert_calls[0]
    assert table == "prices_history"
    assert columns[0] == "token_id"
    assert len(rows) == 2
