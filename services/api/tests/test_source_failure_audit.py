"""Tests for the M5 P0/P1 source failure-rate audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from api.source_failure_audit import (
    DEFAULT_P0_P1_SOURCES,
    SourceFailureAuditReport,
    SourceFailureRow,
    _load_p0_p1_list,
    run_source_failure_audit,
)


_NOW = datetime(2026, 4, 28, 12, tzinfo=UTC)


class _QueryResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self._rows)


# ---------------------------------------------------------------------------
# _load_p0_p1_list
# ---------------------------------------------------------------------------


def test_load_p0_p1_list_returns_default_when_no_config() -> None:
    assert _load_p0_p1_list(None) == DEFAULT_P0_P1_SOURCES


def test_load_p0_p1_list_reads_json_array(tmp_path: Path) -> None:
    cfg = tmp_path / "p0p1.json"
    cfg.write_text(json.dumps(["custom_source", "another"]), encoding="utf-8")

    sources = _load_p0_p1_list(str(cfg))

    assert sources == ["custom_source", "another"]


def test_load_p0_p1_list_falls_back_on_malformed_file(tmp_path: Path) -> None:
    cfg = tmp_path / "broken.json"
    cfg.write_text("not json{{", encoding="utf-8")

    sources = _load_p0_p1_list(str(cfg))

    assert sources == DEFAULT_P0_P1_SOURCES


def test_load_p0_p1_list_falls_back_on_non_list_payload(tmp_path: Path) -> None:
    cfg = tmp_path / "obj.json"
    cfg.write_text(json.dumps({"sources": ["x"]}), encoding="utf-8")

    sources = _load_p0_p1_list(str(cfg))

    assert sources == DEFAULT_P0_P1_SOURCES


def test_load_p0_p1_list_falls_back_when_file_missing() -> None:
    sources = _load_p0_p1_list("/nonexistent/path/p0p1.json")

    assert sources == DEFAULT_P0_P1_SOURCES


# ---------------------------------------------------------------------------
# run_source_failure_audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_marks_p0_p1_sources_below_threshold_as_meeting_sla() -> None:
    # gamma is P0/P1 (in default list); 5/1000 = 0.005 < 0.01 threshold.
    rows = [("gamma", 990, 5, 1000)]
    ch = _FakeClickHouse(rows)

    report = await run_source_failure_audit(ch, asked_at=_NOW)

    gamma = next(r for r in report.sources if r.source == "gamma")
    assert gamma.failure_rate == pytest.approx(0.005)
    assert gamma.is_p0_p1 is True
    assert gamma.meets_sla is True
    assert report.p0_p1_sources_total == 1
    assert report.p0_p1_sources_meeting_sla == 1
    assert report.p0_p1_sources_failing_sla == 0
    assert report.all_p0_p1_meet_sla is True


@pytest.mark.asyncio
async def test_audit_flags_p0_p1_source_above_threshold() -> None:
    # rss has 50/1000 = 0.05 >> 0.01.
    rows = [("rss", 950, 50, 1000)]
    ch = _FakeClickHouse(rows)

    report = await run_source_failure_audit(ch, asked_at=_NOW)

    rss = next(r for r in report.sources if r.source == "rss")
    assert rss.meets_sla is False
    assert report.p0_p1_sources_failing_sla == 1
    assert report.all_p0_p1_meet_sla is False


@pytest.mark.asyncio
async def test_audit_classifies_unlisted_sources_as_other() -> None:
    rows = [("custom_unlisted", 999, 1, 1000)]
    ch = _FakeClickHouse(rows)

    report = await run_source_failure_audit(ch, asked_at=_NOW)

    other = next(r for r in report.sources if r.source == "custom_unlisted")
    assert other.is_p0_p1 is False
    assert other.priority == "other"
    # Unlisted sources don't gate the all_p0_p1_meet_sla aggregate, so when
    # the only row is non-P0/P1 there are zero P0/P1 rows tracked.
    assert report.p0_p1_sources_total == 0
    # all_p0_p1_meet_sla requires len(p0_p1_rows) > 0 — vacuous-pass guard.
    assert report.all_p0_p1_meet_sla is False


@pytest.mark.asyncio
async def test_audit_zero_total_request_does_not_divide() -> None:
    rows = [("gamma", 0, 0, 0)]
    ch = _FakeClickHouse(rows)

    report = await run_source_failure_audit(ch, asked_at=_NOW)

    gamma = next(r for r in report.sources if r.source == "gamma")
    assert gamma.failure_rate == 0.0
    assert gamma.meets_sla is True


@pytest.mark.asyncio
async def test_audit_passes_window_to_query() -> None:
    ch = _FakeClickHouse([])

    await run_source_failure_audit(ch, asked_at=_NOW, window_hours=12)

    _, params = ch.calls[0]
    assert params is not None
    assert params["asof"] == _NOW
    assert params["start"] == _NOW - timedelta(hours=12)


@pytest.mark.asyncio
async def test_audit_query_failure_returns_empty_report() -> None:
    class _ExplodingClient:
        async def query(self, *args: Any, **kwargs: Any) -> _QueryResult:
            raise RuntimeError("boom")

    report = await run_source_failure_audit(_ExplodingClient(), asked_at=_NOW)

    assert report.sources == []
    assert report.total_sources == 0
    assert report.p0_p1_sources_total == 0
    assert report.all_p0_p1_meet_sla is False


@pytest.mark.asyncio
async def test_audit_threshold_override_applies_to_all_sources() -> None:
    # 100/1000 = 0.10 — fails default 0.01 threshold but passes 0.20 override.
    rows = [("gamma", 900, 100, 1000)]
    ch = _FakeClickHouse(rows)

    strict = await run_source_failure_audit(
        ch, asked_at=_NOW, failure_rate_threshold=0.01
    )
    relaxed = await run_source_failure_audit(
        ch, asked_at=_NOW, failure_rate_threshold=0.20
    )

    assert strict.sources[0].meets_sla is False
    assert relaxed.sources[0].meets_sla is True


@pytest.mark.asyncio
async def test_audit_custom_p0_p1_list_overrides_default() -> None:
    rows = [("gamma", 999, 1, 1000), ("custom", 999, 1, 1000)]
    ch = _FakeClickHouse(rows)

    report = await run_source_failure_audit(
        ch, asked_at=_NOW, p0_p1_sources=["custom"]
    )

    gamma = next(r for r in report.sources if r.source == "gamma")
    custom = next(r for r in report.sources if r.source == "custom")
    # With override, gamma is no longer P0/P1.
    assert gamma.is_p0_p1 is False
    assert custom.is_p0_p1 is True
    assert report.p0_p1_sources_total == 1


@pytest.mark.asyncio
async def test_audit_aggregates_meeting_and_failing_counts() -> None:
    rows = [
        ("gamma", 990, 5, 1000),  # meets
        ("clob", 999, 1, 1000),  # meets
        ("rss", 900, 100, 1000),  # fails (10% > 1%)
        ("ignored", 0, 0, 0),  # not P0/P1 (renamed below)
    ]
    ch = _FakeClickHouse(rows)

    report = await run_source_failure_audit(ch, asked_at=_NOW)

    assert report.p0_p1_sources_total == 3  # gamma, clob, rss are tracked
    assert report.p0_p1_sources_meeting_sla == 2
    assert report.p0_p1_sources_failing_sla == 1
    assert report.all_p0_p1_meet_sla is False


@pytest.mark.asyncio
async def test_audit_default_window_is_24_hours() -> None:
    ch = _FakeClickHouse([])

    report = await run_source_failure_audit(ch, asked_at=_NOW)

    assert report.window_hours == 24
    _, params = ch.calls[0]
    assert params is not None
    assert params["start"] == _NOW - timedelta(hours=24)
