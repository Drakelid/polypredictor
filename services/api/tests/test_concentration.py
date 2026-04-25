from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.concentration import concentration_asof, concentration_batch_asof


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self.rows)


def _row(
    condition_id: str = "cond-1",
    *,
    yes_gini: float | None = 0.72,
    no_gini: float | None = 0.5,
    yes_top1_pct: float | None = 0.55,
    no_top1_pct: float | None = 0.3,
    yes_whale_flag: int = 1,
    no_whale_flag: int = 0,
) -> tuple[object, ...]:
    observed_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    max_gini = None
    candidates = [v for v in (yes_gini, no_gini) if v is not None]
    if candidates:
        max_gini = max(candidates)
    return (
        condition_id,
        yes_gini,
        no_gini,
        yes_top1_pct,
        no_top1_pct,
        0.8,
        0.6,
        yes_whale_flag,
        no_whale_flag,
        1 if (yes_whale_flag or no_whale_flag) else 0,
        10,
        5,
        max_gini,
        observed_at,
        observed_at,
    )


@pytest.mark.asyncio
async def test_concentration_asof_returns_row() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_row()])

    row = await concentration_asof(ch, "cond-1", asked_at)

    assert row is not None
    assert row.condition_id == "cond-1"
    assert row.yes_whale_flag is True
    assert row.no_whale_flag is False
    assert row.any_whale_flag is True
    assert row.max_gini == pytest.approx(0.72)


@pytest.mark.asyncio
async def test_concentration_batch_asof_keys_by_condition_id() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            _row("cond-1"),
            _row("cond-2", yes_gini=0.3, yes_top1_pct=0.25, yes_whale_flag=0),
        ]
    )

    rows = await concentration_batch_asof(ch, ["cond-1", "cond-2"], asked_at)

    assert set(rows.keys()) == {"cond-1", "cond-2"}
    assert rows["cond-2"].yes_whale_flag is False


@pytest.mark.asyncio
async def test_concentration_asof_returns_none_when_absent() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])
    assert await concentration_asof(ch, "cond-1", asked_at) is None
