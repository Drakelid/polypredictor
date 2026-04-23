"""GammaClient integration-shape tests."""

from __future__ import annotations

import httpx
import pytest
import respx
from polymarket_client import GammaClient


@pytest.mark.asyncio
@respx.mock
async def test_list_markets_normalizes_clob_token_ids() -> None:
    respx.get("https://gamma-api.polymarket.com/markets").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "conditionId": "0xabc",
                    "question": "Will BTC > $150k?",
                    "slug": "btc-150k",
                    "active": True,
                    "closed": False,
                    "volume": 123.0,
                    "liquidity": 45.0,
                    # Polymarket returns this as a JSON-encoded string.
                    "clobTokenIds": '["tok-yes", "tok-no"]',
                }
            ],
        )
    )
    async with GammaClient() as g:
        markets = await g.list_markets()
    assert len(markets) == 1
    m = markets[0]
    assert m.condition_id == "0xabc"
    assert m.token_ids == ["tok-yes", "tok-no"]
    assert m.active is True


@pytest.mark.asyncio
@respx.mock
async def test_list_markets_empty_clob_token_ids_string_ok() -> None:
    respx.get("https://gamma-api.polymarket.com/markets").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "conditionId": "0xabc",
                    "question": "q",
                    "slug": "s",
                    "active": True,
                    "closed": False,
                }
            ],
        )
    )
    async with GammaClient() as g:
        markets = await g.list_markets()
    assert markets[0].token_ids == []
