from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.external_divergence import run_once
from ingest.writers import SIGNAL_EVENTS_COLS
from model import BinancePerpSnapshot, DeribitIV


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


class _FakeDeribit:
    async def fetch_iv(
        self,
        *,
        currency: str,
        strike: float,
        direction: str,
        horizon_days: float,
    ) -> DeribitIV | None:
        assert currency == "BTC"
        assert strike == pytest.approx(150_000.0)
        assert direction == "above"
        assert horizon_days > 0.0
        return DeribitIV(
            index_price=100_000.0,
            expiry_days=90.0,
            realized_vol=0.45,
            atm_iv=0.60,
            strike_iv=0.75,
            atm_instrument="BTC-ATM",
            strike_instrument="BTC-150K",
        )


class _FakeBinance:
    async def premium_index(self, *, symbol: str) -> BinancePerpSnapshot | None:
        assert symbol == "BTCUSDT"
        return BinancePerpSnapshot(
            symbol="BTCUSDT",
            index_price=100_000.0,
            mark_price=101_500.0,
            funding_rate=0.0001,
        )


@pytest.mark.asyncio
async def test_run_once_writes_external_divergence_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    future = datetime(2026, 7, 1, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [("cond-1", "Will BTC exceed $150k by July?", ["tok-yes"], 1, 0, 0)],
            [("cond-1", "threshold", "BTC", 150_000.0, "above", future)],
            [("tok-yes", 0.85)],
        ]
    )

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.external_divergence.get_async_client",
        _fake_get_async_client,
    )

    count = await run_once(deribit=_FakeDeribit(), binance=_FakeBinance())

    assert count == 1
    assert ch.closed is True
    assert len(ch.insert_calls) == 1
    table, rows, cols = ch.insert_calls[0]
    assert table == "signal_events"
    assert cols == SIGNAL_EVENTS_COLS
    payload = dict(zip(cols, rows[0], strict=True))
    assert payload["event_type"] == "external_divergence"
    assert payload["condition_id"] == "cond-1"
