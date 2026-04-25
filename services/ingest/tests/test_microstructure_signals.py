from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ingest.workers.microstructure_signals import _large_print_event_id, build_signal_rows, run_once
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


def _results(existing_ids: list[str] | None = None) -> list[list[tuple[object, ...]]]:
    current_quote_at = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    prior_quote_at = current_quote_at - timedelta(minutes=15)
    trade_at = current_quote_at - timedelta(minutes=1)
    return [
        [("cond-1", ["tok-yes"], 1, 0, 0)],
        [("trade-big", "tok-yes", "cond-1", 0.65, 10_000.0, "buy", trade_at, trade_at)],
        [("tok-yes", "cond-1", 0.64, 0.66, current_quote_at)],
        [("tok-yes", "cond-1", 0.48, 0.52, prior_quote_at)],
        [
            ("tok-yes", "bid", 0.64, 900.0),
            ("tok-yes", "ask", 0.66, 100.0),
        ],
        [
            ("tok-yes", "bid", 0.48, 100.0),
            ("tok-yes", "ask", 0.52, 900.0),
        ],
        [(event_id,) for event_id in (existing_ids or [])],
    ]


@pytest.mark.asyncio
async def test_build_signal_rows_emits_large_print_and_book_shock() -> None:
    observed_at = datetime(2026, 4, 23, 12, 5, tzinfo=UTC)
    ch = _FakeClickHouse(_results())

    rows = await build_signal_rows(
        ch,
        observed_at=observed_at,
        batch_size=10,
        large_print_min_notional_usdc=5_000.0,
        book_shock_lookback=timedelta(minutes=15),
        book_shock_min_delta=0.40,
    )

    payloads = [dict(zip(SIGNAL_EVENTS_COLS, row, strict=True)) for row in rows]
    event_types = {payload["event_type"] for payload in payloads}
    assert event_types == {"large_print", "book_shock"}
    large_print = next(payload for payload in payloads if payload["event_type"] == "large_print")
    book_shock = next(payload for payload in payloads if payload["event_type"] == "book_shock")
    assert large_print["direction"] == "yes"
    assert large_print["size_delta_usdc"] == pytest.approx(6_500.0)
    assert book_shock["direction"] == "yes"
    assert book_shock["severity"] > 1.0


@pytest.mark.asyncio
async def test_build_signal_rows_skips_existing_large_print_event() -> None:
    observed_at = datetime(2026, 4, 23, 12, 5, tzinfo=UTC)
    ch = _FakeClickHouse(_results(existing_ids=[_large_print_event_id("trade-big")]))

    rows = await build_signal_rows(
        ch,
        observed_at=observed_at,
        batch_size=10,
        large_print_min_notional_usdc=5_000.0,
        book_shock_lookback=timedelta(minutes=15),
        book_shock_min_delta=0.40,
    )

    payloads = [dict(zip(SIGNAL_EVENTS_COLS, row, strict=True)) for row in rows]
    assert [payload["event_type"] for payload in payloads] == ["book_shock"]


@pytest.mark.asyncio
async def test_run_once_writes_microstructure_signal_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse(_results())

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.microstructure_signals.get_async_client",
        _fake_get_async_client,
    )

    count = await run_once()

    assert count == 2
    assert ch.closed is True
    assert len(ch.insert_calls) == 1
    table, rows, cols = ch.insert_calls[0]
    assert table == "signal_events"
    assert cols == SIGNAL_EVENTS_COLS
    assert {dict(zip(cols, row, strict=True))["event_type"] for row in rows} == {
        "large_print",
        "book_shock",
    }
