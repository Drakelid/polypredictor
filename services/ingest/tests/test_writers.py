"""Row-shaping unit tests. No DB required."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.writers import (
    MARKETS_COLS,
    ORDERBOOK_COLS,
    PRICES_HISTORY_COLS,
    QUOTES_COLS,
    REGIME_LABELS_COLS,
    SMART_MONEY_POSITIONS_COLS,
    TRADES_COLS,
    market_row,
    orderbook_rows,
    price_history_rows,
    quote_row,
    regime_label_row,
    smart_money_position_row,
    trade_row,
)
from polymarket_client import Market, Position, PriceHistoryBucket


def _sample_market() -> Market:
    return Market.model_validate(
        {
            "conditionId": "0xabc",
            "eventId": "evt-1",
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
    assert by["event_id"] == "evt-1"
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


def test_price_history_rows_canonicalize_close_only_buckets() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    rows = price_history_rows(
        token_id="tok-yes",
        condition_id="0xabc",
        interval_bucket="1h",
        history=[
            PriceHistoryBucket(t=int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()), p=0.42),
            PriceHistoryBucket(t=int(datetime(2026, 1, 1, 1, tzinfo=UTC).timestamp()), p=0.45),
        ],
        observed_at=now,
    )
    assert len(rows) == 2
    first = dict(zip(PRICES_HISTORY_COLS, rows[0], strict=True))
    assert first["token_id"] == "tok-yes"
    assert first["condition_id"] == "0xabc"
    assert first["interval_bucket"] == "1h"
    assert first["bucket_start"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert first["open"] == pytest.approx(0.42)
    assert first["high"] == pytest.approx(0.42)
    assert first["low"] == pytest.approx(0.42)
    assert first["close"] == pytest.approx(0.42)
    assert first["volume"] == 0.0
    assert first["event_time"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert first["observed_at"] == now


def test_smart_money_position_row_shapes_leaderboard_context() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    position = Position.model_validate(
        {
            "proxyWallet": "0xwallet",
            "conditionId": "cond-1",
            "asset": "tok-yes",
            "outcome": "yes",
            "size": 125.0,
            "avgPrice": 0.41,
            "currentValue": 73.5,
            "cashPnl": 22.0,
        }
    )

    row = smart_money_position_row(
        position=position,
        leaderboard_rank=7,
        leaderboard_pnl=15_000.0,
        leaderboard_vol=250_000.0,
        observed_at=now,
    )

    by = dict(zip(SMART_MONEY_POSITIONS_COLS, row, strict=True))
    assert by["proxy_wallet"] == "0xwallet"
    assert by["condition_id"] == "cond-1"
    assert by["token_id"] == "tok-yes"
    assert by["outcome"] == "YES"
    assert by["size"] == pytest.approx(125.0)
    assert by["avg_entry_price"] == pytest.approx(0.41)
    assert by["current_value_usdc"] == pytest.approx(73.5)
    assert by["leaderboard_rank"] == 7
    assert by["leaderboard_pnl"] == pytest.approx(15_000.0)
    assert by["leaderboard_vol"] == pytest.approx(250_000.0)
    assert by["event_time"] == now
    assert by["observed_at"] == now


def test_regime_label_row_pipes_reasons_and_sets_event_time_to_midnight() -> None:
    observed = datetime(2026, 4, 24, 12, 30, tzinfo=UTC)
    regime_date = datetime(2026, 4, 24, tzinfo=UTC)
    row = regime_label_row(
        regime_date=regime_date,
        label="bull_trend",
        confidence=0.82,
        btc_realized_vol_24h=0.55,
        btc_realized_vol_7d=0.50,
        btc_momentum_7d=0.07,
        btc_ndx_correlation_30d=0.62,
        stablecoin_supply_delta_7d=0.005,
        reasons=["7d momentum +7.0% >= +4.0%", "BTC↔NDX corr = +0.62"],
        classifier="rule_v1",
        observed_at=observed,
    )
    by = dict(zip(REGIME_LABELS_COLS, row, strict=True))
    assert by["label"] == "bull_trend"
    assert by["confidence"] == pytest.approx(0.82)
    assert by["btc_momentum_7d"] == pytest.approx(0.07)
    assert by["reasons"] == "7d momentum +7.0% >= +4.0%|BTC↔NDX corr = +0.62"
    assert by["classifier"] == "rule_v1"
    assert by["event_time"] == datetime(2026, 4, 24, tzinfo=UTC)
    assert by["observed_at"] == observed
