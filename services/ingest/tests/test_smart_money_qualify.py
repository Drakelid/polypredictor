from __future__ import annotations

import pytest
from ingest.workers.smart_money_qualify import (
    QualificationThresholds,
    aggregate_per_market,
    qualify_wallet,
)
from polymarket_client import Position


def _position(
    wallet: str,
    *,
    condition_id: str,
    token_id: str = "tok-1",
    outcome: str = "yes",
    size: float = 100.0,
    avg_price: float = 0.45,
    current_value: float = 55.0,
) -> Position:
    return Position.model_validate(
        {
            "proxyWallet": wallet,
            "conditionId": condition_id,
            "asset": token_id,
            "outcome": outcome,
            "size": size,
            "avgPrice": avg_price,
            "currentValue": current_value,
            "cashPnl": 0.0,
        }
    )


THRESHOLDS = QualificationThresholds(min_volume=10_000.0, min_distinct_markets=3)


def test_qualify_wallet_qualifies_when_all_guards_pass() -> None:
    positions = [
        _position("0xaaa", condition_id="m1"),
        _position("0xaaa", condition_id="m2"),
        _position("0xaaa", condition_id="m3", outcome="no"),
    ]
    decision = qualify_wallet(
        proxy_wallet="0xaaa",
        sources=("CRYPTO:MONTH:PNL", "CRYPTO:ALL:VOL"),
        leaderboard_rank=12,
        leaderboard_pnl=2500.0,
        leaderboard_vol=50_000.0,
        positions=positions,
        thresholds=THRESHOLDS,
    )

    assert decision.qualified is True
    assert decision.reasons == ("qualified",)
    assert decision.distinct_markets == 3
    assert decision.appeared_in_pnl is True
    assert decision.appeared_in_vol is True
    assert decision.min_volume_ok is True


def test_qualify_wallet_rejects_single_ordering_only() -> None:
    positions = [
        _position("0xbbb", condition_id="m1"),
        _position("0xbbb", condition_id="m2"),
        _position("0xbbb", condition_id="m3"),
    ]
    decision = qualify_wallet(
        proxy_wallet="0xbbb",
        sources=("CRYPTO:MONTH:PNL", "FINANCE:ALL:PNL"),
        leaderboard_rank=3,
        leaderboard_pnl=10_000.0,
        leaderboard_vol=50_000.0,
        positions=positions,
        thresholds=THRESHOLDS,
    )

    assert decision.qualified is False
    assert "missing_vol_ordering" in decision.reasons
    assert decision.appeared_in_pnl is True
    assert decision.appeared_in_vol is False


def test_qualify_wallet_rejects_single_market_even_if_cross_ordered() -> None:
    decision = qualify_wallet(
        proxy_wallet="0xccc",
        sources=("CRYPTO:MONTH:PNL", "CRYPTO:ALL:VOL"),
        leaderboard_rank=8,
        leaderboard_pnl=1000.0,
        leaderboard_vol=50_000.0,
        positions=[_position("0xccc", condition_id="m1")],
        thresholds=THRESHOLDS,
    )

    assert decision.qualified is False
    assert "below_min_distinct_markets" in decision.reasons
    assert decision.distinct_markets == 1


def test_qualify_wallet_rejects_below_min_volume() -> None:
    positions = [
        _position("0xddd", condition_id=f"m{i}") for i in range(5)
    ]
    decision = qualify_wallet(
        proxy_wallet="0xddd",
        sources=("CRYPTO:MONTH:PNL", "CRYPTO:ALL:VOL"),
        leaderboard_rank=50,
        leaderboard_pnl=200.0,
        leaderboard_vol=500.0,
        positions=positions,
        thresholds=THRESHOLDS,
    )

    assert decision.qualified is False
    assert decision.min_volume_ok is False
    assert "below_min_volume" in decision.reasons


def test_aggregate_per_market_only_counts_qualified_wallets() -> None:
    positions_by_wallet = {
        "0xqual1": [
            _position("0xqual1", condition_id="m1", outcome="yes", size=100.0, current_value=55.0, avg_price=0.55),
            _position("0xqual1", condition_id="m2", outcome="no", size=40.0, current_value=10.0, avg_price=0.30),
        ],
        "0xqual2": [
            _position("0xqual2", condition_id="m1", outcome="yes", size=50.0, current_value=30.0, avg_price=0.60),
        ],
        "0xbad": [
            # Wash-trade wallet — same market as qual2 but disqualified.
            _position("0xbad", condition_id="m1", outcome="no", size=1000.0, current_value=400.0, avg_price=0.40),
        ],
    }
    qualified = {"0xqual1", "0xqual2"}

    aggregates = aggregate_per_market(positions_by_wallet, qualified)

    by_market = {agg.condition_id: agg for agg in aggregates}
    assert set(by_market.keys()) == {"m1", "m2"}

    m1 = by_market["m1"]
    assert m1.yes_wallets == 2
    assert m1.no_wallets == 0
    assert m1.yes_size == pytest.approx(150.0)
    assert m1.no_size == pytest.approx(0.0)
    assert m1.yes_size_usdc == pytest.approx(85.0)
    assert m1.no_size_usdc == pytest.approx(0.0)
    # Size-weighted: (100 * 0.55 + 50 * 0.60) / 150
    assert m1.yes_avg_entry == pytest.approx((100 * 0.55 + 50 * 0.60) / 150)
    assert m1.no_avg_entry is None
    assert m1.sample_wallets == 2

    m2 = by_market["m2"]
    assert m2.yes_wallets == 0
    assert m2.no_wallets == 1
    assert m2.no_avg_entry == pytest.approx(0.30)


def test_aggregate_per_market_empty_when_no_qualified_wallets() -> None:
    positions = {
        "0xwhoever": [_position("0xwhoever", condition_id="m1")],
    }
    assert aggregate_per_market(positions, set()) == []
