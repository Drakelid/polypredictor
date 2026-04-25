from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.resolution_risk import (
    resolution_risk_asof,
    resolution_risk_batch_asof,
)


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


def _row(
    condition_id: str = "cond-1",
    *,
    risk_score: float = 0.71,
    risk_level: str = "high",
    is_flagged: int = 1,
    risk_multiplier: float = 1.57,
    classifier: str = "heuristic_v1",
    reasons: str = "no explicit resolution source|subjective threshold language",
) -> tuple[object, ...]:
    observed_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    return (
        condition_id,
        risk_score,
        risk_level,
        is_flagged,
        risk_multiplier,
        classifier,
        reasons,
        observed_at,
        observed_at,
    )


@pytest.mark.asyncio
async def test_resolution_risk_asof_returns_row() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_row()])

    row = await resolution_risk_asof(ch, "cond-1", asked_at)

    assert row is not None
    assert row.condition_id == "cond-1"
    assert row.risk_score == pytest.approx(0.71)
    assert row.is_flagged is True
    assert row.reasons == [
        "no explicit resolution source",
        "subjective threshold language",
    ]


@pytest.mark.asyncio
async def test_resolution_risk_batch_asof_keys_by_condition_id() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            _row("cond-1"),
            _row("cond-2", risk_score=0.24, risk_level="low", is_flagged=0, reasons=""),
        ]
    )

    rows = await resolution_risk_batch_asof(ch, ["cond-1", "cond-2"], asked_at)

    assert set(rows.keys()) == {"cond-1", "cond-2"}
    assert rows["cond-2"].risk_level == "low"
    assert rows["cond-2"].is_flagged is False
    assert rows["cond-2"].reasons == []


@pytest.mark.asyncio
async def test_resolution_risk_asof_returns_none_when_absent() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])
    assert await resolution_risk_asof(ch, "cond-1", asked_at) is None
