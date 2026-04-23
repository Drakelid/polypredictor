"""GammaClient integration-shape tests."""

from __future__ import annotations

import httpx
import pytest
import respx
from polymarket_client import GammaClient
from polymarket_client import gamma as gamma_mod
from polymarket_client.cache import TTLCache


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


@pytest.mark.asyncio
@respx.mock
async def test_list_markets_revalidates_with_etag_when_ttl_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gamma_mod, "TTL_MARKETS", 0.0)
    route = respx.get("https://gamma-api.polymarket.com/markets").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[
                    {
                        "conditionId": "0xabc",
                        "question": "Will BTC > $150k?",
                        "slug": "btc-150k",
                        "active": True,
                        "closed": False,
                        "clobTokenIds": '["tok-yes", "tok-no"]',
                    }
                ],
                headers={"etag": '"markets-v1"'},
            ),
            httpx.Response(304),
        ]
    )
    async with GammaClient(cache=TTLCache()) as g:
        first = await g.list_markets()
        second = await g.list_markets()
    assert first[0].condition_id == second[0].condition_id
    assert route.call_count == 2
    assert route.calls.last.request.headers["if-none-match"] == '"markets-v1"'
