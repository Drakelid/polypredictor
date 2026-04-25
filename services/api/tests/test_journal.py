from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.journal import (
    JournalCall,
    JournalResolutionSync,
    _brier_contribution,
    _confidence_buckets,
    _directional_fill_call,
    _pnl_usdc,
    summary,
)


def _call(
    *,
    id: str,
    outcome: str,
    model_prob_at_call: float,
    resolved_outcome: str | None,
    pnl_usdc: float | None,
    brier_contribution: float | None,
    predicted_edge_bps: float,
) -> JournalCall:
    confidence = model_prob_at_call if outcome == "YES" else 1.0 - model_prob_at_call
    return JournalCall(
        id=id,
        condition_id=f"cond-{id}",
        outcome=outcome,
        side="BUY",
        size_usdc=100.0,
        entry_price=0.45,
        model_prob_at_call=model_prob_at_call,
        model_band_lo_at_call=model_prob_at_call,
        model_band_hi_at_call=model_prob_at_call,
        market_mid_at_call=0.45,
        created_at=datetime(2026, 4, 23, tzinfo=UTC),
        resolved_outcome=resolved_outcome,
        resolved_at=datetime(2026, 4, 24, tzinfo=UTC) if resolved_outcome else None,
        pnl_usdc=pnl_usdc,
        brier_contribution=brier_contribution,
        predicted_edge_bps=predicted_edge_bps,
        realized_edge_bps=(pnl_usdc / 100.0) * 10_000.0 if pnl_usdc is not None else None,
        call_confidence=confidence,
    )


def test_pnl_usdc_for_buy_yes_and_buy_no() -> None:
    yes_win = _pnl_usdc(
        side="BUY",
        outcome="YES",
        entry_price=0.40,
        size_usdc=100.0,
        resolved_outcome="YES",
    )
    no_loss = _pnl_usdc(
        side="BUY",
        outcome="NO",
        entry_price=0.55,
        size_usdc=100.0,
        resolved_outcome="YES",
    )

    assert yes_win == pytest.approx(150.0)
    assert no_loss == pytest.approx(-100.0)


def test_brier_contribution_uses_yes_probability() -> None:
    assert _brier_contribution(0.7, "YES") == pytest.approx(0.09)
    assert _brier_contribution(0.7, "NO") == pytest.approx(0.49)


def test_confidence_buckets_group_by_call_side_probability() -> None:
    buckets = _confidence_buckets(
        [
            _call(
                id="a",
                outcome="YES",
                model_prob_at_call=0.72,
                resolved_outcome="YES",
                pnl_usdc=20.0,
                brier_contribution=0.08,
                predicted_edge_bps=300.0,
            ),
            _call(
                id="b",
                outcome="NO",
                model_prob_at_call=0.28,
                resolved_outcome="NO",
                pnl_usdc=15.0,
                brier_contribution=0.08,
                predicted_edge_bps=250.0,
            ),
        ]
    )

    seventy_bucket = next(bucket for bucket in buckets if bucket["label"] == "70-80%")
    assert seventy_bucket["count"] == 2
    assert seventy_bucket["hit_rate"] == pytest.approx(1.0)


def test_directional_fill_call_maps_sell_yes_to_no() -> None:
    directional = _directional_fill_call(
        token_ids=["tok-yes", "tok-no"],
        token_id="tok-yes",
        side="SELL",
        price=0.63,
        size=100.0,
    )

    assert directional is not None
    assert directional["outcome"] == "NO"
    assert directional["token_id"] == "tok-no"
    assert directional["entry_price"] == pytest.approx(0.37)
    assert directional["size_usdc"] == pytest.approx(63.0)


@pytest.mark.asyncio
async def test_summary_aggregates_resolved_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = [
        _call(
            id="a",
            outcome="YES",
            model_prob_at_call=0.72,
            resolved_outcome="YES",
            pnl_usdc=20.0,
            brier_contribution=0.08,
            predicted_edge_bps=300.0,
        ),
        _call(
            id="b",
            outcome="NO",
            model_prob_at_call=0.35,
            resolved_outcome="NO",
            pnl_usdc=15.0,
            brier_contribution=0.12,
            predicted_edge_bps=180.0,
        ),
        _call(
            id="c",
            outcome="YES",
            model_prob_at_call=0.55,
            resolved_outcome=None,
            pnl_usdc=None,
            brier_contribution=None,
            predicted_edge_bps=50.0,
        ),
    ]

    async def fake_list_calls(*, pool, ch, settings):
        return calls

    async def fake_resolution_sync(**_: object) -> None:
        return None

    monkeypatch.setattr("api.journal.list_calls", fake_list_calls)
    monkeypatch.setattr("api.journal._journal_resolution_sync", fake_resolution_sync)

    report = await summary(pool=object(), ch=object(), settings=object())  # type: ignore[arg-type]

    assert report.total_calls == 3
    assert report.resolved_calls == 2
    assert report.unresolved_calls == 1
    assert report.total_pnl_usdc == pytest.approx(35.0)
    assert report.avg_brier == pytest.approx(0.10)
    assert report.best_calls[0].id == "a"
    assert len(report.edge_scatter) == 2
    assert report.resolution_sync is None


@pytest.mark.asyncio
async def test_summary_includes_resolution_sync_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_calls(*, pool, ch, settings):
        return []

    async def fake_resolution_sync(*, pool, settings):
        del pool, settings
        return JournalResolutionSync(
            proxy_wallet="0xabcdefabcdefabcdefabcdefabcdefabcdef1234",
            verified_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
            open_positions=4,
            redeemable_positions=2,
            total_position_value_usdc=250.0,
            total_earnings_usdc=18.5,
        )

    monkeypatch.setattr("api.journal.list_calls", fake_list_calls)
    monkeypatch.setattr("api.journal._journal_resolution_sync", fake_resolution_sync)

    report = await summary(pool=object(), ch=object(), settings=object())  # type: ignore[arg-type]

    assert report.resolution_sync is not None
    assert report.resolution_sync.redeemable_positions == 2
    assert report.resolution_sync.total_earnings_usdc == pytest.approx(18.5)
