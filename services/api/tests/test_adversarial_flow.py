from __future__ import annotations

from datetime import UTC, datetime

from api.adversarial_flow import (
    AdversarialFlowContext,
    QuoteDepthRow,
    build_adversarial_flow_context,
)
from api.features import FeatureSnapshotRow
from api.settings import Settings


def _feature_snapshot(
    *,
    informed_taker_flow_24h: float = 0.0,
    decayed_directional_flow_24h: float = 0.0,
) -> FeatureSnapshotRow:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    return FeatureSnapshotRow(
        condition_id="cond-1",
        token_id="tok-yes",
        mid=0.52,
        spread=0.03,
        book_imbalance_1pct=0.0,
        book_imbalance_5pct=0.0,
        momentum_1h=0.0,
        momentum_24h=0.0,
        momentum_7d=0.0,
        realized_vol_24h=0.3,
        informed_taker_flow_24h=informed_taker_flow_24h,
        passive_maker_flow_24h=0.0,
        decayed_directional_flow_24h=decayed_directional_flow_24h,
        event_time=observed_at,
        observed_at=observed_at,
    )


def _quote_depth(
    *,
    best_bid: float = 0.5,
    best_ask: float = 0.52,
    bid_size: float = 2000.0,
    ask_size: float = 2000.0,
) -> QuoteDepthRow:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    return QuoteDepthRow(
        token_id="tok-yes",
        best_bid=best_bid,
        best_ask=best_ask,
        bid_size=bid_size,
        ask_size=ask_size,
        event_time=observed_at,
        observed_at=observed_at,
    )


def test_build_adversarial_flow_context_flags_uncorroborated_thin_book_flow() -> None:
    settings = Settings(
        adversarial_flow_min_flow_strength=0.10,
        adversarial_flow_flag_threshold=0.65,
        thin_book_top_level_depth_threshold_usdc=1_000.0,
    )

    context = build_adversarial_flow_context(
        condition_id="cond-1",
        feature_snapshot=_feature_snapshot(
            informed_taker_flow_24h=0.48,
            decayed_directional_flow_24h=0.36,
        ),
        quote_depth=_quote_depth(bid_size=400.0, ask_size=400.0),
        external_divergence_count=1,
        external_event_count=0,
        settings=settings,
    )

    assert isinstance(context, AdversarialFlowContext)
    assert context.is_flagged is True
    assert context.thin_book is True
    assert context.score >= 0.65
    assert "large directional flow" in context.reasons
    assert "perps/options divergence not confirming the move" in context.reasons


def test_build_adversarial_flow_context_downweights_when_evidence_exists() -> None:
    settings = Settings(
        adversarial_flow_min_flow_strength=0.10,
        adversarial_flow_flag_threshold=0.65,
        thin_book_top_level_depth_threshold_usdc=1_000.0,
    )

    context = build_adversarial_flow_context(
        condition_id="cond-1",
        feature_snapshot=_feature_snapshot(
            informed_taker_flow_24h=0.22,
            decayed_directional_flow_24h=0.18,
        ),
        quote_depth=_quote_depth(bid_size=5000.0, ask_size=5000.0),
        external_divergence_count=0,
        external_event_count=3,
        settings=settings,
    )

    assert context.thin_book is False
    assert context.is_flagged is False
    assert context.score < 0.4
