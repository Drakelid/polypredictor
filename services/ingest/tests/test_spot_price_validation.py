from __future__ import annotations

import pytest
from ingest.workers.spot_price_validation import run_once
from ingest.writers import VALIDATED_SPOT_PRICE_COLS
from model import SpotTickerSnapshot


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
    async def book_ticker(self, *, symbol: str) -> SpotTickerSnapshot | None:
        return SpotTickerSnapshot(venue="binance", symbol=symbol, price=100000.0)


class _FakeCoinbase:
    async def product_ticker(self, *, product_id: str) -> SpotTickerSnapshot | None:
        return SpotTickerSnapshot(venue="coinbase", symbol=product_id, price=100010.0)


class _FakeCoinGecko:
    async def simple_price(
        self,
        *,
        coin_id: str,
        vs_currency: str = "usd",
    ) -> SpotTickerSnapshot | None:
        return SpotTickerSnapshot(venue="coingecko", symbol=coin_id, price=103000.0)


@pytest.mark.asyncio
async def test_run_once_writes_validated_spot_price_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    class _FakeStack:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def enter_async_context(self, value):
            return value

    monkeypatch.setattr(
        "ingest.workers.spot_price_validation.get_async_client",
        _fake_get_async_client,
    )
    monkeypatch.setattr(
        "ingest.workers.spot_price_validation.AsyncExitStack",
        _FakeStack,
    )
    monkeypatch.setattr(
        "ingest.workers.spot_price_validation.BinanceSpotClient",
        lambda base_url: _FakeBinance(),
    )
    monkeypatch.setattr(
        "ingest.workers.spot_price_validation.CoinbaseSpotClient",
        lambda base_url: _FakeCoinbase(),
    )
    monkeypatch.setattr(
        "ingest.workers.spot_price_validation.CoinGeckoSpotClient",
        lambda base_url: _FakeCoinGecko(),
    )

    count = await run_once()

    assert count == 3
    assert ch.closed is True
    assert len(ch.insert_calls) == 1
    table, rows, cols = ch.insert_calls[0]
    assert table == "validated_spot_prices"
    assert cols == VALIDATED_SPOT_PRICE_COLS
    first = dict(zip(cols, rows[0], strict=True))
    assert first["reference_price"] == pytest.approx(100005.0)
    assert first["accepted_venues"] == ["binance", "coinbase"]
    assert first["rejected_venues"] == ["coingecko"]
    assert first["is_valid"] == 1
