from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from model import (
    BinanceKlinesClient,
    BinancePerpClient,
    BinancePerpSnapshot,
    BinanceSpotClient,
    CoinbaseIntxPerpClient,
    CoinbaseSpotClient,
    CoinGeckoSpotClient,
    DeribitIV,
    SpotTickerSnapshot,
    options_implied_probability,
    perp_basis_implied_probability,
    validate_spot_quotes,
)


@pytest.mark.asyncio
@respx.mock
async def test_binance_perp_client_caches_premium_index() -> None:
    route = respx.get("https://fapi.binance.com/fapi/v1/premiumIndex").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "BTCUSDT",
                "markPrice": "101000",
                "indexPrice": "100000",
                "lastFundingRate": "0.0001",
            },
        )
    )

    async with BinancePerpClient(cache_ttl_s=60.0) as client:
        first = await client.premium_index(symbol="BTCUSDT")
        second = await client.premium_index(symbol="BTCUSDT")

    assert first is not None
    assert second is not None
    assert first.mark_price == pytest.approx(101_000.0)
    assert second.index_price == pytest.approx(100_000.0)
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_binance_spot_client_reads_book_ticker_mid() -> None:
    respx.get("https://api.binance.com/api/v3/ticker/bookTicker").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "BTCUSDT",
                "bidPrice": "100000.0",
                "askPrice": "100010.0",
            },
        )
    )

    async with BinanceSpotClient(cache_ttl_s=60.0) as client:
        snapshot = await client.book_ticker(symbol="BTCUSDT")

    assert snapshot is not None
    assert snapshot.venue == "binance"
    assert snapshot.price == pytest.approx(100005.0)


@pytest.mark.asyncio
@respx.mock
async def test_coinbase_spot_client_reads_public_ticker() -> None:
    respx.get("https://api.exchange.coinbase.com/products/BTC-USD/ticker").mock(
        return_value=httpx.Response(
            200,
            json={
                "price": "100100.0",
                "bid": "100095.0",
                "ask": "100105.0",
                "time": "2026-04-24T12:00:00Z",
            },
        )
    )

    async with CoinbaseSpotClient(cache_ttl_s=60.0) as client:
        snapshot = await client.product_ticker(product_id="BTC-USD")

    assert snapshot is not None
    assert snapshot.venue == "coinbase"
    assert snapshot.price == pytest.approx(100100.0)
    assert snapshot.bid == pytest.approx(100095.0)


@pytest.mark.asyncio
@respx.mock
async def test_coinbase_intx_perp_client_reads_quote_and_funding() -> None:
    respx.get(
        "https://api.international.coinbase.com/api/v1/instruments/BTC-PERP/quote"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "index_price": "100010.0",
                "mark_price": "100040.0",
                "predicted_funding": "0.0002",
                "timestamp": "2026-04-24T12:00:00Z",
            },
        )
    )
    respx.get(
        "https://api.international.coinbase.com/api/v1/instruments/BTC-PERP/funding"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "instrument_id": "BTC-PERP",
                "funding_rate": "0.0003",
                "mark_price": "100040.0",
                "event_time": "2026-04-24T12:00:00Z",
            },
        )
    )

    async with CoinbaseIntxPerpClient(cache_ttl_s=60.0) as client:
        snapshot = await client.snapshot(instrument="btc-perp")

    assert snapshot is not None
    assert snapshot.instrument == "BTC-PERP"
    assert snapshot.index_price == pytest.approx(100010.0)
    assert snapshot.mark_price == pytest.approx(100040.0)
    assert snapshot.funding_rate == pytest.approx(0.0003)


@pytest.mark.asyncio
@respx.mock
async def test_coingecko_spot_client_reads_simple_price() -> None:
    respx.get("https://api.coingecko.com/api/v3/simple/price").mock(
        return_value=httpx.Response(
            200,
            json={"bitcoin": {"usd": 99990.0, "last_updated_at": 1777032000}},
        )
    )

    async with CoinGeckoSpotClient(cache_ttl_s=60.0) as client:
        snapshot = await client.simple_price(coin_id="bitcoin")

    assert snapshot is not None
    assert snapshot.venue == "coingecko"
    assert snapshot.price == pytest.approx(99990.0)


def test_validate_spot_quotes_rejects_outlier_venue() -> None:
    result = validate_spot_quotes(
        [
            SpotTickerSnapshot(venue="binance", symbol="BTCUSDT", price=100000.0),
            SpotTickerSnapshot(venue="coinbase", symbol="BTC-USD", price=100020.0),
            SpotTickerSnapshot(venue="coingecko", symbol="bitcoin", price=103000.0),
        ],
        max_deviation_bps=50.0,
        min_venues=2,
    )

    assert result.is_valid is True
    assert result.reference_price == pytest.approx(100010.0)
    assert [quote.venue for quote in result.accepted_quotes] == ["binance", "coinbase"]
    assert [quote.venue for quote in result.rejected_quotes] == ["coingecko"]


def test_options_implied_probability_uses_deribit_iv_surface() -> None:
    probability = options_implied_probability(
        strike=150_000.0,
        direction="above",
        resolution_date=datetime(2026, 7, 1, tzinfo=UTC),
        asked_at=datetime(2026, 4, 1, tzinfo=UTC),
        iv=DeribitIV(
            index_price=100_000.0,
            expiry_days=90.0,
            realized_vol=0.45,
            atm_iv=0.55,
            strike_iv=0.70,
            atm_instrument="BTC-1",
            strike_instrument="BTC-2",
        ),
    )

    assert probability is not None
    assert 0.0 < probability < 1.0


def test_perp_basis_implied_probability_moves_with_mark_premium() -> None:
    asked_at = datetime(2026, 4, 1, tzinfo=UTC)
    resolution = datetime(2026, 7, 1, tzinfo=UTC)

    bearish = perp_basis_implied_probability(
        strike=110_000.0,
        direction="above",
        resolution_date=resolution,
        asked_at=asked_at,
        sigma=0.6,
        snapshot=BinancePerpSnapshot(
            symbol="BTCUSDT",
            index_price=100_000.0,
            mark_price=99_000.0,
            funding_rate=-0.0001,
        ),
    )
    bullish = perp_basis_implied_probability(
        strike=110_000.0,
        direction="above",
        resolution_date=resolution,
        asked_at=asked_at,
        sigma=0.6,
        snapshot=BinancePerpSnapshot(
            symbol="BTCUSDT",
            index_price=100_000.0,
            mark_price=101_000.0,
            funding_rate=0.0001,
        ),
    )

    assert bearish is not None and bullish is not None
    assert bullish > bearish


@pytest.mark.asyncio
@respx.mock
async def test_binance_klines_client_parses_daily_bars() -> None:
    # Two daily klines: 2026-04-22 and 2026-04-23.
    payload = [
        [
            1_745_280_000_000, "100000.0", "101500.0", "99800.0", "100900.0",
            "12345.6", 1_745_366_399_999, "1234567890.0",
            123, "10000.0", "1000000000.0", "0",
        ],
        [
            1_745_366_400_000, "100900.0", "102200.0", "100400.0", "101800.0",
            "23456.7", 1_745_452_799_999, "2345678901.0",
            456, "20000.0", "2000000000.0", "0",
        ],
    ]
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=httpx.Response(200, json=payload),
    )

    async with BinanceKlinesClient() as client:
        klines = await client.daily_klines(symbol="btcusdt", days=2)

    assert len(klines) == 2
    assert klines[0].symbol == "BTCUSDT"
    assert klines[0].open == pytest.approx(100_000.0)
    assert klines[0].close == pytest.approx(100_900.0)
    assert klines[1].close == pytest.approx(101_800.0)


@pytest.mark.asyncio
@respx.mock
async def test_binance_klines_client_skips_malformed_rows() -> None:
    payload = [
        ["not", "a", "kline"],
        [1_745_280_000_000, "100000.0", "101500.0", "99800.0", "100900.0",
         "12345.6", 1_745_366_399_999],
    ]
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=httpx.Response(200, json=payload),
    )
    async with BinanceKlinesClient() as client:
        klines = await client.daily_klines(symbol="BTCUSDT", days=2)
    assert len(klines) == 1
    assert klines[0].close == pytest.approx(100_900.0)
