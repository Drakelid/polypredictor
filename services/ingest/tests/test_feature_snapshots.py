from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ingest.workers.feature_snapshots import (
    OrderbookLevel,
    TimedMid,
    TimedTrade,
    _annualized_realized_vol,
    _book_imbalance,
    _momentum,
    _trade_flow_features,
    build_feature_snapshots,
)


def test_book_imbalance_uses_requested_depth_window() -> None:
    levels = [
        OrderbookLevel(side="bid", price=0.60, size=120),
        OrderbookLevel(side="bid", price=0.59, size=80),
        OrderbookLevel(side="ask", price=0.61, size=50),
        OrderbookLevel(side="ask", price=0.62, size=70),
    ]

    out = _book_imbalance(levels, best_bid=0.60, best_ask=0.61, depth_pct=0.05)

    assert out == pytest.approx((200 - 120) / 320)


def test_momentum_requires_positive_past_mid() -> None:
    assert _momentum(0.62, 0.5) == pytest.approx(0.24)
    assert _momentum(0.62, None) is None
    assert _momentum(0.62, 0.0) is None


def test_realized_vol_annualizes_irregular_quote_path() -> None:
    points = [
        TimedMid(observed_at=datetime(2026, 4, 22, 0, tzinfo=UTC), mid=0.45),
        TimedMid(observed_at=datetime(2026, 4, 22, 6, tzinfo=UTC), mid=0.50),
        TimedMid(observed_at=datetime(2026, 4, 22, 18, tzinfo=UTC), mid=0.55),
    ]

    out = _annualized_realized_vol(points)

    assert out is not None
    assert out > 0


def test_trade_flow_features_split_informed_passive_and_decayed() -> None:
    observed_at = datetime(2026, 4, 22, 12, tzinfo=UTC)
    trades = [
        TimedTrade(
            observed_at=observed_at - timedelta(hours=1),
            size=120,
            aggressor_side="buy",
            crossed_spread=True,
        ),
        TimedTrade(
            observed_at=observed_at - timedelta(hours=2),
            size=40,
            aggressor_side="sell",
            crossed_spread=False,
        ),
        TimedTrade(
            observed_at=observed_at - timedelta(hours=12),
            size=30,
            aggressor_side="sell",
            crossed_spread=True,
        ),
    ]

    informed, passive, decayed = _trade_flow_features(
        trades,
        observed_at=observed_at,
        decay_half_life_h=6,
    )

    assert informed is not None and informed > 0
    assert passive is not None and passive > 0
    assert decayed is not None and decayed > 0


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, results: list[list[tuple[object, ...]]]) -> None:
        self.results = results
        self.calls: list[str] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.calls.append(query)
        return _QueryResult(self.results.pop(0))


@pytest.mark.asyncio
async def test_build_feature_snapshots_shapes_all_requested_features() -> None:
    observed_at = datetime(2026, 4, 22, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [("cond-1", ["tok-yes", "tok-no"], 1, 0, 0)],
            [("tok-yes", "cond-1", 0.62, 0.60, 0.64, 0.04, observed_at)],
            [
                ("tok-yes", "bid", 0.60, 100.0),
                ("tok-yes", "bid", 0.59, 50.0),
                ("tok-yes", "ask", 0.64, 70.0),
                ("tok-yes", "ask", 0.65, 30.0),
            ],
            [("tok-yes", 0.58)],
            [("tok-yes", 0.50)],
            [("tok-yes", 0.40)],
            [
                ("tok-yes", observed_at - timedelta(hours=24), 0.50),
                ("tok-yes", observed_at - timedelta(hours=12), 0.56),
                ("tok-yes", observed_at, 0.62),
            ],
            [
                ("tok-yes", observed_at - timedelta(hours=1), 120.0, "buy", True),
                ("tok-yes", observed_at - timedelta(hours=2), 40.0, "sell", False),
            ],
        ]
    )

    snapshots = await build_feature_snapshots(
        ch,
        observed_at=observed_at,
        batch_size=50,
        decay_half_life_h=6,
    )

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.condition_id == "cond-1"
    assert snapshot.token_id == "tok-yes"
    assert snapshot.mid == pytest.approx(0.62)
    assert snapshot.spread == pytest.approx(0.04)
    assert snapshot.book_imbalance_1pct is not None
    assert snapshot.book_imbalance_5pct is not None
    assert snapshot.momentum_1h == pytest.approx((0.62 / 0.58) - 1.0)
    assert snapshot.momentum_24h == pytest.approx((0.62 / 0.50) - 1.0)
    assert snapshot.momentum_7d == pytest.approx((0.62 / 0.40) - 1.0)
    assert snapshot.realized_vol_24h is not None
    assert snapshot.informed_taker_flow_24h is not None
    assert snapshot.passive_maker_flow_24h is not None
    assert snapshot.decayed_directional_flow_24h is not None
