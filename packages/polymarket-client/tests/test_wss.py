"""WSS book-state and dispatch tests.

We don't spin up a real Polymarket WSS in unit tests; instead we exercise the
pure-state pieces (book delta application, event parsing) directly.
"""

from __future__ import annotations

import asyncio

import pytest
from polymarket_client import MarketWssManager, WssEvent
from polymarket_client.wss import _BookState


def test_book_state_applies_full_book() -> None:
    s = _BookState()
    s.apply_full_book(
        bids=[{"price": 0.51, "size": 100}, {"price": 0.50, "size": 200}],
        asks=[{"price": 0.52, "size": 50}],
    )
    assert s.bids == {0.51: 100.0, 0.50: 200.0}
    assert s.asks == {0.52: 50.0}


def test_book_state_ignores_zero_size_on_full_book() -> None:
    s = _BookState()
    s.apply_full_book(
        bids=[{"price": 0.51, "size": 0}],
        asks=[{"price": 0.52, "size": 0}],
    )
    assert s.bids == {}
    assert s.asks == {}


def test_book_state_delta_sets_and_removes() -> None:
    s = _BookState()
    s.apply_price_change([{"price": 0.51, "size": 100, "side": "BUY"}])
    assert s.bids == {0.51: 100.0}
    # Zero size removes the level.
    s.apply_price_change([{"price": 0.51, "size": 0, "side": "BUY"}])
    assert s.bids == {}


def test_book_state_side_interpretation() -> None:
    s = _BookState()
    s.apply_price_change(
        [
            {"price": 0.50, "size": 10, "side": "buy"},
            {"price": 0.55, "size": 20, "side": "sell"},
            {"price": 0.49, "size": 5, "side": "b"},  # abbreviated
            {"price": 0.56, "size": 7, "side": "a"},  # abbreviated ask
        ]
    )
    assert s.bids == {0.50: 10.0, 0.49: 5.0}
    assert s.asks == {0.55: 20.0, 0.56: 7.0}


@pytest.mark.asyncio
async def test_dispatch_invokes_callback_with_parsed_event() -> None:
    received: list[WssEvent] = []

    async def cb(ev: WssEvent) -> None:
        received.append(ev)

    mgr = MarketWssManager(callback=cb, initial_token_ids=["tok-1"])
    # Exercise the pure dispatch path without opening a network connection.
    await mgr._dispatch(  # type: ignore[attr-defined]
        {
            "event_type": "book",
            "asset_id": "tok-1",
            "market": "0xabc",
            "bids": [{"price": 0.4, "size": 10}],
            "asks": [{"price": 0.6, "size": 20}],
            "hash": "h1",
            "timestamp": 1700000000,
        }
    )
    assert len(received) == 1
    ev = received[0]
    assert ev.channel == "market"
    assert ev.event_type == "book"
    assert ev.token_id == "tok-1"
    assert ev.condition_id == "0xabc"
    assert ev.event_time == 1700000000.0
    # Book state updated in-place.
    assert mgr._books["tok-1"].bids == {0.4: 10.0}  # type: ignore[attr-defined]
    assert mgr._books["tok-1"].asks == {0.6: 20.0}  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dispatch_price_change_updates_existing_book() -> None:
    async def cb(_: WssEvent) -> None:
        pass

    mgr = MarketWssManager(callback=cb, initial_token_ids=["tok-1"])
    await mgr._dispatch(  # type: ignore[attr-defined]
        {
            "event_type": "book",
            "asset_id": "tok-1",
            "market": "0x1",
            "bids": [{"price": 0.4, "size": 10}],
            "asks": [{"price": 0.6, "size": 20}],
            "hash": "h1",
        }
    )
    await mgr._dispatch(  # type: ignore[attr-defined]
        {
            "event_type": "price_change",
            "asset_id": "tok-1",
            "market": "0x1",
            "changes": [
                {"price": 0.4, "size": 15, "side": "BUY"},
                {"price": 0.6, "size": 0, "side": "SELL"},
            ],
            "hash": "h2",
        }
    )
    assert mgr._books["tok-1"].bids == {0.4: 15.0}  # type: ignore[attr-defined]
    assert mgr._books["tok-1"].asks == {}  # type: ignore[attr-defined]
    assert mgr._books["tok-1"].last_hash == "h2"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_callback_errors_are_logged_not_fatal() -> None:
    async def cb(_: WssEvent) -> None:
        raise RuntimeError("boom")

    mgr = MarketWssManager(callback=cb)
    # Should not raise.
    await mgr._dispatch({"event_type": "trade", "asset_id": "tok-x", "market": "0x1"})  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_subscribe_and_unsubscribe_update_desired_set() -> None:
    async def cb(_: WssEvent) -> None:
        pass

    mgr = MarketWssManager(callback=cb)
    await mgr.subscribe(["a", "b"])
    assert set(mgr.tracked_tokens()) == {"a", "b"}
    await mgr.unsubscribe(["a"])
    assert mgr.tracked_tokens() == ["b"]
    # Idempotent subscribe
    await mgr.subscribe(["b"])
    assert mgr.tracked_tokens() == ["b"]
    # Unsubscribe unknown is a no-op
    await mgr.unsubscribe(["never-subscribed"])
    assert mgr.tracked_tokens() == ["b"]
    # Prevent pytest warning about unawaited coroutines on the internal event.
    await asyncio.sleep(0)
