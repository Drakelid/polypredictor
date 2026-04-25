from __future__ import annotations

import httpx
import pytest
import respx
from model import DeribitClient


@pytest.mark.asyncio
@respx.mock
async def test_fetch_iv_reuses_cached_book_summary_by_currency() -> None:
    summary_route = respx.get(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {
                        "instrument_name": "BTC-30MAY26-100000-C",
                        "underlying_price": 100_000,
                        "mark_iv": 55.0,
                    },
                    {
                        "instrument_name": "BTC-30MAY26-100000-P",
                        "underlying_price": 100_000,
                        "mark_iv": 56.0,
                    },
                    {
                        "instrument_name": "BTC-30MAY26-120000-C",
                        "underlying_price": 100_000,
                        "mark_iv": 60.0,
                    },
                ]
            },
        )
    )
    hv_route = respx.get(
        "https://www.deribit.com/api/v2/public/get_historical_volatility"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"result": [[1_700_000_000_000, 42.0], [1_700_000_600_000, 44.0]]},
        )
    )

    async with DeribitClient(cache_ttl_s=60.0) as client:
        first = await client.fetch_iv(
            currency="BTC",
            strike=120_000,
            direction="above",
            horizon_days=30,
        )
        second = await client.fetch_iv(
            currency="BTC",
            strike=100_000,
            direction="below",
            horizon_days=30,
        )

    assert first is not None
    assert second is not None
    assert first.realized_vol == pytest.approx(0.44)
    assert second.realized_vol == pytest.approx(0.44)
    assert summary_route.call_count == 1
    assert hv_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_term_structure_returns_atm_and_skew_points() -> None:
    respx.get(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {
                        "instrument_name": "BTC-30MAY26-95000-P",
                        "underlying_price": 100_000,
                        "mark_iv": 58.0,
                    },
                    {
                        "instrument_name": "BTC-30MAY26-100000-C",
                        "underlying_price": 100_000,
                        "mark_iv": 55.0,
                    },
                    {
                        "instrument_name": "BTC-30MAY26-105000-C",
                        "underlying_price": 100_000,
                        "mark_iv": 61.0,
                    },
                    {
                        "instrument_name": "BTC-27JUN26-100000-C",
                        "underlying_price": 100_000,
                        "mark_iv": 57.0,
                    },
                    {
                        "instrument_name": "BTC-27JUN26-95000-P",
                        "underlying_price": 100_000,
                        "mark_iv": 59.0,
                    },
                    {
                        "instrument_name": "BTC-27JUN26-105000-C",
                        "underlying_price": 100_000,
                        "mark_iv": 63.0,
                    },
                ]
            },
        )
    )

    async with DeribitClient(cache_ttl_s=60.0) as client:
        points = await client.fetch_term_structure(currency="BTC")

    assert len(points) == 2
    assert points[0].currency == "BTC"
    assert points[0].atm_iv == pytest.approx(0.55)
    assert points[0].call_otm_iv == pytest.approx(0.61)
    assert points[0].put_otm_iv == pytest.approx(0.58)
    assert points[0].strike_skew == pytest.approx(0.03)
