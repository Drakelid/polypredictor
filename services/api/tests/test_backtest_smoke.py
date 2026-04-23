from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api import backtest_smoke
from api.backtest_smoke import (
    BacktestSample,
    latest_binary_resolutions,
    run_backtest_smoke,
    summarize_samples,
)


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.queries.append((query, parameters))
        return _QueryResult(self.rows)


@pytest.mark.asyncio
async def test_latest_binary_resolutions_returns_latest_yes_no_rows() -> None:
    ch = _FakeClickHouse(
        [
            ("cond-1", "YES", datetime(2026, 4, 1, tzinfo=UTC)),
            ("cond-2", "NO", datetime(2026, 4, 2, tzinfo=UTC)),
        ]
    )

    rows = await latest_binary_resolutions(
        ch,
        resolved_from=datetime(2026, 3, 1, tzinfo=UTC),
        resolved_to=datetime(2026, 5, 1, tzinfo=UTC),
        limit=50,
    )

    assert rows == [
        ("cond-1", "YES", datetime(2026, 4, 1, tzinfo=UTC)),
        ("cond-2", "NO", datetime(2026, 4, 2, tzinfo=UTC)),
    ]
    assert "resolved_outcome IN ('YES', 'NO')" in ch.queries[0][0]
    assert ch.queries[0][1] == {
        "limit": 50,
        "resolved_from": datetime(2026, 3, 1, tzinfo=UTC),
        "resolved_to": datetime(2026, 5, 1, tzinfo=UTC),
    }


def test_summarize_samples_groups_and_computes_brier_skill() -> None:
    summaries = summarize_samples(
        [
            BacktestSample(
                condition_id="a",
                asked_at=datetime(2026, 3, 31, tzinfo=UTC),
                resolved_at=datetime(2026, 4, 1, tzinfo=UTC),
                outcome=1,
                market_type="threshold",
                model_prob=0.8,
                market_mid=0.6,
                baseline_source="bs_one_touch",
            ),
            BacktestSample(
                condition_id="b",
                asked_at=datetime(2026, 4, 1, tzinfo=UTC),
                resolved_at=datetime(2026, 4, 2, tzinfo=UTC),
                outcome=0,
                market_type="threshold",
                model_prob=0.3,
                market_mid=0.4,
                baseline_source="bs_one_touch",
            ),
            BacktestSample(
                condition_id="c",
                asked_at=datetime(2026, 4, 2, tzinfo=UTC),
                resolved_at=datetime(2026, 4, 3, tzinfo=UTC),
                outcome=1,
                market_type="discrete_event",
                model_prob=0.7,
                market_mid=0.5,
                baseline_source="fedwatch",
            ),
        ]
    )

    assert [summary.market_type for summary in summaries] == [
        "discrete_event",
        "threshold",
    ]
    threshold = summaries[1]
    assert threshold.samples == 2
    assert threshold.baseline_brier == pytest.approx(((0.8 - 1) ** 2 + (0.3 - 0) ** 2) / 2)
    assert threshold.market_mid_brier == pytest.approx(((0.6 - 1) ** 2 + (0.4 - 0) ** 2) / 2)
    assert threshold.brier_skill_vs_mid is not None


@pytest.mark.asyncio
async def test_run_backtest_smoke_skips_missing_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    ch = _FakeClickHouse(
        [
            ("cond-1", "YES", datetime(2026, 4, 3, 12, tzinfo=UTC)),
            ("cond-2", "NO", datetime(2026, 4, 4, 12, tzinfo=UTC)),
        ]
    )

    async def _fake_model_for_market(ch_: object, *, condition_id: str, asked_at: datetime) -> object | None:
        assert ch_ is ch
        if condition_id == "cond-1":
            class _Detail:
                market_type = "threshold"
                model_prob = 0.7
                mid = 0.55
                baseline_source = "bs_one_touch"

            return _Detail()
        return None

    monkeypatch.setattr(backtest_smoke, "model_for_market", _fake_model_for_market)

    report = await run_backtest_smoke(ch, horizon_hours=24, limit=10)

    assert report.total_resolved_markets == 2
    assert report.scored_samples == 1
    assert report.skipped_samples == 1
    assert report.by_type[0].market_type == "threshold"
    assert report.by_type[0].samples == 1
