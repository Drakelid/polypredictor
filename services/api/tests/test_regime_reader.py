from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.regime import regime_label_asof


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(list(self._rows))


def _row(
    *,
    label: str = "bull_trend",
    confidence: float = 0.78,
    reasons: str = "7d momentum +12.0% >= +4.0%|BTC↔NDX corr = +0.55",
) -> tuple[object, ...]:
    observed = datetime(2026, 4, 24, 12, tzinfo=UTC)
    return (
        observed.date(),
        label,
        confidence,
        0.55,
        0.50,
        0.12,
        0.55,
        0.005,
        reasons,
        "rule_v1",
        observed,
        observed,
    )


@pytest.mark.asyncio
async def test_regime_label_asof_parses_row() -> None:
    asked_at = datetime(2026, 4, 24, 13, tzinfo=UTC)
    ch = _FakeClickHouse([_row()])
    row = await regime_label_asof(ch, asked_at)
    assert row is not None
    assert row.label == "bull_trend"
    assert row.confidence == pytest.approx(0.78)
    assert row.classifier == "rule_v1"
    assert row.btc_momentum_7d == pytest.approx(0.12)
    # Reasons round-trip via the pipe-delimited writer.
    assert row.reasons == [
        "7d momentum +12.0% >= +4.0%",
        "BTC↔NDX corr = +0.55",
    ]
    # Query honors the PIT contract: observed_at <= asked_at.
    assert ch.calls[0][1] == {"asof": asked_at}


@pytest.mark.asyncio
async def test_regime_label_asof_returns_none_when_unset() -> None:
    asked_at = datetime(2026, 4, 24, tzinfo=UTC)
    ch = _FakeClickHouse([])
    assert await regime_label_asof(ch, asked_at) is None


@pytest.mark.asyncio
async def test_regime_label_asof_handles_empty_reasons_string() -> None:
    asked_at = datetime(2026, 4, 24, tzinfo=UTC)
    ch = _FakeClickHouse([_row(reasons="")])
    row = await regime_label_asof(ch, asked_at)
    assert row is not None
    assert row.reasons == []
