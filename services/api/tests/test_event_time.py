from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from api import event_time
from api.event_time import event_time_context_asof, scheduled_events_asof


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, results: list[list[tuple[object, ...]]]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.calls.append((query, parameters))
        return _QueryResult(self.results.pop(0))


@pytest.mark.asyncio
async def test_scheduled_events_asof_returns_latest_unique_rows() -> None:
    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [[
            (
                "macro",
                "macro:fomc",
                "fomc-2026-06-17",
                "FOMC rate decision",
                '{"scheduled": true}',
                datetime(2026, 6, 17, 18, tzinfo=UTC),
                asked_at,
            )
        ]]
    )

    rows = await scheduled_events_asof(ch, "cond-fomc", asked_at)

    assert len(rows) == 1
    assert rows[0].source == "macro:fomc"
    assert ch.calls[0][1]["cond"] == "cond-fomc"


@pytest.mark.asyncio
async def test_scheduled_events_asof_includes_scheduled_onchain_events() -> None:
    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [[
            (
                "onchain",
                "onchain:unlock",
                "unlock-2026-04-30",
                "Token unlock",
                '{"scheduled": true}',
                datetime(2026, 4, 30, 0, tzinfo=UTC),
                asked_at,
            )
        ]]
    )

    rows = await scheduled_events_asof(ch, "cond-token", asked_at)

    assert len(rows) == 1
    assert rows[0].event_kind == "onchain"
    assert rows[0].source == "onchain:unlock"


@pytest.mark.asyncio
async def test_event_time_context_marks_24h_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_classification(ch, condition_id, asked_at):
        del ch, condition_id, asked_at
        return SimpleNamespace(features=SimpleNamespace(asset=None))

    monkeypatch.setattr(event_time, "classification_asof", _fake_classification)

    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [[
                (
                    "macro",
                    "macro:cpi",
                    "cpi-2026-04-25",
                    "US CPI release",
                    '{"scheduled": true}',
                    datetime(2026, 4, 25, 0, tzinfo=UTC),
                    asked_at,
                )
            ]]
    )

    context = await event_time_context_asof(ch, "cond-cpi", asked_at)

    assert context.next_event is not None
    assert context.next_event.source == "macro:cpi"
    assert context.in_event_window_24h is True
    assert context.hours_to_next_event == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_event_time_context_computes_post_event_drift_after_24h(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_classification(ch, condition_id, asked_at):
        del ch, condition_id, asked_at
        return SimpleNamespace(features=SimpleNamespace(asset=None))

    monkeypatch.setattr(event_time, "classification_asof", _fake_classification)

    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [
                (
                    "macro",
                    "macro:fomc",
                    "fomc-2026-04-25",
                    "FOMC rate decision",
                    '{"scheduled": true, "release_time":"2026-04-25T00:00:00+00:00","consensus_value":25,"actual_value":50,"value_unit":"bps"}',
                    datetime(2026, 4, 25, 0, tzinfo=UTC),
                    asked_at,
                )
            ],
            [
                (
                    "cond-fomc",
                    "Will the Fed cut 25bps?",
                    1,
                    0,
                    1000.0,
                    500.0,
                    250.0,
                    None,
                    ["tok-yes", "tok-no"],
                    asked_at,
                    asked_at,
                )
            ],
            [
                (
                    "tok-yes",
                    "cond-fomc",
                    0.44,
                    0.43,
                    0.45,
                    0.02,
                    datetime(2026, 4, 25, 1, tzinfo=UTC),
                    datetime(2026, 4, 25, 1, tzinfo=UTC),
                )
            ],
            [
                (
                    "tok-yes",
                    "cond-fomc",
                    0.51,
                    0.50,
                    0.52,
                    0.02,
                    datetime(2026, 4, 26, 0, tzinfo=UTC),
                    datetime(2026, 4, 26, 0, tzinfo=UTC),
                )
            ],
        ]
    )

    context = await event_time_context_asof(ch, "cond-fomc", asked_at)

    assert context.previous_event is not None
    assert context.post_event_mid_1h == pytest.approx(0.44)
    assert context.post_event_mid_24h == pytest.approx(0.51)
    assert context.post_event_drift_1h_to_24h == pytest.approx(0.07)
    assert context.previous_event.consensus_value == pytest.approx(25.0)
    assert context.previous_event.actual_value == pytest.approx(50.0)
    assert context.previous_event.surprise_value == pytest.approx(25.0)
    assert context.previous_event.value_unit == "bps"


@pytest.mark.asyncio
async def test_event_time_context_computes_pre_event_iv_runup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_classification(ch, condition_id, asked_at):
        del ch, condition_id, asked_at
        return SimpleNamespace(features=SimpleNamespace(asset="BTC"))

    monkeypatch.setattr(event_time, "classification_asof", _fake_classification)

    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [
                (
                    "macro",
                    "macro:cpi",
                    "cpi-2026-04-25",
                    "US CPI release",
                    '{"scheduled": true}',
                    datetime(2026, 4, 25, 0, tzinfo=UTC),
                    asked_at,
                )
            ],
            [
                ("BTC", datetime(2026, 4, 25, 0, tzinfo=UTC), 0.62, asked_at),
            ],
            [
                (
                    "BTC",
                    datetime(2026, 4, 25, 0, tzinfo=UTC),
                    0.55,
                    datetime(2026, 4, 23, 12, tzinfo=UTC),
                ),
            ],
        ]
    )

    context = await event_time_context_asof(ch, "cond-cpi", asked_at)

    assert context.event_asset == "BTC"
    assert context.pre_event_atm_iv_now == pytest.approx(0.62)
    assert context.pre_event_atm_iv_24h_ago == pytest.approx(0.55)
    assert context.pre_event_iv_runup_24h == pytest.approx(0.07)


@pytest.mark.asyncio
async def test_event_time_context_computes_post_event_iv_crush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_classification(ch, condition_id, asked_at):
        del ch, condition_id, asked_at
        return SimpleNamespace(features=SimpleNamespace(asset="ETH"))

    monkeypatch.setattr(event_time, "classification_asof", _fake_classification)

    asked_at = datetime(2026, 4, 26, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            [
                (
                    "macro",
                    "macro:fomc",
                    "fomc-2026-04-25",
                    "FOMC rate decision",
                    '{"scheduled": true}',
                    datetime(2026, 4, 25, 0, tzinfo=UTC),
                    asked_at,
                )
            ],
            [
                ("ETH", datetime(2026, 4, 25, 0, tzinfo=UTC), 0.78, datetime(2026, 4, 24, 23, tzinfo=UTC)),
            ],
            [
                ("ETH", datetime(2026, 4, 25, 0, tzinfo=UTC), 0.64, datetime(2026, 4, 26, 0, tzinfo=UTC)),
            ],
            [
                (
                    "cond-fomc",
                    "Will the Fed cut 25bps?",
                    1,
                    0,
                    1000.0,
                    500.0,
                    250.0,
                    None,
                    ["tok-yes", "tok-no"],
                    asked_at,
                    asked_at,
                )
            ],
            [
                (
                    "tok-yes",
                    "cond-fomc",
                    0.44,
                    0.43,
                    0.45,
                    0.02,
                    datetime(2026, 4, 25, 1, tzinfo=UTC),
                    datetime(2026, 4, 25, 1, tzinfo=UTC),
                )
            ],
            [
                (
                    "tok-yes",
                    "cond-fomc",
                    0.51,
                    0.50,
                    0.52,
                    0.02,
                    datetime(2026, 4, 26, 0, tzinfo=UTC),
                    datetime(2026, 4, 26, 0, tzinfo=UTC),
                )
            ],
        ]
    )

    context = await event_time_context_asof(ch, "cond-fomc", asked_at)

    assert context.event_asset == "ETH"
    assert context.post_event_atm_iv_pre_1h == pytest.approx(0.78)
    assert context.post_event_atm_iv_24h == pytest.approx(0.64)
    assert context.post_event_iv_crush_pre_1h_to_24h == pytest.approx(-0.14)
