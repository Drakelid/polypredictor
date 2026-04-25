"""Persist Binance + Coinbase perpetual funding and basis snapshots (M5).

For each configured asset we poll two public derivatives venues:

* Binance ``/fapi/v1/premiumIndex`` for ``ASSETUSDT`` perps.
* Coinbase International Exchange ``/api/v1/instruments/{instrument}/quote`` and
  ``/api/v1/instruments/{instrument}/funding`` for ``ASSET-PERP`` instruments.

Both emit into the same append-only ``perp_funding_basis`` table.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from model import (
    BinancePerpClient,
    BinancePerpSnapshot,
    CoinbaseIntxPerpClient,
    CoinbaseIntxPerpSnapshot,
)

from ..clickhouse import get_async_client
from ..settings import get_settings
from ..writers import PERP_FUNDING_BASIS_COLS, perp_funding_basis_row, utcnow

BINANCE_VENUE = "binance"
COINBASE_VENUE = "coinbase"
BINANCE_FUNDING_CYCLES_PER_DAY = 3.0  # 8h cycles
COINBASE_FUNDING_CYCLES_PER_DAY = 24.0  # hourly funding on INTX


def _assets() -> list[str]:
    raw = get_settings().perp_funding_basis_assets
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def _binance_symbol(asset: str) -> str:
    return f"{asset}USDT"


def _coinbase_instrument(asset: str) -> str:
    return f"{asset}-PERP"


async def _fetch_binance_snapshots(
    binance: BinancePerpClient,
    assets: Iterable[str],
) -> list[tuple[str, BinancePerpSnapshot]]:
    out: list[tuple[str, BinancePerpSnapshot]] = []
    for asset in assets:
        snapshot = await binance.premium_index(symbol=_binance_symbol(asset))
        if snapshot is None:
            continue
        out.append((asset, snapshot))
    return out


async def _fetch_coinbase_snapshots(
    coinbase: CoinbaseIntxPerpClient,
    assets: Iterable[str],
) -> list[tuple[str, CoinbaseIntxPerpSnapshot]]:
    out: list[tuple[str, CoinbaseIntxPerpSnapshot]] = []
    for asset in assets:
        snapshot = await coinbase.snapshot(instrument=_coinbase_instrument(asset))
        if snapshot is None:
            continue
        out.append((asset, snapshot))
    return out


async def run_once(
    *,
    binance: BinancePerpClient | None = None,
    coinbase: CoinbaseIntxPerpClient | None = None,
) -> int:
    settings = get_settings()
    ch = await get_async_client()
    own_binance = binance is None
    own_coinbase = coinbase is None
    observed_at = utcnow()
    rows: list[tuple[object, ...]] = []
    try:
        if binance is None:
            binance = BinancePerpClient(base_url=settings.binance_base)
        if coinbase is None:
            coinbase = CoinbaseIntxPerpClient(base_url=settings.coinbase_intx_base)
        for asset, snapshot in await _fetch_binance_snapshots(binance, _assets()):
            rows.append(
                perp_funding_basis_row(
                    venue=BINANCE_VENUE,
                    asset=asset,
                    symbol=snapshot.symbol,
                    index_price=snapshot.index_price,
                    mark_price=snapshot.mark_price,
                    funding_rate=snapshot.funding_rate,
                    funding_cycles_per_day=BINANCE_FUNDING_CYCLES_PER_DAY,
                    observed_at=observed_at,
                )
            )
        for asset, snapshot in await _fetch_coinbase_snapshots(coinbase, _assets()):
            rows.append(
                perp_funding_basis_row(
                    venue=COINBASE_VENUE,
                    asset=asset,
                    symbol=snapshot.instrument,
                    index_price=snapshot.index_price,
                    mark_price=snapshot.mark_price,
                    funding_rate=snapshot.funding_rate,
                    funding_cycles_per_day=COINBASE_FUNDING_CYCLES_PER_DAY,
                    observed_at=observed_at,
                )
            )
        if rows:
            await ch.insert(
                "perp_funding_basis",
                rows,
                column_names=PERP_FUNDING_BASIS_COLS,
            )
        return len(rows)
    finally:
        if own_binance and binance is not None:
            await binance.aclose()
        if own_coinbase and coinbase is not None:
            await coinbase.aclose()
        await ch.close()


async def run_forever() -> None:
    settings = get_settings()
    while True:
        await run_once()
        await asyncio.sleep(settings.perp_funding_basis_interval_s)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
