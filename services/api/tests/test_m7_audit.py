from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api import m7_audit
from api.backtest_walk_forward import ReplaySampleRow
from model import MarketType


class _FakeConn:
    def __init__(self, row) -> None:
        self.row = row

    async def fetchrow(self, query: str, *args: object):
        del query, args
        return self.row


class _AcquireCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self.conn)


@pytest.mark.asyncio
async def test_journal_round_trip_audit_flags_complete_auto_wss_flow() -> None:
    pool = _FakePool(
        _FakeConn(
            {
                "total_auto_wss_calls": 5,
                "resolved_auto_wss_calls": 3,
                "unresolved_auto_wss_calls": 2,
                "scored_auto_wss_calls": 3,
                "missing_model_snapshot_calls": 0,
                "missing_resolution_score_calls": 0,
            }
        )
    )

    audit = await m7_audit.journal_round_trip_audit(pool)  # type: ignore[arg-type]

    assert audit.meets_round_trip is True
    assert audit.scored_auto_wss_calls == 3


@pytest.mark.asyncio
async def test_tuning_reproducibility_audit_detects_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    replay_rows = [
        ReplaySampleRow(
            condition_id="cond-1",
            market_type=MarketType.THRESHOLD,
            regime="bull_trend",
            asked_at=asked_at,
            resolved_at=asked_at,
            predicted_prob=0.61,
            outcome=1,
            time_to_resolution_s=3600.0,
            band_lo=0.55,
            band_hi=0.67,
        )
    ]

    async def fake_get_active_profile(*, pool, settings):
        del pool, settings
        return type("Profile", (), {"name": "Balanced", "preset": "balanced"})()

    async def fake_replay_walk_forward_samples(
        ch,
        *,
        horizons_hours,
        resolved_from,
        resolved_to,
        limit,
        tuning_profile,
    ):
        del ch, horizons_hours, resolved_from, resolved_to, limit, tuning_profile
        return replay_rows, object(), []

    async def fake_model_for_market(
        ch,
        *,
        condition_id,
        asked_at,
        tuning_profile=None,
    ):
        del ch, condition_id, asked_at, tuning_profile
        return type(
            "Detail",
            (),
            {
                "model_prob": 0.61,
                "band_lo": 0.55,
                "band_hi": 0.67,
            },
        )()

    monkeypatch.setattr(m7_audit, "get_active_profile", fake_get_active_profile)
    monkeypatch.setattr(m7_audit, "replay_walk_forward_samples", fake_replay_walk_forward_samples)
    monkeypatch.setattr(m7_audit, "model_for_market", fake_model_for_market)

    audit = await m7_audit.tuning_reproducibility_audit(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        asked_at=asked_at,
    )

    assert audit.total_replay_samples == 1
    assert audit.mismatched_samples == 0
    assert audit.within_tolerance is True


@pytest.mark.asyncio
async def test_tuning_reproducibility_audit_detects_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    replay_rows = [
        ReplaySampleRow(
            condition_id="cond-1",
            market_type=MarketType.THRESHOLD,
            regime="bull_trend",
            asked_at=asked_at,
            resolved_at=asked_at,
            predicted_prob=0.61,
            outcome=1,
            time_to_resolution_s=3600.0,
            band_lo=0.55,
            band_hi=0.67,
        )
    ]

    async def fake_get_active_profile(*, pool, settings):
        del pool, settings
        return type("Profile", (), {"name": "Balanced", "preset": "balanced"})()

    async def fake_replay_walk_forward_samples(
        ch,
        *,
        horizons_hours,
        resolved_from,
        resolved_to,
        limit,
        tuning_profile,
    ):
        del ch, horizons_hours, resolved_from, resolved_to, limit, tuning_profile
        return replay_rows, object(), []

    async def fake_model_for_market(
        ch,
        *,
        condition_id,
        asked_at,
        tuning_profile=None,
    ):
        del ch, condition_id, asked_at, tuning_profile
        return type(
            "Detail",
            (),
            {
                "model_prob": 0.62,
                "band_lo": 0.55,
                "band_hi": 0.67,
            },
        )()

    monkeypatch.setattr(m7_audit, "get_active_profile", fake_get_active_profile)
    monkeypatch.setattr(m7_audit, "replay_walk_forward_samples", fake_replay_walk_forward_samples)
    monkeypatch.setattr(m7_audit, "model_for_market", fake_model_for_market)

    audit = await m7_audit.tuning_reproducibility_audit(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        asked_at=asked_at,
    )

    assert audit.mismatched_samples == 1
    assert audit.within_tolerance is False
