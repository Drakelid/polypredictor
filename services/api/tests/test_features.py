from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.asof import quotes_timeseries_pit
from api.features import feature_snapshot_asof


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


@pytest.mark.asyncio
async def test_feature_snapshot_asof_returns_latest_row() -> None:
    asked_at = datetime(2026, 4, 22, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            (
                "cond-1",
                "tok-yes",
                0.62,
                0.04,
                0.2,
                0.3,
                0.05,
                0.1,
                0.15,
                0.42,
                0.12,
                -0.08,
                0.04,
                asked_at,
                asked_at,
            )
        ]
    )

    row = await feature_snapshot_asof(ch, "cond-1", asked_at)

    assert row is not None
    assert row.condition_id == "cond-1"
    assert row.token_id == "tok-yes"
    assert row.mid == pytest.approx(0.62)
    assert row.realized_vol_24h == pytest.approx(0.42)
    assert row.informed_taker_flow_24h == pytest.approx(0.12)
    assert row.passive_maker_flow_24h == pytest.approx(-0.08)
    assert ch.calls[0][1] == {"cond": "cond-1", "asof": asked_at}


@pytest.mark.asyncio
async def test_quotes_timeseries_pit_returns_rows() -> None:
    start = datetime(2026, 4, 21, 12, tzinfo=UTC)
    end = datetime(2026, 4, 22, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            (
                "tok-yes",
                "cond-1",
                0.62,
                0.61,
                0.63,
                0.02,
                end,
                end,
            )
        ]
    )

    rows = await quotes_timeseries_pit(ch, "tok-yes", start, end)

    assert len(rows) == 1
    assert rows[0].token_id == "tok-yes"
    assert rows[0].mid == pytest.approx(0.62)
    assert ch.calls[0][1] == {"tok": "tok-yes", "start": start, "end": end}
