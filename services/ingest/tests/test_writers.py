"""Row-shaping unit tests. No DB required."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.writers import (
    MARKETS_COLS,
    ORDERBOOK_COLS,
    QUOTES_COLS,
    TRADES_COLS,
    market_row,
    orderbook_rows,
    quote_row,
    trade_row,
)
from polymarket_client import Market


def _sample_market() -> Market:
    return Market.model_validate(
        {
            "conditionId": "0xabc",
            "question": "Will BTC > $150k?",
            "slug": "btc-150k",
            "description": "yes means yes",
            "category": "crypto",
            "tags": ["crypto"],
            "active": True,
            "closed": False,
            "archived": False,
            "volume": 10_000.0,
            "liquidity": 500.0,
            "openInterest": 250.0,
            "token_ids": ["tyes", "tno"],
        }
    )


def test_market_row_positional_columns_match_schema() -> None:
    m = _sample_market()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    row = market_row(m, observed_at=now)
    assert len(row) == len(MARKETS_COLS)
    by = dict(zip(MARKETS_COLS, row, strict=True))
    assert by["condition_id"] == "0xabc"
    assert by["active"] == 1
    assert by["closed"] == 0
    assert by["archived"] == 0
    assert by["volume_usdc"] == 10_000.0
    assert by["liquidity_usdc"] == 500.0
    assert by["open_interest_usdc"] == 250.0
    assert by["token_ids"] == ["tyes", "tno"]
    assert by["observed_at"] == now
    assert by["event_time"] == now
    assert by["payload_hash"]  # non-empty


def test_market_row_stable_payload_hash() -> None:
    m = _sample_market()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    r1 = market_row(m, observed_at=now)
    r2 = market_row(m, observed_at=now)
    hash_idx = MARKETS_COLS.index("payload_hash")
    assert r1[hash_idx] == r2[hash_idx]


def test_quote_row_computes_spread() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    row = quote_row(
        token_id="t",
        condition_id="0x",
        mid=0.505,
        best_bid=0.50,
        best_ask=0.51,
        bid_size=100,
        ask_size=200,
        observed_at=now,
    )
    by = dict(zip(QUOTES_COLS, row, strict=True))
    assert by["spread"] == pytest.approx(0.01)
    assert by["observed_at"] == now
    assert by["event_time"] == now


def test_quote_row_clamps_spread_nonnegative() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    # Malformed book with bid > ask — spread should be clamped to 0.
    row = quote_row(
        token_id="t",
        condition_id="0x",
        mid=0.5,
        best_bid=0.6,
        best_ask=0.55,
        bid_size=1,
        ask_size=1,
        observed_at=now,
    )
    by = dict(zip(QUOTES_COLS, row, strict=True))
    assert by["spread"] == 0.0


def test_orderbook_rows_shape_and_sides() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    rows = orderbook_rows(
        token_id="t",
        condition_id="0x",
        bids=[(0.4, 10), (0.39, 20)],
        asks=[(0.5, 5)],
        observed_at=now,
    )
    assert len(rows) == 3
    for r in rows:
        assert len(r) == len(ORDERBOOK_COLS)
    sides = [dict(zip(ORDERBOOK_COLS, r, strict=True))["side"] for r in rows]
    assert sides == ["bid", "bid", "ask"]


def test_trade_row_normalizes_side() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    row = trade_row(
        trade_id="tr1",
        token_id="t",
        condition_id="0x",
        price=0.55,
        size=5,
        side="BUY",
        aggressor_side=None,
        crossed_spread=None,
        event_time=now,
        observed_at=now,
    )
    by = dict(zip(TRADES_COLS, row, strict=True))
    assert by["side"] == "buy"
    assert by["aggressor_side"] is None
    assert by["crossed_spread"] is None
