from __future__ import annotations

from datetime import UTC, datetime

import pytest
from model import (
    MarketType,
    SiblingMarket,
    SiblingQuote,
    build_multi_outcome_groups,
    build_threshold_pairs,
    detect_multi_outcome_arbs,
    detect_threshold_arbs,
    sibling_prior_for_market,
)


def _threshold(
    condition_id: str,
    *,
    strike: float,
    resolution_date: datetime,
    direction: str = "above",
    mid: float | None = None,
    best_bid: float | None = None,
    best_ask: float | None = None,
    bid_size: float | None = None,
    ask_size: float | None = None,
) -> SiblingMarket:
    return SiblingMarket(
        condition_id=condition_id,
        question=f"{condition_id}?",
        event_id=None,
        market_type=MarketType.THRESHOLD,
        asset="BTC",
        direction=direction,
        strike=strike,
        resolution_date=resolution_date,
        quote=SiblingQuote(
            mid=mid,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size=bid_size,
            ask_size=ask_size,
        ),
    )


def test_build_threshold_pairs_orders_looser_and_stricter_markets() -> None:
    june = datetime(2026, 6, 30, tzinfo=UTC)
    july = datetime(2026, 7, 31, tzinfo=UTC)
    pairs = build_threshold_pairs(
        [
            _threshold("btc-150k-june", strike=150_000, resolution_date=june),
            _threshold("btc-140k-june", strike=140_000, resolution_date=june),
            _threshold("btc-150k-july", strike=150_000, resolution_date=july),
        ]
    )

    assert {(pair.relation_type, pair.looser.condition_id, pair.stricter.condition_id) for pair in pairs} == {
        ("same_date_threshold", "btc-140k-june", "btc-150k-june"),
        ("same_threshold_date", "btc-150k-july", "btc-150k-june"),
    }


def test_sibling_prior_uses_stricter_as_lower_bound_and_looser_as_upper_bound() -> None:
    date = datetime(2026, 6, 30, tzinfo=UTC)
    target = _threshold("btc-150k", strike=150_000, resolution_date=date, mid=0.40)
    looser = _threshold("btc-140k", strike=140_000, resolution_date=date, mid=0.55)
    stricter = _threshold("btc-160k", strike=160_000, resolution_date=date, mid=0.28)

    prior = sibling_prior_for_market(
        target,
        build_threshold_pairs([target, looser, stricter]),
    )

    assert prior is not None
    assert prior.lower_bound == pytest.approx(0.28)
    assert prior.upper_bound == pytest.approx(0.55)
    assert prior.implied_prior == pytest.approx(0.415)
    assert prior.support_conditions == ("btc-140k", "btc-160k")


def test_detect_threshold_arbs_uses_top_of_book_and_fee() -> None:
    date = datetime(2026, 6, 30, tzinfo=UTC)
    looser = _threshold(
        "btc-140k",
        strike=140_000,
        resolution_date=date,
        mid=0.52,
        best_ask=0.53,
        ask_size=400.0,
    )
    stricter = _threshold(
        "btc-150k",
        strike=150_000,
        resolution_date=date,
        mid=0.60,
        best_bid=0.60,
        bid_size=250.0,
    )

    violations = detect_threshold_arbs(
        build_threshold_pairs([looser, stricter]),
        event_time=date,
        taker_fee_bps=10.0,
    )

    assert len(violations) == 1
    violation = violations[0]
    assert violation.condition_id == "btc-150k"
    assert violation.locked_profit_per_share == pytest.approx(0.06887, abs=1e-4)
    assert violation.implied_size == pytest.approx(250.0)
    assert violation.implied_profit_usdc == pytest.approx(17.2175, abs=1e-3)


def test_detect_multi_outcome_arbs_flags_underpriced_basket() -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    group = build_multi_outcome_groups(
        [
            SiblingMarket(
                condition_id="leg-a",
                question="A",
                event_id="evt-1",
                market_type=MarketType.MULTI_OUTCOME,
                quote=SiblingQuote(mid=0.2, best_bid=0.18, best_ask=0.20, ask_size=100.0),
            ),
            SiblingMarket(
                condition_id="leg-b",
                question="B",
                event_id="evt-1",
                market_type=MarketType.MULTI_OUTCOME,
                quote=SiblingQuote(mid=0.3, best_bid=0.28, best_ask=0.30, ask_size=90.0),
            ),
            SiblingMarket(
                condition_id="leg-c",
                question="C",
                event_id="evt-1",
                market_type=MarketType.MULTI_OUTCOME,
                quote=SiblingQuote(mid=0.1, best_bid=0.09, best_ask=0.10, ask_size=80.0),
            ),
        ]
    )

    violations = detect_multi_outcome_arbs(group, event_time=now, taker_fee_bps=0.0)

    assert len(violations) == 1
    violation = violations[0]
    assert violation.relation_type == "multi_outcome_basket"
    assert violation.locked_profit_per_share == pytest.approx(0.40)
    assert violation.implied_size == pytest.approx(80.0)
    assert violation.payload["condition_ids"] == ["leg-a", "leg-b", "leg-c"]
