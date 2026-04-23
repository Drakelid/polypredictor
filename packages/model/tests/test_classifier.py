"""Classifier tests — realistic Polymarket-style phrasings."""

from __future__ import annotations

from datetime import UTC, datetime

from model import MarketType, classify


def _end(year: int = 2026, month: int = 6, day: int = 30) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def test_threshold_btc_above_dollar_magnitude() -> None:
    r = classify(
        question="Will BTC reach $150k by June 30, 2026?",
        tags=["crypto"],
        end_date=_end(),
    )
    assert r.market_type is MarketType.THRESHOLD
    assert r.features.asset == "BTC"
    assert r.features.strike == 150_000.0
    assert r.features.direction == "above"


def test_threshold_eth_below_comma_number() -> None:
    r = classify(
        question="Will ETH close below $2,500 by Jul 1?",
        tags=["crypto"],
        end_date=_end(year=2026, month=7, day=1),
    )
    assert r.market_type is MarketType.THRESHOLD
    assert r.features.asset == "ETH"
    assert r.features.strike == 2_500.0
    assert r.features.direction == "below"


def test_range_btc_between() -> None:
    r = classify(
        question="Will BTC close between $100k and $120k on June 30, 2026?",
        tags=["crypto"],
        end_date=_end(),
    )
    assert r.market_type is MarketType.RANGE
    assert r.features.range_low == 100_000.0
    assert r.features.range_high == 120_000.0


def test_discrete_event_fomc() -> None:
    r = classify(
        question="Will the Fed cut 25bps at the June FOMC?",
        tags=["finance"],
        end_date=_end(),
    )
    assert r.market_type is MarketType.DISCRETE_EVENT
    assert r.features.event_family == "fomc"


def test_discrete_event_cpi_with_strike() -> None:
    r = classify(
        question="Will CPI print below 2.8% in June?",
        tags=["finance"],
        end_date=_end(),
    )
    # Even though there's a threshold-like strike, event_family wins — this
    # market is driven by consensus/OIS priors, not Black-Scholes on spot.
    assert r.market_type is MarketType.DISCRETE_EVENT
    assert r.features.event_family == "cpi"
    assert r.features.strike == 2.8


def test_multi_outcome_via_sibling_count() -> None:
    r = classify(
        question="Which L2 hits $10B TVL first?",
        tags=["crypto"],
        multi_outcome_sibling_count=5,
    )
    assert r.market_type is MarketType.MULTI_OUTCOME
    assert r.features.is_multi_outcome_sibling is True


def test_multi_outcome_via_outcomes_list() -> None:
    r = classify(
        question="Who wins the ETH ETF flow race?",
        tags=["crypto"],
        outcomes=["BlackRock", "Fidelity", "Grayscale", "VanEck"],
    )
    assert r.market_type is MarketType.MULTI_OUTCOME


def test_long_tail_binary_fallback() -> None:
    r = classify(
        question="Will Coinbase be hacked in 2026?",
        tags=["crypto"],
        end_date=_end(year=2026, month=12, day=31),
    )
    assert r.market_type is MarketType.LONG_TAIL_BINARY
    assert r.confidence >= 0.5


def test_misc_for_untagged_garbage() -> None:
    r = classify(question="a market without any recognizable structure")
    assert r.market_type is MarketType.MISC
    assert r.needs_review


def test_needs_review_for_low_confidence_or_misc() -> None:
    low = classify(question="Will something happen?")
    # "Will" triggers long-tail binary with ~0.6 confidence — not flagged.
    assert low.market_type is MarketType.LONG_TAIL_BINARY
    assert not low.needs_review

    misc = classify(question="vague blob")
    assert misc.needs_review
