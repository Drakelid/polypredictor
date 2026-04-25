from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.signals import list_signals


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
    *,
    event_id: str = "evt-1",
    event_type: str = "whale_open",
    condition_id: str = "cond-1",
    severity: float = 1.25,
    actor: str = "0xw",
    direction: str = "yes",
    size_delta_usdc: float | None = 12_500.0,
    prior_size_usdc: float | None = 0.0,
    current_size_usdc: float | None = 12_500.0,
    payload: str = '{"token_id":"tok-y","outcome":"YES"}',
) -> tuple[object, ...]:
    observed_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    return (
        event_id,
        event_type,
        condition_id,
        severity,
        actor,
        direction,
        size_delta_usdc,
        prior_size_usdc,
        current_size_usdc,
        payload,
        observed_at,
        observed_at,
    )


@pytest.mark.asyncio
async def test_list_signals_returns_parsed_events() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_row()])

    events = await list_signals(ch, asked_at=asked_at, lookback_hours=12)

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "whale_open"
    assert event.severity == pytest.approx(1.25)
    assert event.payload == {"token_id": "tok-y", "outcome": "YES"}


@pytest.mark.asyncio
async def test_list_signals_applies_event_type_filter_in_params() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])

    await list_signals(
        ch,
        asked_at=asked_at,
        event_types=["whale_open", "whale_resize"],
    )

    assert ch.calls[0][1]["types"] == ["whale_open", "whale_resize"]


@pytest.mark.asyncio
async def test_list_signals_condition_scope_adds_where_clause() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])

    await list_signals(ch, asked_at=asked_at, condition_id="cond-9")

    assert ch.calls[0][1]["cond"] == "cond-9"


@pytest.mark.asyncio
async def test_list_signals_min_severity_adds_where_clause() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse([])

    await list_signals(ch, asked_at=asked_at, min_severity=1.5)

    assert ch.calls[0][1]["min_severity"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_list_signals_handles_invalid_payload_gracefully() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse([_row(payload="not json")])

    events = await list_signals(ch, asked_at=asked_at)

    assert events[0].payload == {}
