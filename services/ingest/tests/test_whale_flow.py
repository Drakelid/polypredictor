from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.whale_flow import (
    PositionSnapshot,
    WhaleFlowThresholds,
    detect_whale_flow,
    positions_from_polymarket,
)
from polymarket_client import Position


def _snap(
    wallet: str,
    *,
    condition_id: str = "cond-1",
    token_id: str = "tok-y",
    outcome: str = "YES",
    size: float = 1.0,
    size_usdc: float = 0.0,
    avg: float = 0.5,
) -> PositionSnapshot:
    return PositionSnapshot(
        proxy_wallet=wallet,
        condition_id=condition_id,
        token_id=token_id,
        outcome=outcome,
        size=size,
        size_usdc=size_usdc,
        avg_entry_price=avg,
    )


THRESHOLDS = WhaleFlowThresholds(open_threshold_usdc=10_000.0, resize_pct=0.20)
NOW = datetime(2026, 4, 23, 12, tzinfo=UTC)


def test_new_qualifying_position_emits_whale_open() -> None:
    events = detect_whale_flow(
        current=[_snap("0xw", size_usdc=15_000.0)],
        prior=[],
        qualified_wallets={"0xw"},
        observed_at=NOW,
        thresholds=THRESHOLDS,
    )
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "whale_open"
    assert event.direction == "yes"
    assert event.size_delta_usdc == pytest.approx(15_000.0)
    assert event.prior_size_usdc == pytest.approx(0.0)
    assert event.current_size_usdc == pytest.approx(15_000.0)
    assert event.severity == pytest.approx(1.5)


def test_resize_above_absolute_threshold_emits_event() -> None:
    events = detect_whale_flow(
        current=[_snap("0xw", size_usdc=50_000.0)],
        prior=[_snap("0xw", size_usdc=30_000.0)],
        qualified_wallets={"0xw"},
        observed_at=NOW,
        thresholds=THRESHOLDS,
    )
    assert len(events) == 1
    assert events[0].event_type == "whale_resize"
    assert events[0].size_delta_usdc == pytest.approx(20_000.0)


def test_resize_under_absolute_but_over_pct_emits_event() -> None:
    # Prior 2_000, current 3_000 → delta 1_000 (< 10k) but = 33% of 3_000
    # which is above resize_pct=20%.
    events = detect_whale_flow(
        current=[_snap("0xw", size_usdc=3_000.0)],
        prior=[_snap("0xw", size_usdc=2_000.0)],
        qualified_wallets={"0xw"},
        observed_at=NOW,
        thresholds=THRESHOLDS,
    )
    assert len(events) == 1
    assert events[0].event_type == "whale_resize"


def test_new_dust_open_is_suppressed_by_min_reference_floor() -> None:
    # A wallet opens a brand-new 500 USDC position. The resize_pct rule
    # would trivially qualify (100% of itself > 20%) but the min_reference
    # floor of 1_000 USDC filters the dust.
    events = detect_whale_flow(
        current=[_snap("0xw", size_usdc=500.0)],
        prior=[],
        qualified_wallets={"0xw"},
        observed_at=NOW,
        thresholds=THRESHOLDS,
    )
    assert events == []


def test_small_move_under_both_thresholds_is_suppressed() -> None:
    # Delta 200 USDC = 10% of 2_000 — under both limits.
    events = detect_whale_flow(
        current=[_snap("0xw", size_usdc=2_200.0)],
        prior=[_snap("0xw", size_usdc=2_000.0)],
        qualified_wallets={"0xw"},
        observed_at=NOW,
        thresholds=THRESHOLDS,
    )
    assert events == []


def test_close_emits_whale_close() -> None:
    events = detect_whale_flow(
        current=[],
        prior=[_snap("0xw", size_usdc=40_000.0)],
        qualified_wallets={"0xw"},
        observed_at=NOW,
        thresholds=THRESHOLDS,
    )
    assert len(events) == 1
    assert events[0].event_type == "whale_close"
    assert events[0].current_size_usdc == pytest.approx(0.0)
    assert events[0].size_delta_usdc == pytest.approx(-40_000.0)


def test_unqualified_wallet_moves_do_not_emit() -> None:
    events = detect_whale_flow(
        current=[_snap("0xwash", size_usdc=100_000.0)],
        prior=[],
        qualified_wallets=set(),  # 0xwash is not qualified
        observed_at=NOW,
        thresholds=THRESHOLDS,
    )
    assert events == []


def test_independent_legs_emit_separately() -> None:
    # One YES leg opens, one NO leg also opens — two distinct events.
    events = detect_whale_flow(
        current=[
            _snap("0xw", size_usdc=12_000.0, outcome="YES"),
            _snap("0xw", size_usdc=15_000.0, outcome="NO"),
        ],
        prior=[],
        qualified_wallets={"0xw"},
        observed_at=NOW,
        thresholds=THRESHOLDS,
    )
    assert len(events) == 2
    directions = {event.direction for event in events}
    assert directions == {"yes", "no"}


def test_events_sorted_by_severity_desc() -> None:
    events = detect_whale_flow(
        current=[
            _snap("0xa", size_usdc=11_000.0),
            _snap("0xb", condition_id="cond-2", size_usdc=30_000.0),
        ],
        prior=[],
        qualified_wallets={"0xa", "0xb"},
        observed_at=NOW,
        thresholds=THRESHOLDS,
    )
    assert [event.proxy_wallet for event in events] == ["0xb", "0xa"]


def test_positions_from_polymarket_skips_zero_sizes() -> None:
    positions = [
        Position.model_validate(
            {
                "proxyWallet": "0xw",
                "conditionId": "cond-1",
                "asset": "tok-y",
                "outcome": "yes",
                "size": 5.0,
                "avgPrice": 0.4,
                "currentValue": 2.0,
                "cashPnl": 0.0,
            }
        ),
        Position.model_validate(
            {
                "proxyWallet": "0xw",
                "conditionId": "cond-2",
                "asset": "tok-n",
                "outcome": "no",
                "size": 0.0,
                "avgPrice": 0.5,
                "currentValue": 0.0,
                "cashPnl": 0.0,
            }
        ),
    ]
    snapshots = positions_from_polymarket(positions)
    assert len(snapshots) == 1
    assert snapshots[0].outcome == "YES"
