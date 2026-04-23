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
