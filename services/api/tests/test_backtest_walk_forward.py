from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api import asof as asof_q
from api import backtest_walk_forward
from api.backtest_walk_forward import (
    _normalize_horizons,
    load_resolved_market_corpus,
    run_walk_forward_backtest,
)


class _Detail:
    def __init__(
        self,
        *,
        market_type: str = "threshold",
        model_prob: float | None = 0.7,
        band_lo: float | None = 0.5,
        band_hi: float | None = 0.9,
        regime_label: str | None = "bull_trend",
    ) -> None:
        self.market_type = market_type
        self.model_prob = model_prob
        self.band_lo = band_lo
        self.band_hi = band_hi
        self.regime_label = regime_label


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self.rows)


def test_normalize_horizons_dedupes_and_validates() -> None:
    assert _normalize_horizons([24, 24, 72, -1, 0]) == [24, 72]
    with pytest.raises(ValueError):
        _normalize_horizons([0, -24])


@pytest.mark.asyncio
async def test_load_resolved_market_corpus_counts_invalid_disputed_and_low_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse(
        [
            (
                "cond-yes",
                "YES",
                "",
                datetime(2026, 4, 10, 12, tzinfo=UTC),
                datetime(2026, 4, 10, 12, tzinfo=UTC),
            ),
            (
                "cond-invalid",
                "INVALID",
                "disputed",
                datetime(2026, 4, 9, 12, tzinfo=UTC),
                datetime(2026, 4, 9, 12, tzinfo=UTC),
            ),
        ]
    )

    async def _fake_snapshot_asof(
        ch_: object,
        condition_id: str,
        asked_at: datetime,
    ) -> asof_q.MarketSnapshotRow | None:
        assert ch_ is ch
        if condition_id == "cond-invalid":
            return asof_q.MarketSnapshotRow(
                condition_id=condition_id,
                question="Invalid market",
                description="",
                resolution_source="",
                active=False,
                closed=True,
                volume_usdc=100.0,
                liquidity_usdc=50.0,
                open_interest_usdc=0.0,
                end_date=None,
                token_ids=[],
                event_time=asked_at,
                observed_at=asked_at,
            )
        return asof_q.MarketSnapshotRow(
            condition_id=condition_id,
            question="Binary market",
            description="",
            resolution_source="",
            active=False,
            closed=True,
            volume_usdc=5_000.0,
            liquidity_usdc=2_000.0,
            open_interest_usdc=0.0,
            end_date=None,
            token_ids=[],
            event_time=asked_at,
            observed_at=asked_at,
        )

    monkeypatch.setattr(
        backtest_walk_forward.asof_q,
        "latest_market_snapshot_asof",
        _fake_snapshot_asof,
    )

    rows, summary = await load_resolved_market_corpus(
        ch,  # type: ignore[arg-type]
        limit=10,
        low_volume_threshold_usdc=1_000.0,
    )

    assert len(rows) == 2
    assert summary.total_resolved_markets == 2
    assert summary.binary_markets == 1
    assert summary.invalid_markets == 1
    assert summary.disputed_markets == 1
    assert summary.low_volume_markets == 1
    assert "resolved_outcome IN ('YES', 'NO', 'INVALID')" in ch.calls[0][0]
    assert "ORDER BY condition_id, observed_at ASC" in ch.calls[0][0]


@pytest.mark.asyncio
async def test_run_walk_forward_backtest_replays_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    corpus_rows = [
        backtest_walk_forward.ResolvedCorpusRow(
            condition_id="cond-1",
            resolved_outcome="YES",
            resolved_at=datetime(2026, 4, 10, 12, tzinfo=UTC),
            resolution_observed_at=datetime(2026, 4, 10, 12, tzinfo=UTC),
            dispute_status=None,
            volume_usdc=5000.0,
            liquidity_usdc=2000.0,
        ),
        backtest_walk_forward.ResolvedCorpusRow(
            condition_id="cond-2",
            resolved_outcome="NO",
            resolved_at=datetime(2026, 4, 11, 12, tzinfo=UTC),
            resolution_observed_at=datetime(2026, 4, 11, 12, tzinfo=UTC),
            dispute_status=None,
            volume_usdc=800.0,
            liquidity_usdc=300.0,
        ),
        backtest_walk_forward.ResolvedCorpusRow(
            condition_id="cond-3",
            resolved_outcome="INVALID",
            resolved_at=datetime(2026, 4, 11, 12, tzinfo=UTC),
            resolution_observed_at=datetime(2026, 4, 11, 12, tzinfo=UTC),
            dispute_status="disputed",
            volume_usdc=200.0,
            liquidity_usdc=100.0,
        ),
    ]

    async def _fake_load_resolved_market_corpus(
        ch: object,
        *,
        resolved_from: datetime | None = None,
        resolved_to: datetime | None = None,
        limit: int = 500,
        low_volume_threshold_usdc: float = 1_000.0,
    ) -> tuple[list[backtest_walk_forward.ResolvedCorpusRow], backtest_walk_forward.CorpusSummary]:
        assert resolved_from is None
        assert resolved_to is None
        assert limit == 50
        assert low_volume_threshold_usdc == pytest.approx(1_000.0)
        return (
            corpus_rows,
            backtest_walk_forward.CorpusSummary(
                total_resolved_markets=3,
                binary_markets=2,
                invalid_markets=1,
                disputed_markets=1,
                low_volume_markets=2,
                missing_snapshot_markets=0,
            ),
        )

    async def _fake_model_for_market(
        ch: object,
        *,
        condition_id: str,
        asked_at: datetime,
        tuning_profile=None,
    ) -> object | None:
        del tuning_profile
        assert ch == "fake-clickhouse"
        if condition_id == "cond-2" and asked_at.day == 8:
            return None
        if condition_id == "cond-2":
            return _Detail(
                market_type="discrete_event",
                model_prob=0.2,
                band_lo=0.1,
                band_hi=0.4,
                regime_label="chop",
            )
        return _Detail(
            market_type="threshold",
            model_prob=0.8,
            band_lo=0.6,
            band_hi=0.95,
            regime_label="bull_trend",
        )

    monkeypatch.setattr(
        backtest_walk_forward,
        "load_resolved_market_corpus",
        _fake_load_resolved_market_corpus,
    )
    monkeypatch.setattr(
        backtest_walk_forward,
        "model_for_market",
        _fake_model_for_market,
    )

    report = await run_walk_forward_backtest(
        "fake-clickhouse",  # type: ignore[arg-type]
        horizons_hours=[24, 72],
        limit=50,
    )

    assert report.total_resolved_markets == 3
    assert report.scored_samples == 3
    assert report.skipped_samples == 1
    assert report.corpus.total_resolved_markets == 3
    assert report.corpus.invalid_markets == 1
    assert report.corpus.low_volume_markets == 2
    assert [summary.horizon_hours for summary in report.by_horizon] == [24, 72]
    assert report.by_horizon[0].scored_samples == 2
    assert report.by_horizon[1].scored_samples == 1
    assert report.by_horizon[1].skipped_samples == 1
    assert report.evaluation.overall.sample_count == 3
    assert {row.market_type for row in report.evaluation.by_type} == {
        "discrete_event",
        "threshold",
    }
    assert {row.regime for row in report.evaluation.by_regime} == {
        "bull_trend",
        "chop",
    }
