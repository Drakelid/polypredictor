"""Persist Deribit IV term-structure snapshots for BTC/ETH.

This closes the M5 "IV, skew, term structure poller" task. We derive a
coarse surface from the same public ``get_book_summary_by_currency`` response
already used by the threshold baseline:

* ATM IV per expiry
* nearest OTM call / put IV around spot
* strike skew = call_otm_iv - put_otm_iv
"""

from __future__ import annotations

import asyncio

from model import DeribitClient

from ..clickhouse import get_async_client
from ..settings import get_settings
from ..writers import DERIBIT_IV_SURFACE_COLS, deribit_iv_surface_row, utcnow


def _currencies() -> list[str]:
    raw = get_settings().deribit_iv_surface_currencies
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


async def run_once(*, deribit: DeribitClient | None = None) -> int:
    settings = get_settings()
    ch = await get_async_client()
    own_client = deribit is None
    observed_at = utcnow()
    rows: list[tuple[object, ...]] = []
    try:
        if deribit is None:
            deribit = DeribitClient(base_url=settings.deribit_base)
        for currency in _currencies():
            points = await deribit.fetch_term_structure(currency=currency)
            for point in points:
                rows.append(
                    deribit_iv_surface_row(
                        currency=point.currency,
                        expiry_date=point.expiry_date,
                        expiry_days=point.expiry_days,
                        underlying_price=point.underlying_price,
                        atm_iv=point.atm_iv,
                        call_otm_iv=point.call_otm_iv,
                        put_otm_iv=point.put_otm_iv,
                        strike_skew=point.strike_skew,
                        atm_instrument=point.atm_instrument,
                        call_otm_instrument=point.call_otm_instrument,
                        put_otm_instrument=point.put_otm_instrument,
                        observed_at=observed_at,
                    )
                )
        if rows:
            await ch.insert(
                "deribit_iv_surface",
                rows,
                column_names=DERIBIT_IV_SURFACE_COLS,
            )
        return len(rows)
    finally:
        if own_client and deribit is not None:
            await deribit.aclose()
        await ch.close()


async def run_forever() -> None:
    settings = get_settings()
    while True:
        await run_once()
        await asyncio.sleep(settings.deribit_iv_surface_interval_s)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
