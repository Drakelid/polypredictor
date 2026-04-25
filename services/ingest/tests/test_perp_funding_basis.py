from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.perp_funding_basis import (
    BINANCE_VENUE,
    COINBASE_VENUE,
    run_once,
)
from ingest.writers import PERP_FUNDING_BASIS_COLS, perp_funding_basis_row
from model import BinancePerpSnapshot, CoinbaseIntxPerpSnapshot


class _FakeClickHouse:
    def __init__(self) -> None:
        self.insert_calls: list[tuple[str, list[tuple[object, ...]], tuple[str, ...]]] = []
        self.closed = False

    async def insert(
        self, table: str, rows: list[tuple[object, ...]], column_names: tuple[str, ...]
    ) -> None:
        self.insert_calls.append((table, rows, column_names))

    async def close(self) -> None:
        self.closed = True


class _FakeBinance:
    def __init__(self) -> None:
        self._by_symbol = {
            "BTCUSDT": BinancePerpSnapshot(
                symbol="BTCUSDT",
                index_price=100_000.0,
                mark_price=100_500.0,
                funding_rate=0.0001,
            ),
            "ETHUSDT": BinancePerpSnapshot(
                symbol="ETHUSDT",
                index_price=5_000.0,
                mark_price=4_995.0,
                funding_rate=-0.00005,
            ),
            "SOLUSDT": None,
        }
        self.calls: list[str] = []

    async def premium_index(self, *, symbol: str) -> BinancePerpSnapshot | None:
        self.calls.append(symbol)
        return self._by_symbol.get(symbol)


class _FakeCoinbase:
    def __init__(self) -> None:
        self._by_instrument = {
            "BTC-PERP": CoinbaseIntxPerpSnapshot(
                instrument="BTC-PERP",
                index_price=100_010.0,
                mark_price=100_040.0,
                funding_rate=0.0002,
                event_time=datetime(2026, 4, 24, 12, tzinfo=UTC),
            ),
            "ETH-PERP": None,
            "SOL-PERP": CoinbaseIntxPerpSnapshot(
                instrument="SOL-PERP",
                index_price=150.0,
                mark_price=151.5,
                funding_rate=0.0004,
                event_time=datetime(2026, 4, 24, 12, tzinfo=UTC),
            ),
        }
        self.calls: list[str] = []

    async def snapshot(
        self,
        *,
        instrument: str,
    ) -> CoinbaseIntxPerpSnapshot | None:
        self.calls.append(instrument)
        return self._by_instrument.get(instrument)


def test_perp_funding_basis_row_computes_basis_bps_and_annualized() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    row = perp_funding_basis_row(
        venue=BINANCE_VENUE,
        asset="BTC",
        symbol="BTCUSDT",
        index_price=100_000.0,
        mark_price=100_500.0,
        funding_rate=0.0001,
        observed_at=observed_at,
    )
    by = dict(zip(PERP_FUNDING_BASIS_COLS, row, strict=True))
    assert by["venue"] == BINANCE_VENUE
    assert by["basis_bps"] == pytest.approx(50.0)
    assert by["funding_rate"] == pytest.approx(0.0001)
    assert by["funding_rate_annualized"] == pytest.approx(0.0001 * 3 * 365)


def test_perp_funding_basis_row_handles_zero_index_price_safely() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    row = perp_funding_basis_row(
        venue=BINANCE_VENUE,
        asset="BAD",
        symbol="BADUSDT",
        index_price=0.0,
        mark_price=1.0,
        funding_rate=None,
        observed_at=observed_at,
    )
    by = dict(zip(PERP_FUNDING_BASIS_COLS, row, strict=True))
    assert by["basis_bps"] == pytest.approx(0.0)
    assert by["funding_rate"] is None
    assert by["funding_rate_annualized"] is None


def test_perp_funding_basis_row_coinbase_uses_custom_cycle_count() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    row = perp_funding_basis_row(
        venue=COINBASE_VENUE,
        asset="BTC",
        symbol="BTC-PERP",
        index_price=100_010.0,
        mark_price=100_040.0,
        funding_rate=0.0002,
        funding_cycles_per_day=24.0,
        observed_at=observed_at,
    )
    by = dict(zip(PERP_FUNDING_BASIS_COLS, row, strict=True))
    assert by["venue"] == COINBASE_VENUE
    assert by["basis_bps"] == pytest.approx((100_040.0 - 100_010.0) / 100_010.0 * 10_000.0)
    assert by["funding_rate_annualized"] == pytest.approx(0.0002 * 24 * 365)


@pytest.mark.asyncio
async def test_run_once_writes_rows_for_both_venues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.perp_funding_basis.get_async_client",
        _fake_get_async_client,
    )

    count = await run_once(binance=_FakeBinance(), coinbase=_FakeCoinbase())

    assert count == 4
    assert ch.closed is True
    assert len(ch.insert_calls) == 1
    table, rows, cols = ch.insert_calls[0]
    assert table == "perp_funding_basis"
    assert cols == PERP_FUNDING_BASIS_COLS
    payloads = [dict(zip(cols, row, strict=True)) for row in rows]
    assert {(row["venue"], row["asset"]) for row in payloads} == {
        ("binance", "BTC"),
        ("binance", "ETH"),
        ("coinbase", "BTC"),
        ("coinbase", "SOL"),
    }


@pytest.mark.asyncio
async def test_run_once_does_not_insert_when_no_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.perp_funding_basis.get_async_client",
        _fake_get_async_client,
    )

    class _AllNullBinance:
        async def premium_index(self, *, symbol: str) -> BinancePerpSnapshot | None:
            return None

    class _AllNullCoinbase:
        async def snapshot(
            self,
            *,
            instrument: str,
        ) -> CoinbaseIntxPerpSnapshot | None:
            return None

    count = await run_once(
        binance=_AllNullBinance(),
        coinbase=_AllNullCoinbase(),
    )
    assert count == 0
    assert ch.closed is True
    assert ch.insert_calls == []
