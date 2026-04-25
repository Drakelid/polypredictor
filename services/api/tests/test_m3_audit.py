from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from api import m3_audit


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
async def test_smart_money_ablation_audit_reports_incremental_gain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_at = datetime(2026, 4, 23, 12, tzinfo=UTC)

    async def _fake_resolutions(ch, **kwargs):
        return [
            ("cond-1", "YES", resolved_at),
            ("cond-2", "NO", resolved_at),
        ]

    async def _fake_probs(ch, *, condition_id: str, asked_at: datetime, deribit, fedwatch):
        if condition_id == "cond-1":
            return (0.70, 0.55)
        return (0.30, 0.45)

    monkeypatch.setattr(m3_audit, "latest_binary_resolutions", _fake_resolutions)
    monkeypatch.setattr(m3_audit, "_smart_money_probabilities_for_resolution", _fake_probs)
    monkeypatch.setattr(
        m3_audit,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "deribit_base": "https://example.com",
                "cme_api_id": None,
                "cme_api_secret": None,
                "cme_fedwatch_base": "",
                "cme_oauth_token_url": "",
                "cme_application_name": "app",
                "cme_application_vendor": "vendor",
                "cme_application_version": "0.0.1",
            },
        )(),
    )

    class _FakeDeribit:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(m3_audit, "DeribitClient", lambda base_url: _FakeDeribit())

    report = await m3_audit.smart_money_ablation_audit(
        object(),  # type: ignore[arg-type]
        asked_at=datetime(2026, 4, 24, tzinfo=UTC),
        resolved_limit=10,
    )

    assert report.total_resolved_markets == 2
    assert report.evaluated_samples == 2
    assert report.samples_with_smart_money == 2
    assert report.non_zero_contribution_samples == 2
    assert report.mean_abs_probability_delta == pytest.approx(0.15)
    assert report.incremental_brier_gain is not None
    assert report.incremental_brier_gain > 0


@pytest.mark.asyncio
async def test_arb_replay_audit_counts_recovered_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)

    async def _fake_recent(ch, *, asked_at: datetime, lookback_days: int, limit: int):
        return [
            ("evt-1", "cond-1", now),
            ("evt-2", "cond-2", now),
        ]

    async def _fake_replay(ch, *, event_id: str, condition_id: str, observed_at: datetime, limit: int = 500):
        return event_id == "evt-1"

    monkeypatch.setattr(m3_audit, "_recent_arb_events", _fake_recent)
    monkeypatch.setattr(m3_audit, "_replay_arb_event", _fake_replay)

    report = await m3_audit.arb_replay_audit(
        object(),  # type: ignore[arg-type]
        asked_at=now,
        event_limit=10,
    )

    assert report.known_event_count == 2
    assert report.replayed_events == 2
    assert report.recovered_events == 1
    assert report.recovery_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_signal_feed_density_audit_fills_missing_days() -> None:
    asked_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    ch = _FakeClickHouse(
        [
            (date(2026, 4, 17), 12),
            (date(2026, 4, 19), 8),
            (date(2026, 4, 23), 15),
        ]
    )

    report = await m3_audit.signal_feed_density_audit(
        ch,  # type: ignore[arg-type]
        asked_at=asked_at,
        lookback_days=7,
        min_severity=1.0,
    )

    assert report.total_events == 35
    assert report.average_events_per_day == pytest.approx(5.0)
    assert report.days_with_10_plus_events == 2
    assert report.meets_threshold_each_day is False
    assert report.by_day[0].day == "2026-04-17"
    assert report.by_day[1].events == 0
