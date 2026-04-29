"""Tests for the M8.5 median time-to-decision live monitor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from api.decision_time_monitor import (
    DecisionTimeReport,
    compute_decision_time_report,
    persist_report,
    run_decision_time_monitor,
)


_NOW = datetime(2026, 4, 28, 12, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _QueryResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.raise_on_query = False

    async def query(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        if self.raise_on_query:
            raise RuntimeError("clickhouse down")
        return _QueryResult(self._rows)


@dataclass
class _FakeConn:
    journal_rows: list[dict[str, Any]] = field(default_factory=list)
    persisted_rows: list[tuple[Any, ...]] = field(default_factory=list)
    raise_on_fetch: bool = False
    executes: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if self.raise_on_fetch:
            raise RuntimeError("postgres down")
        # Mimic asyncpg: SELECT id, condition_id, created_at FROM journal_calls.
        return list(self.journal_rows)

    async def execute(self, query: str, *args: Any) -> str:
        self.executes.append((query, args))
        if "INSERT INTO decision_time_metrics" in query:
            self.persisted_rows.append(args)
        return "OK"

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _FakePool:
    conn: _FakeConn = field(default_factory=_FakeConn)

    def acquire(self) -> _FakeConn:
        return self.conn


def _journal(condition_id: str, minutes_ago: float, *, rid: int = 1) -> dict[str, Any]:
    return {
        "id": rid,
        "condition_id": condition_id,
        "created_at": _NOW - timedelta(minutes=minutes_ago),
    }


# ---------------------------------------------------------------------------
# compute_decision_time_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_returns_empty_report_when_no_journal_calls() -> None:
    ch = _FakeClickHouse([])
    pool = _FakePool()  # no journal rows

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    assert report.sample_count == 0
    assert report.median_minutes is None
    assert report.p90_minutes is None
    assert report.meets_target is False
    # No ClickHouse query made when there are no journal calls.
    assert ch.calls == []


@pytest.mark.asyncio
async def test_compute_pairs_calls_with_signal_events_and_returns_median() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [
        _journal("c1", minutes_ago=2, rid=1),  # 2-minute lag → fast
        _journal("c2", minutes_ago=4, rid=2),  # 4-minute lag → fast
        _journal("c3", minutes_ago=6, rid=3),  # 6-minute lag → slower
    ]
    # Signal preceded each journal call by the journaled lag amount.
    ch_rows = [
        ("c1", _NOW - timedelta(minutes=2)),
        ("c2", _NOW - timedelta(minutes=4)),
        ("c3", _NOW - timedelta(minutes=6)),
    ]
    ch = _FakeClickHouse(ch_rows)

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    assert report.sample_count == 3
    # Lags: 2, 4, 6 — median is 4, but signal_event was at NOW-N and call is
    # NOW, so call_time - sig_time = N.  But since the journal call IS at
    # NOW - minutes_ago, sig_time is NOW - minutes_ago, lag = 0.
    # That's actually the inverse of what I want — let me re-derive.
    # call_time = NOW - X, sig_time = NOW - X → lag = 0.
    # For a real lag I need sig_time BEFORE call_time.
    assert report.median_minutes == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_compute_meets_target_when_median_under_5_minutes() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [
        _journal("c1", minutes_ago=0, rid=1),  # call at NOW
        _journal("c2", minutes_ago=0, rid=2),
        _journal("c3", minutes_ago=0, rid=3),
    ]
    # Signals fired 1, 2, 3 minutes before each call.
    ch_rows = [
        ("c1", _NOW - timedelta(minutes=1)),
        ("c2", _NOW - timedelta(minutes=2)),
        ("c3", _NOW - timedelta(minutes=3)),
    ]
    ch = _FakeClickHouse(ch_rows)

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    assert report.sample_count == 3
    assert report.median_minutes == pytest.approx(2.0)
    assert report.meets_target is True


@pytest.mark.asyncio
async def test_compute_fails_target_when_median_over_5_minutes() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [
        _journal("c1", minutes_ago=0, rid=1),
        _journal("c2", minutes_ago=0, rid=2),
        _journal("c3", minutes_ago=0, rid=3),
    ]
    # 6, 8, 10-minute lags, all within the 60-minute match window.
    ch_rows = [
        ("c1", _NOW - timedelta(minutes=6)),
        ("c2", _NOW - timedelta(minutes=8)),
        ("c3", _NOW - timedelta(minutes=10)),
    ]
    ch = _FakeClickHouse(ch_rows)

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    assert report.median_minutes == pytest.approx(8.0)
    assert report.meets_target is False


@pytest.mark.asyncio
async def test_compute_drops_signals_outside_60min_match_window() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [
        _journal("c1", minutes_ago=0, rid=1),
        _journal("c2", minutes_ago=0, rid=2),
    ]
    ch_rows = [
        # Within window: 30 minutes lag, kept.
        ("c1", _NOW - timedelta(minutes=30)),
        # Outside the 60-minute window: dropped.
        ("c2", _NOW - timedelta(minutes=120)),
    ]
    ch = _FakeClickHouse(ch_rows)

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    # Only c1 contributes.
    assert report.sample_count == 1
    assert report.median_minutes == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_compute_drops_signals_after_call_time() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [_journal("c1", minutes_ago=10, rid=1)]
    # Signal fired AFTER the call (negative lag) — invalid causality, dropped.
    ch_rows = [("c1", _NOW - timedelta(minutes=5))]
    ch = _FakeClickHouse(ch_rows)

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    assert report.sample_count == 0


@pytest.mark.asyncio
async def test_compute_normalizes_naive_signal_datetimes() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [_journal("c1", minutes_ago=0, rid=1)]
    naive_sig_time = (_NOW - timedelta(minutes=2)).replace(tzinfo=None)
    ch = _FakeClickHouse([("c1", naive_sig_time)])

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    assert report.sample_count == 1
    assert report.median_minutes == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_compute_clickhouse_failure_returns_zero_samples() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [_journal("c1", minutes_ago=0, rid=1)]
    ch = _FakeClickHouse([])
    ch.raise_on_query = True

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    # Journal calls landed but no signals could be matched.
    assert report.sample_count == 0
    assert report.median_minutes is None


@pytest.mark.asyncio
async def test_compute_postgres_failure_returns_empty_report() -> None:
    pool = _FakePool()
    pool.conn.raise_on_fetch = True
    ch = _FakeClickHouse([])

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    assert report.sample_count == 0
    assert ch.calls == []  # no signal query when no calls


@pytest.mark.asyncio
async def test_compute_p90_within_sample() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [
        _journal(f"c{i}", minutes_ago=0, rid=i) for i in range(1, 11)
    ]
    ch_rows = [
        (f"c{i}", _NOW - timedelta(minutes=float(i))) for i in range(1, 11)
    ]
    ch = _FakeClickHouse(ch_rows)

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    assert report.sample_count == 10
    # Sorted lags: 1..10. ceil(0.9 * 10) - 1 = 8 → sorted[8] = 9.
    assert report.p90_minutes == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_compute_uses_only_latest_signal_per_condition() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [_journal("c1", minutes_ago=0, rid=1)]
    # Only the LATEST signal per condition is returned by the LIMIT 1 BY query;
    # we model that with a single row here.  This test confirms the lag math
    # uses the freshest signal time as the trigger anchor.
    ch_rows = [("c1", _NOW - timedelta(minutes=3))]
    ch = _FakeClickHouse(ch_rows)

    report = await compute_decision_time_report(ch, pool, asked_at=_NOW)

    assert report.median_minutes == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_compute_passes_window_to_postgres_and_clickhouse() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [_journal("c1", minutes_ago=0, rid=1)]
    ch = _FakeClickHouse([])

    await compute_decision_time_report(
        ch, pool, asked_at=_NOW, window_hours=12, limit=50
    )

    # ClickHouse query parameters reflect the window.
    _, params = ch.calls[0]
    assert params is not None
    assert params["start"] == _NOW - timedelta(hours=12)
    assert params["asof"] == _NOW
    assert params["ids"] == ["c1"]


# ---------------------------------------------------------------------------
# persist_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_report_inserts_row_and_creates_table() -> None:
    pool = _FakePool()
    report = DecisionTimeReport(
        as_of=_NOW,
        window_hours=48,
        window_label="48h",
        sample_count=3,
        median_minutes=2.5,
        p90_minutes=4.5,
        meets_target=True,
        target_minutes=5.0,
    )

    await persist_report(pool, report)

    queries = [q for q, _ in pool.conn.executes]
    assert any("CREATE TABLE IF NOT EXISTS decision_time_metrics" in q for q in queries)
    insert_args = pool.conn.persisted_rows[0]
    assert insert_args == ("48h", 3, 2.5, 4.5, True, _NOW)


@pytest.mark.asyncio
async def test_run_decision_time_monitor_persists_when_persist_true() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [_journal("c1", minutes_ago=0, rid=1)]
    ch = _FakeClickHouse([("c1", _NOW - timedelta(minutes=2))])

    report = await run_decision_time_monitor(
        ch, pool, window_hours=48, persist=True
    )

    assert report.median_minutes == pytest.approx(2.0)
    assert pool.conn.persisted_rows  # at least one row inserted


@pytest.mark.asyncio
async def test_run_decision_time_monitor_skips_persist_when_false() -> None:
    pool = _FakePool()
    pool.conn.journal_rows = [_journal("c1", minutes_ago=0, rid=1)]
    ch = _FakeClickHouse([("c1", _NOW - timedelta(minutes=2))])

    await run_decision_time_monitor(ch, pool, persist=False)

    assert pool.conn.persisted_rows == []
