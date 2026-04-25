from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from api import asof as asof_q
from api import resolution_risk_corpus
from api.resolution_risk import ResolutionRiskRow
from api.resolution_risk_corpus import (
    ResolutionHistoryRow,
    build_resolution_risk_corpus_rows,
    run_resolution_risk_corpus_refresh,
    summarize_resolution_path,
)


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self.inserts: list[tuple[str, list[tuple[object, ...]], tuple[str, ...]]] = []

    async def query(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self.rows)

    async def insert(
        self,
        table: str,
        rows: list[tuple[object, ...]],
        *,
        column_names: tuple[str, ...],
    ) -> None:
        self.inserts.append((table, list(rows), column_names))


def test_summarize_resolution_path_flags_disputes_and_unexpected_paths() -> None:
    t0 = datetime(2026, 4, 20, 12, tzinfo=UTC)
    summary = summarize_resolution_path(
        [
            ResolutionHistoryRow(
                condition_id="cond-1",
                resolved_outcome="INVALID",
                resolution_source="uma",
                is_revision=False,
                prior_observed_at=None,
                dispute_status="disputed",
                event_time=t0,
                observed_at=t0,
            ),
            ResolutionHistoryRow(
                condition_id="cond-1",
                resolved_outcome="YES",
                resolution_source="manual",
                is_revision=True,
                prior_observed_at=t0,
                dispute_status="appealed",
                event_time=t0,
                observed_at=t0,
            ),
        ]
    )

    assert summary.had_dispute is True
    assert summary.unexpected_resolution is True
    assert summary.revision_count == 1
    assert summary.invalid_count == 1
    assert summary.manual_count == 1
    assert summary.dispute_statuses == ["appealed", "disputed"]
    assert "resolution revised" in summary.unexpected_reasons


@pytest.mark.asyncio
async def test_build_resolution_risk_corpus_rows_joins_snapshot_and_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            (
                "cond-1",
                "YES",
                "uma",
                0,
                None,
                "",
                observed_at,
                observed_at,
            ),
            (
                "cond-2",
                "INVALID",
                "manual",
                1,
                observed_at,
                "disputed",
                observed_at,
                observed_at,
            ),
        ]
    )

    async def _fake_snapshot(
        ch_: object,
        condition_id: str,
        asked_at: datetime,
    ) -> asof_q.MarketSnapshotRow | None:
        del ch_, asked_at
        return asof_q.MarketSnapshotRow(
            condition_id=condition_id,
            question=f"Question for {condition_id}",
            description="Resolution text",
            resolution_source="official source",
            active=False,
            closed=True,
            volume_usdc=1000.0,
            liquidity_usdc=500.0,
            open_interest_usdc=100.0,
            end_date=None,
            token_ids=[],
            event_time=observed_at,
            observed_at=observed_at,
        )

    async def _fake_risk(
        ch_: object,
        condition_id: str,
        asked_at: datetime,
    ) -> ResolutionRiskRow | None:
        del ch_, asked_at
        return ResolutionRiskRow(
            condition_id=condition_id,
            risk_score=0.7,
            risk_level="high",
            is_flagged=True,
            risk_multiplier=1.5,
            classifier="heuristic_v1",
            reasons=["subjective threshold language"],
            event_time=observed_at,
            observed_at=observed_at,
        )

    monkeypatch.setattr(
        resolution_risk_corpus.asof_q,
        "latest_market_snapshot_asof",
        _fake_snapshot,
    )
    monkeypatch.setattr(
        resolution_risk_corpus,
        "resolution_risk_asof",
        _fake_risk,
    )

    rows, disputed_markets, unexpected_markets = await build_resolution_risk_corpus_rows(
        ch,  # type: ignore[arg-type]
        asked_at=observed_at,
        lookback_days=365,
        limit=10,
    )

    assert len(rows) == 2
    assert disputed_markets == 1
    assert unexpected_markets == 1
    cond2 = next(row for row in rows if row[0] == "cond-2")
    assert cond2[1] == "Question for cond-2"
    assert cond2[2] == "Resolution text"
    assert cond2[3] == "official source"
    assert cond2[13] is True


@pytest.mark.asyncio
async def test_run_resolution_risk_corpus_refresh_writes_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)

    async def _fake_build_rows(
        ch: object,
        *,
        asked_at: datetime,
        lookback_days: int,
        limit: int,
    ) -> tuple[list[tuple[object, ...]], int, int]:
        del ch, lookback_days, limit
        return (
            [
                (
                    "cond-1",
                    "Question",
                    "Description",
                    "source",
                    "YES",
                    "YES",
                    "uma",
                    1,
                    0,
                    0,
                    0,
                    1,
                    False,
                    False,
                    "",
                    "",
                    0.1,
                    "low",
                    False,
                    "heuristic_v1",
                    "",
                    asked_at,
                    asked_at,
                    asked_at,
                    asked_at,
                )
            ],
            0,
            0,
        )

    fake_settings = SimpleNamespace(
        resolution_risk_corpus_lookback_days=3650,
        resolution_risk_corpus_limit=5000,
    )
    monkeypatch.setattr(resolution_risk_corpus, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(
        resolution_risk_corpus,
        "build_resolution_risk_corpus_rows",
        _fake_build_rows,
    )

    ch = _FakeClickHouse([])
    report = await run_resolution_risk_corpus_refresh(
        ch,  # type: ignore[arg-type]
        asked_at=observed_at,
    )

    assert report.rows_written == 1
    assert [item[0] for item in ch.inserts] == ["resolution_risk_corpus"]
