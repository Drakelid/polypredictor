from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.drift_report import latest_drift_snapshot_asof


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, responses: list[list[tuple[object, ...]]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self._responses[len(self.calls) - 1])


@pytest.mark.asyncio
async def test_latest_drift_snapshot_asof_returns_model_and_feature_rows() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [(observed_at,)],
            [
                (
                    "30d",
                    "threshold",
                    "1d_7d",
                    "bull_trend",
                    12,
                    0.11,
                    0.04,
                    0.79,
                    0.61,
                    0.58,
                    0.13,
                    0.153846,
                    observed_at,
                )
            ],
            [
                (
                    "spread",
                    90,
                    45,
                    0.31,
                    0.12,
                    0.2,
                    True,
                    observed_at,
                )
            ],
        ]
    )

    snapshot = await latest_drift_snapshot_asof(
        ch,
        asked_at=datetime(2026, 4, 24, 13, tzinfo=UTC),
    )

    assert snapshot is not None
    assert snapshot.observed_at == observed_at
    assert len(snapshot.model_metrics) == 1
    assert snapshot.model_metrics[0].regime == "bull_trend"
    assert snapshot.model_metrics[0].coverage == pytest.approx(0.79)
    assert len(snapshot.feature_metrics) == 1
    assert snapshot.feature_metrics[0].feature_name == "spread"
    assert snapshot.feature_metrics[0].is_alert is True


@pytest.mark.asyncio
async def test_latest_drift_snapshot_asof_returns_none_without_rows() -> None:
    ch = _FakeClickHouse([[(None,)]])

    snapshot = await latest_drift_snapshot_asof(
        ch,
        asked_at=datetime(2026, 4, 24, 13, tzinfo=UTC),
    )

    assert snapshot is None
