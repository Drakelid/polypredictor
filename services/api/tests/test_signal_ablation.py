from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from api import signal_ablation
from api.signal_ablation import (
    SignalArchiveStatus,
    SignalFamilyAblation,
    ablate_signal_family,
    bootstrap_mean_ci,
    build_signal_archive_transition_rows,
    run_signal_ablation,
    summarize_signal_family,
)
from model import EnsembleSample, MarketType


class _FakeClickHouse:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[tuple[object, ...]], tuple[str, ...]]] = []

    async def insert(
        self,
        table: str,
        rows: list[tuple[object, ...]],
        *,
        column_names: tuple[str, ...],
    ) -> None:
        self.inserts.append((table, list(rows), column_names))


def test_ablate_signal_family_clears_only_requested_feature() -> None:
    sample = EnsembleSample(
        market_type=MarketType.THRESHOLD,
        p_base=0.55,
        market_mid=0.54,
        sibling_implied_prior=0.6,
        smart_money_consensus=0.2,
        holder_concentration=0.7,
        resolution_risk_score=0.4,
        adversarial_flow_score=0.3,
        asked_at=0.0,
    )

    ablated = ablate_signal_family(sample, "smart_money")

    assert ablated.smart_money_consensus is None
    assert ablated.holder_concentration == pytest.approx(0.7)
    assert ablated.sibling_implied_prior == pytest.approx(0.6)


def test_bootstrap_mean_ci_is_deterministic() -> None:
    values = [0.01, 0.02, 0.03, 0.04]

    lo1, hi1 = bootstrap_mean_ci(values, iterations=200, ci_level=0.95, seed=7)
    lo2, hi2 = bootstrap_mean_ci(values, iterations=200, ci_level=0.95, seed=7)

    assert lo1 == pytest.approx(lo2)
    assert hi1 == pytest.approx(hi2)
    assert lo1 is not None and hi1 is not None
    assert lo1 <= hi1


def test_summarize_signal_family_recommends_archive_when_ci_includes_zero() -> None:
    deltas = [
        signal_ablation._FamilySampleDelta(0.25, 0.26, 0.01, True),
        signal_ablation._FamilySampleDelta(0.24, 0.23, 0.01, True),
        signal_ablation._FamilySampleDelta(0.21, 0.22, 0.01, True),
        signal_ablation._FamilySampleDelta(0.23, 0.22, 0.01, True),
    ]

    report = summarize_signal_family(
        "smart_money",
        deltas,
        ci_level=0.95,
        bootstrap_iterations=300,
        min_samples=4,
        model_version="per_type_ensemble_v1",
        horizon_hours=24,
        resolved_lookback_days=180,
    )

    assert report.sample_count == 4
    assert report.signal_present_samples == 4
    assert report.archive_recommended is True
    assert report.bootstrap_ci_lo is not None
    assert report.bootstrap_ci_hi is not None
    assert report.bootstrap_ci_lo <= 0.0 <= report.bootstrap_ci_hi


def test_build_signal_archive_transition_rows_archives_and_reenables() -> None:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    archive_report = SignalFamilyAblation(
        signal_family="smart_money",
        sample_count=80,
        signal_present_samples=80,
        non_zero_contribution_samples=50,
        mean_abs_probability_delta=0.01,
        brier_with_signal=0.20,
        brier_without_signal=0.20,
        incremental_brier_gain=0.0,
        bootstrap_ci_lo=-0.01,
        bootstrap_ci_hi=0.01,
        ci_level=0.95,
        bootstrap_iterations=500,
        archive_recommended=True,
        model_version="per_type_ensemble_v1",
        horizon_hours=24,
        resolved_lookback_days=180,
    )
    enabled_report = SignalFamilyAblation(
        signal_family="sibling_prior",
        sample_count=80,
        signal_present_samples=80,
        non_zero_contribution_samples=60,
        mean_abs_probability_delta=0.02,
        brier_with_signal=0.18,
        brier_without_signal=0.20,
        incremental_brier_gain=0.02,
        bootstrap_ci_lo=0.01,
        bootstrap_ci_hi=0.03,
        ci_level=0.95,
        bootstrap_iterations=500,
        archive_recommended=False,
        model_version="per_type_ensemble_v1",
        horizon_hours=24,
        resolved_lookback_days=180,
    )
    current_status = {
        "sibling_prior": SignalArchiveStatus(
            signal_family="sibling_prior",
            is_archived=True,
            action="archived",
            sample_count=50,
            incremental_brier_gain=0.0,
            bootstrap_ci_lo=-0.01,
            bootstrap_ci_hi=0.01,
            ci_level=0.95,
            reason="prior run",
            event_time=observed_at,
            observed_at=observed_at,
        )
    }

    rows = build_signal_archive_transition_rows(
        [archive_report, enabled_report],
        current_status=current_status,
        observed_at=observed_at,
    )

    assert [row[1] for row in rows] == ["archived", "re_enabled"]
    assert rows[0][0] == "smart_money"
    assert rows[1][0] == "sibling_prior"


@pytest.mark.asyncio
async def test_run_signal_ablation_writes_metrics_and_archive_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = [
        SignalFamilyAblation(
            signal_family="smart_money",
            sample_count=100,
            signal_present_samples=100,
            non_zero_contribution_samples=70,
            mean_abs_probability_delta=0.02,
            brier_with_signal=0.18,
            brier_without_signal=0.19,
            incremental_brier_gain=0.01,
            bootstrap_ci_lo=-0.002,
            bootstrap_ci_hi=0.018,
            ci_level=0.95,
            bootstrap_iterations=500,
            archive_recommended=True,
            model_version="per_type_ensemble_v1",
            horizon_hours=24,
            resolved_lookback_days=180,
        )
    ]

    async def _fake_reports(
        ch: object,
        *,
        asked_at: datetime,
        horizon_hours: int,
        resolved_lookback_days: int,
        resolved_limit: int,
        bootstrap_iterations: int,
        ci_level: float,
        min_samples: int,
    ) -> list[SignalFamilyAblation]:
        del ch, asked_at, horizon_hours, resolved_lookback_days, resolved_limit
        del bootstrap_iterations, ci_level, min_samples
        return reports

    async def _fake_status_batch(
        ch: object,
        *,
        signal_families: list[str],
        asked_at: datetime,
    ) -> dict[str, SignalArchiveStatus]:
        del ch, signal_families, asked_at
        return {}

    fake_settings = SimpleNamespace(
        signal_ablation_horizon_hours=24,
        signal_ablation_resolved_lookback_days=180,
        signal_ablation_limit=1000,
        signal_ablation_bootstrap_iterations=500,
        signal_ablation_ci_level=0.95,
        signal_ablation_min_samples=50,
    )
    monkeypatch.setattr(signal_ablation, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(signal_ablation, "signal_family_ablation_reports", _fake_reports)
    monkeypatch.setattr(signal_ablation, "signal_archive_status_batch_asof", _fake_status_batch)

    ch = _FakeClickHouse()
    report = await run_signal_ablation(
        ch,  # type: ignore[arg-type]
        asked_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
    )

    assert report.metric_rows_written == 1
    assert report.archive_rows_written == 1
    assert report.archived_families == ["smart_money"]
    assert [item[0] for item in ch.inserts] == [
        "signal_ablation_metrics",
        "signal_archive_log",
    ]
