from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.markets_registry import MarketRef
from ingest.workers.trades_sweeper import (
    TradeCursor,
    _advance_cursor,
    _new_trades_since,
    _rows_for_trades,
    load_cursor,
    sweep_one_market,
)
from polymarket_client import Trade


def _trade(
    trade_id: str,
    *,
    ts: datetime,
    token_id: str = "tok-1",
    market: str = "cond-1",
    side: str = "BUY",
    price: float = 0.51,
    size: float = 10.0,
) -> Trade:
    return Trade(
        trade_id=trade_id,
        token_id=token_id,
        market=market,
        price=price,
        size=size,
        side=side,
        timestamp=ts,
    )


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.rows = rows or []
        self.insert_calls: list[tuple[str, list[tuple[object, ...]], list[str]]] = []

    async def query(self, query: str, parameters: dict[str, object] | None = None) -> _QueryResult:
        return _QueryResult(self.rows)

    async def insert(
        self, table: str, rows: list[tuple[object, ...]], column_names: list[str]
    ) -> None:
        self.insert_calls.append((table, rows, column_names))


class _FakeClob:
    def __init__(self, trades: list[Trade]) -> None:
        self.trades_to_return = trades
        self.calls: list[tuple[str, int]] = []

    async def trades(
        self,
        *,
        market: str | None = None,
        token_id: str | None = None,
        limit: int = 100,
    ) -> list[Trade]:
        assert token_id is None
        self.calls.append((str(market), limit))
        return list(self.trades_to_return)


class _FakePm:
    def __init__(self, trades: list[Trade]) -> None:
        self.clob = _FakeClob(trades)


def test_new_trades_since_filters_older_duplicates_and_keeps_same_timestamp_new_ids() -> None:
    observed_at = datetime(2026, 4, 23, tzinfo=UTC)
    t1 = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 23, 10, 1, tzinfo=UTC)
    cursor = TradeCursor(last_event_time=t2, trade_ids_at_last_time={"b"})

    trades = [
        _trade("a", ts=t1),
        _trade("b", ts=t2),
        _trade("c", ts=t2),
        _trade("d", ts=datetime(2026, 4, 23, 10, 2, tzinfo=UTC)),
    ]

    fresh = _new_trades_since(trades, cursor=cursor, observed_at=observed_at)

    assert [trade.trade_id for trade in fresh] == ["c", "d"]


def test_advance_cursor_tracks_all_trade_ids_at_latest_timestamp() -> None:
    observed_at = datetime(2026, 4, 23, tzinfo=UTC)
    latest = datetime(2026, 4, 23, 10, 1, tzinfo=UTC)
    cursor = TradeCursor(last_event_time=latest, trade_ids_at_last_time={"b"})

    _advance_cursor(
        cursor,
        trades=[
            _trade("b", ts=latest),
            _trade("c", ts=latest),
            _trade("d", ts=datetime(2026, 4, 23, 10, 2, tzinfo=UTC)),
        ],
        observed_at=observed_at,
    )

    assert cursor.last_event_time == datetime(2026, 4, 23, 10, 2, tzinfo=UTC)
    assert cursor.trade_ids_at_last_time == {"d"}


def test_rows_for_trades_falls_back_to_market_ref_condition_id() -> None:
    observed_at = datetime(2026, 4, 23, tzinfo=UTC)
    ref = MarketRef(
        condition_id="cond-1",
        token_ids=["tok-1"],
        volume_usdc=100.0,
        liquidity_usdc=10.0,
        active=True,
        closed=False,
    )

    rows = _rows_for_trades(
        ref,
        [Trade(trade_id="x", token_id="tok-1", price=0.5, size=1.0, side="BUY", market=None)],
        observed_at=observed_at,
    )

    assert rows[0][2] == "cond-1"


@pytest.mark.asyncio
async def test_load_cursor_uses_latest_timestamp_group() -> None:
    latest = datetime(2026, 4, 23, 10, 2, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            ("b", latest),
            ("a", latest),
            ("older", datetime(2026, 4, 23, 10, 1, tzinfo=UTC)),
        ]
    )

    cursor = await load_cursor(ch, "cond-1")

    assert cursor.last_event_time == latest
    assert cursor.trade_ids_at_last_time == {"a", "b"}


@pytest.mark.asyncio
async def test_sweep_one_market_inserts_only_fresh_rows() -> None:
    observed_latest = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)
    ref = MarketRef(
        condition_id="cond-1",
        token_ids=["tok-1"],
        volume_usdc=100.0,
        liquidity_usdc=10.0,
        active=True,
        closed=False,
    )
    pm = _FakePm(
        [
            _trade("old", ts=observed_latest),
            _trade("new", ts=observed_latest),
        ]
    )
    ch = _FakeClickHouse()
    cursor = TradeCursor(last_event_time=observed_latest, trade_ids_at_last_time={"old"})

    inserted = await sweep_one_market(pm, ch, ref, cursor=cursor, limit=200)

    assert inserted == 1
    assert pm.clob.calls == [("cond-1", 200)]
    assert len(ch.insert_calls) == 1
    assert ch.insert_calls[0][0] == "trades_raw"
    assert len(ch.insert_calls[0][1]) == 1
