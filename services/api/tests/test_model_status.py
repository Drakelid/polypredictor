from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.model_status import (
    model_disable_status_asof,
    model_disable_status_batch_asof,
)


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
    market_type: str = "threshold",
    action: str = "disabled",
    consecutive_days: int = 8,
    threshold_days: int = 7,
    reason: str = "8 consecutive days of negative 30d Brier skill",
) -> tuple[object, ...]:
    observed = datetime(2026, 4, 24, 12, tzinfo=UTC)
    return (
        market_type,
        action,
        consecutive_days,
        threshold_days,
        reason,
        observed,
        observed,
    )


@pytest.mark.asyncio
async def test_model_disable_status_asof_returns_disabled_state() -> None:
    asked_at = datetime(2026, 4, 24, 13, tzinfo=UTC)
    ch = _FakeClickHouse([_row(action="disabled")])

    status = await model_disable_status_asof(
        ch, market_type="threshold", asked_at=asked_at
    )

    assert status is not None
    assert status.is_disabled is True
    assert status.action == "disabled"
    assert status.consecutive_days == 8
    assert status.threshold_days == 7
    assert ch.calls[0][1] == {"market_type": "threshold", "asof": asked_at}


@pytest.mark.asyncio
async def test_model_disable_status_asof_returns_enabled_state_after_re_enable() -> None:
    asked_at = datetime(2026, 4, 24, 13, tzinfo=UTC)
    ch = _FakeClickHouse([_row(action="re_enabled", consecutive_days=0)])

    status = await model_disable_status_asof(
        ch, market_type="threshold", asked_at=asked_at
    )

    assert status is not None
    assert status.is_disabled is False
    assert status.action == "re_enabled"


@pytest.mark.asyncio
async def test_model_disable_status_asof_returns_none_when_never_logged() -> None:
    asked_at = datetime(2026, 4, 24, tzinfo=UTC)
    ch = _FakeClickHouse([])
    assert (
        await model_disable_status_asof(
            ch, market_type="threshold", asked_at=asked_at
        )
        is None
    )


@pytest.mark.asyncio
async def test_model_disable_status_batch_asof_keys_by_market_type() -> None:
    asked_at = datetime(2026, 4, 24, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            _row(market_type="threshold", action="disabled"),
            _row(market_type="discrete_event", action="re_enabled"),
        ]
    )

    statuses = await model_disable_status_batch_asof(
        ch, market_types=["threshold", "discrete_event"], asked_at=asked_at
    )

    assert set(statuses.keys()) == {"threshold", "discrete_event"}
    assert statuses["threshold"].is_disabled is True
    assert statuses["discrete_event"].is_disabled is False


@pytest.mark.asyncio
async def test_model_disable_status_batch_asof_empty_input_skips_query() -> None:
    asked_at = datetime(2026, 4, 24, tzinfo=UTC)
    ch = _FakeClickHouse([])

    result = await model_disable_status_batch_asof(
        ch, market_types=[], asked_at=asked_at
    )

    assert result == {}
    assert ch.calls == []
