"""Contract tests against recorded Polymarket response fixtures.

These fixtures are intentionally small but preserve the public response shapes
that have caused integration churn before: Gamma stringified ``clobTokenIds``,
CLOB string numeric book levels, history wrapper objects, and Data API
``items`` wrappers. The tests mock transport I/O with recorded payloads rather
than handcrafted per-assertion dictionaries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from polymarket_client import ClobClient, DataClient, GammaClient

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
@respx.mock
async def test_gamma_clients_parse_recorded_market_and_event_shapes() -> None:
    respx.get("https://gamma-api.polymarket.com/markets").mock(
        return_value=httpx.Response(200, json=fixture("gamma_markets.json"))
    )
    respx.get("https://gamma-api.polymarket.com/events").mock(
        return_value=httpx.Response(200, json=fixture("gamma_events.json"))
    )

    async with GammaClient() as client:
        markets = await client.list_markets(limit=1)
        events = await client.list_events(limit=1)

    assert markets[0].condition_id == "0xbtc150"
    assert markets[0].token_ids == ["tok-yes-btc150", "tok-no-btc150"]
    assert markets[0].category == "Crypto"
    assert markets[0].volume == pytest.approx(123456.78)
    assert events[0].markets[0].token_ids == ["tok-yes-btc150", "tok-no-btc150"]


@pytest.mark.asyncio
@respx.mock
async def test_clob_client_parses_recorded_book_history_and_trades() -> None:
    respx.get("https://clob.polymarket.com/book").mock(
        return_value=httpx.Response(200, json=fixture("clob_book.json"))
    )
    respx.get("https://clob.polymarket.com/prices-history").mock(
        return_value=httpx.Response(200, json=fixture("clob_prices_history.json"))
    )
    respx.get("https://clob.polymarket.com/trades").mock(
        return_value=httpx.Response(200, json=fixture("clob_trades.json"))
    )

    async with ClobClient() as client:
        book = await client.book("tok-yes-btc150")
        history = await client.prices_history("tok-yes-btc150")
        trades = await client.trades(token_id="tok-yes-btc150")

    assert book.market == "0xbtc150"
    assert book.bids[0].price == pytest.approx(0.41)
    assert book.asks[0].size == pytest.approx(1100)
    assert history[-1].p == pytest.approx(0.42)
    assert trades[0].trade_id == "trade-1"
    assert trades[0].timestamp is not None


@pytest.mark.asyncio
@respx.mock
async def test_data_client_parses_recorded_leaderboard_and_holders() -> None:
    respx.get("https://data-api.polymarket.com/v1/leaderboard").mock(
        return_value=httpx.Response(200, json=fixture("data_leaderboard.json"))
    )
    respx.get("https://data-api.polymarket.com/holders").mock(
        return_value=httpx.Response(200, json=fixture("data_holders.json"))
    )

    async with DataClient() as client:
        leaderboard = await client.leaderboard(category="CRYPTO", limit=1)
        holders = await client.holders("0xbtc150")

    assert leaderboard[0].proxy_wallet == "0x1111111111111111111111111111111111111111"
    assert leaderboard[0].rank == 1
    assert leaderboard[0].volume == pytest.approx(540000.75)
    assert holders["0xbtc150"][0].proxy_wallet == "0x2222222222222222222222222222222222222222"
    assert holders["0xbtc150"][1].outcome == "NO"
