"""Tests for the smart-money x concentration interaction at the API layer.

The PRD calls for the ensemble feature ``smart_money_consensus`` to be
down-weighted when ``holder_concentration > 0.6``. This is implemented as a
linear attenuation in :func:`api.markets._smart_money_consensus_feature`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.concentration import MarketConcentration
from api.markets import _concentration_score, _smart_money_consensus_feature
from api.smart_money import SmartMoneyPerMarket


def _smart_money(consensus: float = 0.6) -> SmartMoneyPerMarket:
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)
    return SmartMoneyPerMarket(
        condition_id="cond-1",
        yes_wallets=4,
        no_wallets=1,
        yes_size=400.0,
        no_size=100.0,
        yes_size_usdc=200.0,
        no_size_usdc=50.0,
        yes_avg_entry=0.55,
        no_avg_entry=0.40,
        net_size_usdc=150.0,
        consensus_score=consensus,
        sample_wallets=5,
        dominant_outcome="YES",
        event_time=now,
        observed_at=now,
    )


def _concentration(max_gini: float | None = 0.5) -> MarketConcentration:
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)
    return MarketConcentration(
        condition_id="cond-1",
        yes_gini=max_gini,
        no_gini=0.0 if max_gini is not None else None,
        yes_top1_pct=0.4,
        no_top1_pct=0.2,
        yes_top5_pct=0.7,
        no_top5_pct=0.5,
        yes_whale_flag=(max_gini is not None and max_gini > 0.6),
        no_whale_flag=False,
        any_whale_flag=(max_gini is not None and max_gini > 0.6),
        yes_holders_count=10,
        no_holders_count=5,
        max_gini=max_gini,
        event_time=now,
        observed_at=now,
    )


def test_feature_is_none_when_smart_money_absent() -> None:
    assert (
        _smart_money_consensus_feature(
            None, _concentration(), concentration_threshold=0.6
        )
        is None
    )


def test_feature_unchanged_when_concentration_below_threshold() -> None:
    result = _smart_money_consensus_feature(
        _smart_money(consensus=0.5),
        _concentration(max_gini=0.3),
        concentration_threshold=0.6,
    )
    assert result == pytest.approx(0.5)


def test_feature_unchanged_when_concentration_exactly_at_threshold() -> None:
    result = _smart_money_consensus_feature(
        _smart_money(consensus=0.8),
        _concentration(max_gini=0.6),
        concentration_threshold=0.6,
    )
    assert result == pytest.approx(0.8)


def test_feature_attenuated_linearly_above_threshold() -> None:
    # Threshold 0.6, score 0.8 — halfway from threshold to 1.0, so attenuation
    # to half the raw consensus.
    result = _smart_money_consensus_feature(
        _smart_money(consensus=1.0),
        _concentration(max_gini=0.8),
        concentration_threshold=0.6,
    )
    assert result == pytest.approx(0.5)


def test_feature_fully_attenuated_at_max_concentration() -> None:
    result = _smart_money_consensus_feature(
        _smart_money(consensus=1.0),
        _concentration(max_gini=1.0),
        concentration_threshold=0.6,
    )
    assert result == pytest.approx(0.0)


def test_feature_passes_raw_when_concentration_missing() -> None:
    result = _smart_money_consensus_feature(
        _smart_money(consensus=0.3),
        None,
        concentration_threshold=0.6,
    )
    assert result == pytest.approx(0.3)


def test_concentration_score_prefers_max_gini() -> None:
    assert _concentration_score(_concentration(max_gini=0.72)) == pytest.approx(0.72)


def test_concentration_score_none_when_absent() -> None:
    assert _concentration_score(None) is None
