from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.tuning import (
    TuningAdjustmentContext,
    TuningProfile,
    TuningProfileInput,
    apply_tuning_adjustment,
    get_active_profile,
    update_active_profile,
)


class _FakeConn:
    def __init__(self, rows: list[object | None]) -> None:
        self.rows = rows
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object):
        self.fetchrow_calls.append((query, args))
        return self.rows.pop(0) if self.rows else None

    async def execute(self, query: str, *args: object):
        self.execute_calls.append((query, args))
        return "OK"


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
async def test_get_active_profile_defaults_to_balanced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.tuning.ensure_user_by_email", fake_ensure_user_by_email
    )
    pool = _FakePool(_FakeConn([None]))

    profile = await get_active_profile(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
    )

    assert profile.preset == "balanced"
    assert profile.name == "Balanced"
    assert profile.log_odds_shifts["resolution_risk"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_update_active_profile_persists_named_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 4, 24, 12, tzinfo=UTC)

    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.tuning.ensure_user_by_email", fake_ensure_user_by_email
    )
    conn = _FakeConn(
        [
            {
                "name": "Conservative",
                "preset": "conservative",
                "log_odds_shifts": {
                    "smart_money": 0.10,
                    "sibling_prior": 0.10,
                    "holder_concentration": 0.25,
                    "resolution_risk": 0.45,
                    "adversarial_flow": 0.35,
                },
                "is_active": True,
                "updated_at": now,
            }
        ]
    )
    pool = _FakePool(conn)

    profile = await update_active_profile(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        payload=TuningProfileInput(preset="conservative", log_odds_shifts={}),
    )

    assert profile.name == "Conservative"
    assert profile.preset == "conservative"
    assert conn.execute_calls
    insert_args = conn.fetchrow_calls[0][1]
    assert insert_args[1] == "Conservative"
    assert insert_args[2] == "conservative"


def test_apply_tuning_adjustment_adds_directional_and_caution_log_odds() -> None:
    profile = TuningProfile(
        name="Custom",
        preset="custom",
        log_odds_shifts={
            "smart_money": 0.4,
            "sibling_prior": 0.2,
            "holder_concentration": 0.0,
            "resolution_risk": 0.3,
            "adversarial_flow": 0.0,
        },
        is_active=True,
        updated_at=None,
    )

    adjustment = apply_tuning_adjustment(
        profile=profile,
        context=TuningAdjustmentContext(
            model_prob=0.60,
            smart_money_consensus=0.80,
            smart_money_dominant="YES",
            sibling_implied_prior=0.65,
            resolution_risk_score=0.50,
        ),
        concentration_threshold=0.60,
    )

    assert adjustment.tuned_probability is not None
    assert adjustment.tuned_probability > 0.60
    assert adjustment.total_log_odds_shift > 0
    assert "smart money" in adjustment.reasons[0].lower()


def test_apply_tuning_adjustment_can_pull_probabilities_toward_neutral() -> None:
    profile = TuningProfile(
        name="Conservative",
        preset="conservative",
        log_odds_shifts={
            "smart_money": 0.0,
            "sibling_prior": 0.0,
            "holder_concentration": 0.0,
            "resolution_risk": 0.5,
            "adversarial_flow": 0.5,
        },
        is_active=True,
        updated_at=None,
    )

    adjustment = apply_tuning_adjustment(
        profile=profile,
        context=TuningAdjustmentContext(
            model_prob=0.72,
            resolution_risk_score=0.80,
            adversarial_flow_score=0.60,
        ),
        concentration_threshold=0.60,
    )

    assert adjustment.tuned_probability is not None
    assert adjustment.tuned_probability < 0.72
    assert adjustment.total_log_odds_shift < 0
