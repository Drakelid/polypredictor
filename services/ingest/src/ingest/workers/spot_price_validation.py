"""Cross-validate spot prices across Binance / Coinbase / CoinGecko.

This closes the M5 price-feed cross-validation task. For each configured
asset, we fetch spot prices from the three venues, reject quotes that diverge
too far from the median, and persist only the consensus reference price.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from model import (
    BinanceSpotClient,
    CoinbaseSpotClient,
    CoinGeckoSpotClient,
    SpotTickerSnapshot,
    validate_spot_quotes,
)

from ..clickhouse import get_async_client
from ..settings import get_settings
from ..writers import VALIDATED_SPOT_PRICE_COLS, utcnow, validated_spot_price_row


def _assets() -> list[str]:
    raw = get_settings().spot_validation_assets
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def _binance_symbol(asset: str) -> str:
    return f"{asset}USDT"


def _coinbase_product(asset: str) -> str:
    return f"{asset}-USD"


def _coingecko_id(asset: str) -> str | None:
    return {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
    }.get(asset)


async def _quotes_for_asset(
    *,
    asset: str,
    binance: BinanceSpotClient,
    coinbase: CoinbaseSpotClient,
    coingecko: CoinGeckoSpotClient,
) -> list[SpotTickerSnapshot]:
    quotes: list[SpotTickerSnapshot] = []
    binance_quote = await binance.book_ticker(symbol=_binance_symbol(asset))
    if binance_quote is not None:
        quotes.append(binance_quote)
    coinbase_quote = await coinbase.product_ticker(product_id=_coinbase_product(asset))
    if coinbase_quote is not None:
        quotes.append(coinbase_quote)
    coingecko_id = _coingecko_id(asset)
    if coingecko_id is not None:
        coingecko_quote = await coingecko.simple_price(coin_id=coingecko_id)
        if coingecko_quote is not None:
            quotes.append(coingecko_quote)
    return quotes


async def run_once() -> int:
    settings = get_settings()
    observed_at = utcnow()
    ch = await get_async_client()
    rows: list[tuple[object, ...]] = []
    try:
        async with AsyncExitStack() as stack:
            binance = await stack.enter_async_context(
                BinanceSpotClient(base_url=settings.binance_spot_base)
            )
            coinbase = await stack.enter_async_context(
                CoinbaseSpotClient(base_url=settings.coinbase_exchange_base)
            )
            coingecko = await stack.enter_async_context(
                CoinGeckoSpotClient(base_url=settings.coingecko_base)
            )
            for asset in _assets():
                quotes = await _quotes_for_asset(
                    asset=asset,
                    binance=binance,
                    coinbase=coinbase,
                    coingecko=coingecko,
                )
                validation = validate_spot_quotes(
                    quotes,
                    max_deviation_bps=settings.spot_validation_max_deviation_bps,
                    min_venues=settings.spot_validation_min_venues,
                )
                rows.append(
                    validated_spot_price_row(
                        asset=asset,
                        reference_price=validation.reference_price,
                        quotes=quotes,
                        accepted_venues=[quote.venue for quote in validation.accepted_quotes],
                        rejected_venues=[quote.venue for quote in validation.rejected_quotes],
                        max_divergence_bps=validation.max_divergence_bps,
                        is_valid=validation.is_valid,
                        observed_at=observed_at,
                    )
                )
        if rows:
            await ch.insert(
                "validated_spot_prices",
                rows,
                column_names=VALIDATED_SPOT_PRICE_COLS,
            )
        return len(rows)
    finally:
        await ch.close()


async def run_forever() -> None:
    settings = get_settings()
    while True:
        await run_once()
        await asyncio.sleep(settings.spot_validation_interval_s)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
