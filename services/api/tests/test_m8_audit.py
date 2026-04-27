from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api import m8_audit
from api.drift_report import DriftSnapshot, ModelDriftMetric
from api.m8_audit import (
    DEFAULT_BRIER_SKILL_THRESHOLD,
    DEFAULT_COVERAGE_HI,
    DEFAULT_COVERAGE_LO,
    DEFAULT_WINDOW_LABEL,
    build_m8_audit_report,
    evaluate_cells,
    summarize_brier_skill,
    summarize_conformal_coverage,
)


def _metric(
    *,
    window_label: str = "30d",
    market_type: str = "threshold",
    ttr_bucket: str = "1d_7d",
    regime: str | None = "bull_trend",
    sample_count: int = 25,
    brier_skill: float | None = 0.10,
    coverage: float | None = 0.80,
) -> ModelDriftMetric:
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    return ModelDriftMetric(
        window_label=window_label,
        market_type=market_type,
        ttr_bucket=ttr_bucket,
        regime=regime,
        sample_count=sample_count,
        brier=0.20,
        ece=0.04,
        coverage=coverage,
        avg_predicted=0.55,
        avg_outcome=0.5,
        baseline_brier=0.25,
        brier_skill=brier_skill,
        observed_at=observed_at,
    )


# ---------------------------------------------------------------------------
# evaluate_cells
# ---------------------------------------------------------------------------


def test_evaluate_cells_filters_to_window_label() -> None:
    metrics = [
        _metric(window_label="7d", market_type="threshold"),
        _metric(window_label="30d", market_type="threshold"),
        _metric(window_label="90d", market_type="range"),
    ]

    cells = evaluate_cells(metrics)

    assert len(cells) == 1
    assert cells[0].market_type == "threshold"


def test_evaluate_cells_excludes_overall_aggregate_rows() -> None:
    metrics = [
        _metric(market_type="overall", ttr_bucket="overall"),
        _metric(market_type="threshold", ttr_bucket="overall"),
        _metric(market_type="overall", ttr_bucket="1d_7d"),
        _metric(market_type="threshold", ttr_bucket="1d_7d"),
    ]

    cells = evaluate_cells(metrics)

    assert len(cells) == 1
    assert cells[0].market_type == "threshold"
    assert cells[0].ttr_bucket == "1d_7d"


def test_evaluate_cells_marks_skill_and_coverage_flags() -> None:
    metrics = [
        _metric(brier_skill=0.06, coverage=0.79),
        _metric(brier_skill=0.04, coverage=0.79),
        _metric(brier_skill=0.10, coverage=0.95),
        _metric(brier_skill=None, coverage=None),
    ]

    cells = evaluate_cells(metrics)

    assert cells[0].meets_skill_threshold is True
    assert cells[0].coverage_in_target_band is True
    assert cells[1].meets_skill_threshold is False
    assert cells[1].coverage_in_target_band is True
    assert cells[2].meets_skill_threshold is True
    assert cells[2].coverage_in_target_band is False
    assert cells[3].meets_skill_threshold is False
    assert cells[3].coverage_in_target_band is False


def test_evaluate_cells_uses_inclusive_coverage_band_endpoints() -> None:
    metrics = [
        _metric(coverage=DEFAULT_COVERAGE_LO),
        _metric(coverage=DEFAULT_COVERAGE_HI),
    ]

    cells = evaluate_cells(metrics)

    assert cells[0].coverage_in_target_band is True
    assert cells[1].coverage_in_target_band is True


def test_evaluate_cells_uses_inclusive_skill_threshold() -> None:
    metric = _metric(brier_skill=DEFAULT_BRIER_SKILL_THRESHOLD)

    cells = evaluate_cells([metric])

    assert cells[0].meets_skill_threshold is True


# ---------------------------------------------------------------------------
# summarize_brier_skill
# ---------------------------------------------------------------------------


def test_summarize_brier_skill_aggregates_by_market_type() -> None:
    cells = evaluate_cells(
        [
            _metric(market_type="threshold", ttr_bucket="lt_1d", brier_skill=0.07),
            _metric(market_type="threshold", ttr_bucket="1d_7d", brier_skill=0.04),
            _metric(market_type="range", ttr_bucket="1d_7d", brier_skill=0.06),
        ]
    )

    summary = summarize_brier_skill(
        cells,
        window_label=DEFAULT_WINDOW_LABEL,
        threshold=DEFAULT_BRIER_SKILL_THRESHOLD,
    )

    assert summary.cells_total == 3
    assert summary.cells_meeting_threshold == 2
    assert summary.cells_below_threshold == 1
    assert summary.meets_in_every_cell is False
    assert summary.by_market_type_total == {"range": 1, "threshold": 2}
    assert summary.by_market_type_meeting == {"range": 1, "threshold": 1}


def test_summarize_brier_skill_meets_in_every_cell_when_all_pass() -> None:
    cells = evaluate_cells(
        [
            _metric(market_type="threshold", ttr_bucket="lt_1d", brier_skill=0.07),
            _metric(market_type="range", ttr_bucket="1d_7d", brier_skill=0.06),
        ]
    )

    summary = summarize_brier_skill(
        cells,
        window_label=DEFAULT_WINDOW_LABEL,
        threshold=DEFAULT_BRIER_SKILL_THRESHOLD,
    )

    assert summary.meets_in_every_cell is True
    assert summary.cells_below_threshold == 0


def test_summarize_brier_skill_handles_empty_cells() -> None:
    summary = summarize_brier_skill(
        [],
        window_label=DEFAULT_WINDOW_LABEL,
        threshold=DEFAULT_BRIER_SKILL_THRESHOLD,
    )

    assert summary.cells_total == 0
    # Empty set is not "every cell passes" — the criterion is not yet
    # measurable, so meets_in_every_cell stays False.
    assert summary.meets_in_every_cell is False


def test_summarize_brier_skill_counts_skill_present_separately_from_passing() -> None:
    cells = evaluate_cells(
        [
            _metric(market_type="threshold", brier_skill=0.07),
            _metric(market_type="range", brier_skill=None),
        ]
    )

    summary = summarize_brier_skill(
        cells,
        window_label=DEFAULT_WINDOW_LABEL,
        threshold=DEFAULT_BRIER_SKILL_THRESHOLD,
    )

    assert summary.cells_total == 2
    assert summary.cells_with_skill == 1
    assert summary.cells_meeting_threshold == 1


# ---------------------------------------------------------------------------
# summarize_conformal_coverage
# ---------------------------------------------------------------------------


def test_summarize_conformal_coverage_counts_in_band_per_market_type() -> None:
    cells = evaluate_cells(
        [
            _metric(market_type="threshold", coverage=0.80),
            _metric(market_type="threshold", ttr_bucket="lt_1d", coverage=0.70),
            _metric(market_type="range", coverage=0.81),
        ]
    )

    summary = summarize_conformal_coverage(
        cells,
        window_label=DEFAULT_WINDOW_LABEL,
        target_lo=DEFAULT_COVERAGE_LO,
        target_hi=DEFAULT_COVERAGE_HI,
    )

    assert summary.cells_total == 3
    assert summary.cells_in_target_band == 2
    assert summary.cells_outside_target_band == 1
    assert summary.meets_in_every_cell is False
    assert summary.by_market_type_total == {"range": 1, "threshold": 2}
    assert summary.by_market_type_in_band == {"range": 1, "threshold": 1}


def test_summarize_conformal_coverage_counts_coverage_present_separately() -> None:
    cells = evaluate_cells(
        [
            _metric(coverage=0.80),
            _metric(coverage=None),
        ]
    )

    summary = summarize_conformal_coverage(
        cells,
        window_label=DEFAULT_WINDOW_LABEL,
        target_lo=DEFAULT_COVERAGE_LO,
        target_hi=DEFAULT_COVERAGE_HI,
    )

    assert summary.cells_with_coverage == 1
    assert summary.cells_in_target_band == 1
    assert summary.cells_outside_target_band == 1


def test_summarize_conformal_coverage_handles_empty_cells() -> None:
    summary = summarize_conformal_coverage(
        [],
        window_label=DEFAULT_WINDOW_LABEL,
        target_lo=DEFAULT_COVERAGE_LO,
        target_hi=DEFAULT_COVERAGE_HI,
    )

    assert summary.cells_total == 0
    assert summary.meets_in_every_cell is False


# ---------------------------------------------------------------------------
# build_m8_audit_report
# ---------------------------------------------------------------------------


def test_build_m8_audit_report_with_no_snapshot() -> None:
    as_of = datetime(2026, 4, 25, tzinfo=UTC)
    report = build_m8_audit_report(None, as_of=as_of)

    assert report.snapshot_observed_at is None
    assert report.cells == []
    assert report.brier_skill.cells_total == 0
    assert report.conformal_coverage.cells_total == 0


def test_build_m8_audit_report_emits_per_cell_evaluation() -> None:
    as_of = datetime(2026, 4, 25, tzinfo=UTC)
    observed_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    snapshot = DriftSnapshot(
        observed_at=observed_at,
        model_metrics=[
            _metric(market_type="overall", ttr_bucket="overall"),
            _metric(market_type="threshold", ttr_bucket="lt_1d", brier_skill=0.07, coverage=0.80),
            _metric(market_type="range", ttr_bucket="1d_7d", brier_skill=0.02, coverage=0.95),
        ],
        feature_metrics=[],
    )

    report = build_m8_audit_report(snapshot, as_of=as_of)

    assert report.snapshot_observed_at == observed_at
    assert len(report.cells) == 2
    assert report.brier_skill.cells_meeting_threshold == 1
    assert report.brier_skill.meets_in_every_cell is False
    assert report.conformal_coverage.cells_in_target_band == 1
    assert report.conformal_coverage.meets_in_every_cell is False


# ---------------------------------------------------------------------------
# run_m8_audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_m8_audit_invokes_drift_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    asked_at = datetime(2026, 4, 25, tzinfo=UTC)
    captured: dict[str, object] = {}

    async def _fake_loader(ch: object, *, asked_at: datetime) -> DriftSnapshot:
        captured["asked_at"] = asked_at
        return DriftSnapshot(
            observed_at=asked_at,
            model_metrics=[
                _metric(market_type="threshold", brier_skill=0.07, coverage=0.80)
            ],
            feature_metrics=[],
        )

    monkeypatch.setattr(m8_audit, "latest_drift_snapshot_asof", _fake_loader)

    report = await m8_audit.run_m8_audit(object(), asked_at=asked_at)  # type: ignore[arg-type]

    assert captured["asked_at"] == asked_at
    assert report.brier_skill.cells_meeting_threshold == 1
    assert report.conformal_coverage.cells_in_target_band == 1
