from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from api import privacy_audit


class _FakeRecord:
    """Mimic asyncpg.Record's ``keys()`` + ``__getitem__`` interface."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def keys(self) -> list[str]:
        return list(self._payload.keys())

    def __getitem__(self, key: str) -> Any:
        return self._payload[key]


class _FakeConn:
    def __init__(self, rows: list[_FakeRecord]) -> None:
        self.rows = rows

    async def fetch(self, query: str) -> list[_FakeRecord]:
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


def _good_row(*, users: int = 10, calls: int = 100) -> _FakeRecord:
    return _FakeRecord(
        {
            "cohort_kind": "confidence_bucket",
            "bucket_label": "70-80%",
            "source_user_count": users,
            "raw_call_count": calls,
            "noisy_call_count": float(calls + 2),
            "raw_hit_rate": 0.74,
            "noisy_hit_rate": 0.73,
            "epsilon": 1.0,
            "min_users": 5,
            "min_calls": 20,
            "lookback_start": datetime(2026, 1, 1, tzinfo=UTC),
            "lookback_end": datetime(2026, 4, 25, tzinfo=UTC),
            "observed_at": datetime(2026, 4, 25, tzinfo=UTC),
        }
    )


@pytest.mark.asyncio
async def test_privacy_audit_passes_when_rows_clean() -> None:
    pool = _FakePool(_FakeConn([_good_row(), _good_row(users=8, calls=80)]))
    report = await privacy_audit.run_privacy_audit(
        pool=pool,  # type: ignore[arg-type]
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert report.invariants_held is True
    assert report.dp_aggregates.rows_inspected == 2
    assert report.dp_aggregates.schema_violations == 0
    assert report.dp_aggregates.k_anonymity_violations == 0
    assert report.dp_aggregates.weakest_user_count == 8
    assert report.dp_aggregates.weakest_call_count == 80


@pytest.mark.asyncio
async def test_privacy_audit_flags_user_id_leak() -> None:
    """A row with a stray ``user_id`` column must trip the schema check."""
    leaky = _good_row()
    leaky._payload["user_id"] = "alice"  # type: ignore[attr-defined]
    pool = _FakePool(_FakeConn([leaky]))

    report = await privacy_audit.run_privacy_audit(
        pool=pool,  # type: ignore[arg-type]
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert report.invariants_held is False
    assert report.dp_aggregates.schema_clean is False
    assert report.dp_aggregates.schema_violations == 1


@pytest.mark.asyncio
async def test_privacy_audit_flags_k_anonymity_violation() -> None:
    """A row with source_user_count < min_users must trip the k-anonymity check."""
    leaky = _good_row(users=2, calls=100)  # min_users=5 but only 2 users
    pool = _FakePool(_FakeConn([leaky]))

    report = await privacy_audit.run_privacy_audit(
        pool=pool,  # type: ignore[arg-type]
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert report.invariants_held is False
    assert report.dp_aggregates.k_anonymity_violations == 1
    assert report.dp_aggregates.schema_clean is True


@pytest.mark.asyncio
async def test_privacy_audit_passes_on_empty_table() -> None:
    """No DP rows persisted yet — vacuously holds."""
    pool = _FakePool(_FakeConn([]))
    report = await privacy_audit.run_privacy_audit(
        pool=pool,  # type: ignore[arg-type]
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    assert report.invariants_held is True
    assert report.dp_aggregates.rows_inspected == 0
    assert report.dp_aggregates.weakest_user_count is None


@pytest.mark.asyncio
async def test_report_to_dict_does_not_leak_payloads() -> None:
    """The serialized report must contain only counts/booleans, never any row
    contents — operators consume this; it must be safe to render publicly."""
    pool = _FakePool(_FakeConn([_good_row()]))
    report = await privacy_audit.run_privacy_audit(
        pool=pool,  # type: ignore[arg-type]
        asked_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
    )
    payload = privacy_audit.report_to_dict(report)
    flat = repr(payload).lower()
    # Bucket labels are templated category names like '70-80%', never user
    # identifiers — the audit reader must NOT reflect them out.
    assert "user_id" not in flat
    assert "alice" not in flat
    assert "bucket_label" not in flat
    assert payload["invariants_held"] is True
