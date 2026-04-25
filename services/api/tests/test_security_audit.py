from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from api import security_audit


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.last_query: str | None = None

    async def fetch(self, query: str) -> list[dict[str, Any]]:
        self.last_query = query
        return self.rows

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeConn:
        return self._conn


def _row(provider: str, rotated_days_ago: float | None, created_days_ago: float = 365.0) -> dict[str, Any]:
    now = datetime(2026, 4, 25, 12, tzinfo=UTC)
    return {
        "provider": provider,
        "created_at": now - timedelta(days=created_days_ago),
        "rotated_at": (
            now - timedelta(days=rotated_days_ago) if rotated_days_ago is not None else None
        ),
    }


@pytest.mark.asyncio
async def test_rotation_audit_aggregates_per_provider() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [
        _row("polymarket_clob", rotated_days_ago=10),
        _row("polymarket_clob", rotated_days_ago=120),
        _row("polymarket_clob", rotated_days_ago=200),
        _row("alpaca", rotated_days_ago=15),
    ]
    pool = _FakePool(_FakeConn(rows))

    report = await security_audit.rotation_audit(
        pool=pool,  # type: ignore[arg-type]
        asked_at=asked_at,
        rotation_interval_days=90,
    )
    assert report.total_credentials == 4
    # Two polymarket_clob credentials are over 90d (120, 200); none for alpaca.
    assert report.needs_rotation_count == 2
    poly = next(p for p in report.providers if p.provider == "polymarket_clob")
    assert poly.total_credentials == 3
    assert poly.needs_rotation_count == 2
    # Oldest age in the polymarket_clob bucket is 200d.
    assert poly.oldest_rotation_age_days == pytest.approx(200.0, abs=0.01)
    alpaca = next(p for p in report.providers if p.provider == "alpaca")
    assert alpaca.needs_rotation_count == 0


@pytest.mark.asyncio
async def test_rotation_audit_treats_null_rotated_at_as_needs_rotation_via_created_at() -> None:
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    # rotated_at = None; created_at fallback is 200d ago → over the 90d window.
    rows = [_row("polymarket_clob", rotated_days_ago=None, created_days_ago=200.0)]
    pool = _FakePool(_FakeConn(rows))

    report = await security_audit.rotation_audit(
        pool=pool,  # type: ignore[arg-type]
        asked_at=asked_at,
        rotation_interval_days=90,
    )
    assert report.needs_rotation_count == 1


@pytest.mark.asyncio
async def test_rotation_audit_does_not_leak_user_id() -> None:
    """The audit must produce only aggregate counts; user_id must never
    appear in the response (privacy invariant from PRD §6 / cross-cutting)."""
    asked_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    rows = [_row("polymarket_clob", rotated_days_ago=10)]
    pool = _FakePool(_FakeConn(rows))

    report = await security_audit.rotation_audit(
        pool=pool,  # type: ignore[arg-type]
        asked_at=asked_at,
    )
    # Walk every dataclass field and confirm none reference user_id.
    payload = repr(report).lower()
    assert "user_id" not in payload
    assert "user-id" not in payload


@pytest.mark.asyncio
async def test_rotation_audit_zero_interval_rejected() -> None:
    pool = _FakePool(_FakeConn([]))
    with pytest.raises(ValueError):
        await security_audit.rotation_audit(
            pool=pool,  # type: ignore[arg-type]
            asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
            rotation_interval_days=0,
        )


@pytest.mark.asyncio
async def test_rotation_audit_empty_table_returns_zero_counts() -> None:
    pool = _FakePool(_FakeConn([]))
    report = await security_audit.rotation_audit(
        pool=pool,  # type: ignore[arg-type]
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert report.total_credentials == 0
    assert report.needs_rotation_count == 0
    assert report.providers == []
