from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from api import drift_monitor
from api.drift_monitor import (
    DriftReplaySample,
    build_disable_transition_rows,
    build_drift_metric_rows,
    build_feature_drift_rows,
    run_drift_monitor,
)
from api.model_status import ModelDisableStatus
from model import MarketType, WalkForwardSample


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.rows = rows or []
        self.inserts: list[tuple[str, list[tuple[object, ...]], tuple[str, ...]]] = []

    async def query(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> _QueryResult:
        return _QueryResult(self.rows)

    async def insert(
        self,
        table: str,
        rows: list[tuple[object, ...]],
        *,
        column_names: tuple[str, ...],
    ) -> None:
        self.inserts.append((table, list(rows), column_names))


def _replay_sample(
    *,
    asked_at: float,
    predicted_prob: float,
    baseline_prob: float,
    outcome: int,
    regime: str | None,
) -> DriftReplaySample:
    return DriftReplaySample(
        condition_id=f"cond-{asked_at}",
        model_sample=WalkForwardSample(
            market_type=MarketType.THRESHOLD,
            asked_at=asked_at,
            resolved_at=asked_at + 24 * 3_600.0,
            predicted_prob=predicted_prob,
            outcome=outcome,
            time_to_resolution_s=24 * 3_600.0,
            band_lo=0.2,
            band_hi=0.8,
            regime=regime,
        ),
        baseline_prob=baseline_prob,
    )


def test_build_drift_metric_rows_includes_skill_and_regime_rows() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    samples = [
        _replay_sample(
            asked_at=(observed_at.timestamp() - 2 * 86_400.0),
            predicted_prob=0.8,
            baseline_prob=0.6,
            outcome=1,
            regime="bull_trend",
        ),
        _replay_sample(
            asked_at=(observed_at.timestamp() - 3 * 86_400.0),
            predicted_prob=0.2,
            baseline_prob=0.4,
            outcome=0,
            regime="chop",
        ),
    ]

    rows = build_drift_metric_rows(samples, observed_at=observed_at)
    by_key = {
        (str(row[0]), str(row[1]), str(row[2]), str(row[3])): row
        for row in rows
    }

    overall_7d = by_key[("7d", "overall", "overall", "")]
    bull_7d = by_key[("7d", "overall", "overall", "bull_trend")]
    threshold_7d = by_key[("7d", "threshold", "overall", "")]

    assert len(rows) == 18
    assert overall_7d[4] == 2
    assert overall_7d[10] == pytest.approx(0.16)
    assert overall_7d[11] == pytest.approx(0.75)
    assert bull_7d[4] == 1
    assert threshold_7d[1] == "threshold"


def test_build_feature_drift_rows_flags_alert_when_psi_exceeds_threshold() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    reference = {
        "spread": [float(idx) for idx in range(100)],
        "book_imbalance_1pct": [0.0] * 100,
        "book_imbalance_5pct": [0.0] * 100,
        "momentum_1h": [0.0] * 100,
        "momentum_24h": [0.0] * 100,
        "momentum_7d": [0.0] * 100,
        "realized_vol_24h": [0.0] * 100,
        "informed_taker_flow_24h": [0.0] * 100,
        "passive_maker_flow_24h": [0.0] * 100,
        "decayed_directional_flow_24h": [0.0] * 100,
    }
    current = {
        **{
            feature_name: [0.0] * 100
            for feature_name in reference
            if feature_name != "spread"
        },
        "spread": [float(idx + 50) for idx in range(100)],
    }

    rows = build_feature_drift_rows(
        reference_values=reference,
        current_values=current,
        observed_at=observed_at,
        psi_threshold=0.2,
        min_samples=20,
    )
    by_feature = {str(row[0]): row for row in rows}

    assert by_feature["spread"][1] == 100
    assert by_feature["spread"][2] == 100
    assert by_feature["spread"][3] > 0.2
    assert by_feature["spread"][6] is True
    assert by_feature["momentum_1h"][6] is False


@pytest.mark.asyncio
async def test_build_disable_transition_rows_disables_after_negative_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_history(
        ch: object,
        *,
        market_type: str,
        asked_at: datetime,
        lookback_days: int,
    ) -> list[drift_monitor.DailySkillSample]:
        assert market_type == "threshold"
        return [
            drift_monitor.DailySkillSample(
                asked_at=(asked_at.timestamp() - (6 - idx) * 86_400.0),
                model_brier=0.30,
                baseline_brier=0.20,
            )
            for idx in range(7)
        ]

    async def _fake_status_batch(
        ch: object,
        *,
        market_types: list[str],
        asked_at: datetime,
    ) -> dict[str, ModelDisableStatus]:
        assert market_types == ["threshold"]
        return {}

    monkeypatch.setattr(drift_monitor, "load_daily_skill_history", _fake_history)
    monkeypatch.setattr(drift_monitor, "model_disable_status_batch_asof", _fake_status_batch)

    rows = await build_disable_transition_rows(
        object(),
        asked_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        market_types=["threshold"],
        disable_streak_days=7,
        history_lookback_days=60,
    )

    assert len(rows) == 1
    assert rows[0][0] == "threshold"
    assert rows[0][1] == "disabled"


@pytest.mark.asyncio
async def test_build_disable_transition_rows_reenables_on_positive_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_history(
        ch: object,
        *,
        market_type: str,
        asked_at: datetime,
        lookback_days: int,
    ) -> list[drift_monitor.DailySkillSample]:
        return [
            drift_monitor.DailySkillSample(
                asked_at=asked_at.timestamp(),
                model_brier=0.12,
                baseline_brier=0.20,
            )
        ]

    async def _fake_status_batch(
        ch: object,
        *,
        market_types: list[str],
        asked_at: datetime,
    ) -> dict[str, ModelDisableStatus]:
        observed_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
        return {
            "threshold": ModelDisableStatus(
                market_type="threshold",
                is_disabled=True,
                action="disabled",
                consecutive_days=8,
                threshold_days=7,
                reason="8 consecutive days of negative 30d Brier skill",
                event_time=observed_at,
                observed_at=observed_at,
            )
        }

    monkeypatch.setattr(drift_monitor, "load_daily_skill_history", _fake_history)
    monkeypatch.setattr(drift_monitor, "model_disable_status_batch_asof", _fake_status_batch)

    rows = await build_disable_transition_rows(
        object(),
        asked_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        market_types=["threshold"],
        disable_streak_days=7,
        history_lookback_days=60,
    )

    assert len(rows) == 1
    assert rows[0][0] == "threshold"
    assert rows[0][1] == "re_enabled"


@pytest.mark.asyncio
async def test_run_drift_monitor_writes_metrics_and_disable_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_replay_samples(
        ch: object,
        *,
        asked_at: datetime,
        horizon_hours: int,
        resolved_lookback_days: int,
        limit: int,
    ) -> list[DriftReplaySample]:
        return [
            _replay_sample(
                asked_at=asked_at.timestamp() - 2 * 86_400.0,
                predicted_prob=0.8,
                baseline_prob=0.6,
                outcome=1,
                regime="bull_trend",
            )
        ]

    async def _fake_disable_rows(
        ch: object,
        *,
        asked_at: datetime,
        market_types: list[str],
        disable_streak_days: int,
        history_lookback_days: int,
    ) -> list[tuple[object, ...]]:
        assert market_types == ["threshold"]
        return [("threshold", "disabled", 7, 7, "reason", asked_at, asked_at)]

    async def _fake_feature_window_values(
        ch: object,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> dict[str, list[float]]:
        del ch, window_start, window_end
        return {
            "spread": [0.0] * 50,
            "book_imbalance_1pct": [0.0] * 50,
            "book_imbalance_5pct": [0.0] * 50,
            "momentum_1h": [0.0] * 50,
            "momentum_24h": [0.0] * 50,
            "momentum_7d": [0.0] * 50,
            "realized_vol_24h": [0.0] * 50,
            "informed_taker_flow_24h": [0.0] * 50,
            "passive_maker_flow_24h": [0.0] * 50,
            "decayed_directional_flow_24h": [0.0] * 50,
        }

    monkeypatch.setattr(drift_monitor, "load_drift_replay_samples", _fake_replay_samples)
    monkeypatch.setattr(drift_monitor, "build_disable_transition_rows", _fake_disable_rows)
    monkeypatch.setattr(drift_monitor, "load_feature_window_values", _fake_feature_window_values)

    fake_settings = SimpleNamespace(
        drift_monitor_horizon_hours=24,
        drift_monitor_limit=1000,
        drift_monitor_resolved_lookback_days=120,
        drift_monitor_disable_streak_days=7,
        drift_monitor_history_lookback_days=60,
        feature_drift_current_window_days=7,
        feature_drift_reference_window_days=90,
        feature_drift_alert_threshold_psi=0.2,
        feature_drift_min_samples=20,
    )
    monkeypatch.setattr(drift_monitor, "get_settings", lambda: fake_settings)

    ch = _FakeClickHouse()
    report = await run_drift_monitor(
        ch,
        asked_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
    )

    assert report.metric_rows_written > 0
    assert report.feature_metric_rows_written == 10
    assert report.disable_rows_written == 1
    assert report.disabled_market_types == ["threshold"]
    assert [item[0] for item in ch.inserts] == [
        "model_drift_metrics",
        "feature_drift_metrics",
        "model_disable_log",
    ]
