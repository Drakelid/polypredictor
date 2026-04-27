from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from api import m4_audit
from api.m4_audit import (
    AuditSample,
    FamilyAblationStat,
    M4AuditReport,
    ablate_resolution_risk,
    ablate_social_features,
    family_ablations_from_samples,
    family_coverage_from_samples,
    resolution_risk_precision_recall_audit,
    sample_has_resolution_risk,
    sample_has_social,
)
from model import EnsembleSample, MarketType


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


def _make_sample(
    *,
    market_type: MarketType = MarketType.THRESHOLD,
    p_base: float = 0.55,
    market_mid: float = 0.54,
    resolution_risk_score: float | None = None,
    social_post_count_24h: float | None = None,
    social_reach_volume_24h: float | None = None,
    social_novelty_score_latest: float | None = None,
    social_sentiment_dispersion_24h: float | None = None,
    social_tone_shift_24h_vs_7d: float | None = None,
) -> EnsembleSample:
    return EnsembleSample(
        market_type=market_type,
        p_base=p_base,
        market_mid=market_mid,
        resolution_risk_score=resolution_risk_score,
        social_post_count_24h=social_post_count_24h,
        social_reach_volume_24h=social_reach_volume_24h,
        social_novelty_score_latest=social_novelty_score_latest,
        social_sentiment_dispersion_24h=social_sentiment_dispersion_24h,
        social_tone_shift_24h_vs_7d=social_tone_shift_24h_vs_7d,
    )


def _audit_sample(
    sample: EnsembleSample,
    *,
    outcome: int = 1,
    has_event_time: bool = False,
    condition_id: str = "cond-x",
) -> AuditSample:
    asked_at = datetime(2026, 4, 22, 12, tzinfo=UTC)
    return AuditSample(
        condition_id=condition_id,
        asked_at=asked_at,
        resolved_at=asked_at + timedelta(hours=24),
        outcome=outcome,
        sample=sample,
        has_social=sample_has_social(sample),
        has_event_time=has_event_time,
        has_resolution_risk=sample_has_resolution_risk(sample),
    )


# ---------------------------------------------------------------------------
# Predicate helpers
# ---------------------------------------------------------------------------


def test_sample_has_social_returns_true_when_any_field_set() -> None:
    sample = _make_sample(social_post_count_24h=12.0)
    assert sample_has_social(sample) is True


def test_sample_has_social_returns_false_when_all_social_fields_none() -> None:
    sample = _make_sample()
    assert sample_has_social(sample) is False


def test_sample_has_resolution_risk_requires_score() -> None:
    assert sample_has_resolution_risk(_make_sample()) is False
    assert sample_has_resolution_risk(_make_sample(resolution_risk_score=0.42)) is True


# ---------------------------------------------------------------------------
# Ablation primitives
# ---------------------------------------------------------------------------


def test_ablate_social_features_clears_only_social_fields() -> None:
    sample = _make_sample(
        resolution_risk_score=0.4,
        social_post_count_24h=10.0,
        social_reach_volume_24h=200.0,
        social_novelty_score_latest=0.7,
        social_sentiment_dispersion_24h=0.2,
        social_tone_shift_24h_vs_7d=0.1,
    )

    ablated = ablate_social_features(sample)

    assert ablated.social_post_count_24h is None
    assert ablated.social_reach_volume_24h is None
    assert ablated.social_novelty_score_latest is None
    assert ablated.social_sentiment_dispersion_24h is None
    assert ablated.social_tone_shift_24h_vs_7d is None
    # Non-social fields untouched
    assert ablated.resolution_risk_score == pytest.approx(0.4)
    assert ablated.market_mid == pytest.approx(0.54)


def test_ablate_resolution_risk_clears_only_risk_score() -> None:
    sample = _make_sample(
        resolution_risk_score=0.4,
        social_post_count_24h=10.0,
    )

    ablated = ablate_resolution_risk(sample)

    assert ablated.resolution_risk_score is None
    assert ablated.social_post_count_24h == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def test_family_coverage_from_samples_counts_each_family() -> None:
    samples = [
        _audit_sample(_make_sample(social_post_count_24h=5.0), has_event_time=True),
        _audit_sample(_make_sample(resolution_risk_score=0.3), has_event_time=False),
        _audit_sample(_make_sample(), has_event_time=True),
    ]

    report = family_coverage_from_samples(samples)

    assert report.total_replayed == 3
    assert report.samples_with_social == 1
    assert report.samples_with_resolution_risk == 1
    assert report.samples_with_event_time == 2
    assert report.social_coverage_ratio == pytest.approx(1 / 3)
    assert report.event_time_coverage_ratio == pytest.approx(2 / 3)
    assert report.resolution_risk_coverage_ratio == pytest.approx(1 / 3)


def test_family_coverage_from_samples_returns_none_ratios_when_empty() -> None:
    report = family_coverage_from_samples([])

    assert report.total_replayed == 0
    assert report.social_coverage_ratio is None
    assert report.event_time_coverage_ratio is None
    assert report.resolution_risk_coverage_ratio is None


# ---------------------------------------------------------------------------
# Ablation rollup
# ---------------------------------------------------------------------------


class _RegistryStub:
    """Minimal stand-in for an ``EnsembleRegistry``.

    The stub returns a probability that is sensitive to the social and
    resolution-risk fields so the ablation pass can show non-zero deltas.
    """

    def __init__(self) -> None:
        self.calls: int = 0

    def model_for_type(self, market_type: MarketType) -> _RegistryStub | None:
        if market_type is MarketType.MISC:
            return None
        return self

    def predict_probability(self, sample: EnsembleSample) -> float:
        self.calls += 1
        score = sample.p_base
        if sample.social_post_count_24h is not None:
            score += 0.05
        if sample.resolution_risk_score is not None:
            score -= 0.1 * sample.resolution_risk_score
        return max(0.0, min(1.0, score))


def test_family_ablations_from_samples_returns_empty_stats_when_registry_missing() -> None:
    samples = [
        _audit_sample(_make_sample(social_post_count_24h=5.0)),
        _audit_sample(_make_sample(resolution_risk_score=0.3)),
    ]

    stats = family_ablations_from_samples(samples, registry=None)

    assert {stat.family for stat in stats} == {"social", "resolution_risk"}
    for stat in stats:
        assert stat.samples_evaluated == 0
        assert stat.incremental_brier_gain is None
        assert stat.brier_with_signal is None
        assert stat.brier_without_signal is None


def test_family_ablations_from_samples_skips_market_types_without_model() -> None:
    samples = [
        _audit_sample(
            _make_sample(market_type=MarketType.MISC, social_post_count_24h=4.0),
        ),
    ]

    stats = family_ablations_from_samples(samples, registry=_RegistryStub())  # type: ignore[arg-type]

    for stat in stats:
        assert stat.samples_evaluated == 0


def test_family_ablations_from_samples_records_non_zero_delta_when_signal_active() -> None:
    samples = [
        _audit_sample(
            _make_sample(social_post_count_24h=5.0, resolution_risk_score=0.3),
            outcome=1,
        ),
        _audit_sample(
            _make_sample(social_post_count_24h=2.0, resolution_risk_score=0.5),
            outcome=0,
        ),
    ]

    stats = family_ablations_from_samples(samples, registry=_RegistryStub())  # type: ignore[arg-type]

    by_family: dict[str, FamilyAblationStat] = {stat.family: stat for stat in stats}
    assert by_family["social"].samples_evaluated == 2
    assert by_family["social"].samples_with_signal == 2
    assert by_family["social"].non_zero_contribution_samples == 2
    assert by_family["social"].mean_abs_probability_delta is not None
    assert by_family["social"].mean_abs_probability_delta > 0
    assert by_family["resolution_risk"].samples_with_signal == 2
    assert by_family["resolution_risk"].non_zero_contribution_samples == 2
    assert by_family["resolution_risk"].mean_abs_probability_delta is not None
    assert by_family["resolution_risk"].mean_abs_probability_delta > 0


def test_family_ablations_from_samples_zero_delta_when_signal_absent() -> None:
    samples = [
        _audit_sample(_make_sample(), outcome=1),
        _audit_sample(_make_sample(), outcome=0),
    ]

    stats = family_ablations_from_samples(samples, registry=_RegistryStub())  # type: ignore[arg-type]

    by_family = {stat.family: stat for stat in stats}
    # No social or resolution-risk fields present => ablation is a no-op.
    assert by_family["social"].samples_with_signal == 0
    assert by_family["social"].non_zero_contribution_samples == 0
    assert by_family["social"].mean_abs_probability_delta == pytest.approx(0.0)
    assert by_family["resolution_risk"].samples_with_signal == 0
    assert by_family["resolution_risk"].mean_abs_probability_delta == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Resolution-risk precision/recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolution_risk_precision_recall_audit_computes_pr_f1() -> None:
    asked_at = datetime(2026, 4, 24, tzinfo=UTC)
    rows: list[tuple[object, ...]] = [
        (True, True),    # TP
        (True, True),    # TP
        (True, False),   # FN
        (False, True),   # FP
        (False, False),  # TN
        (False, False),  # TN
    ]
    ch = _FakeClickHouse(rows)

    report = await resolution_risk_precision_recall_audit(
        ch,  # type: ignore[arg-type]
        asked_at=asked_at,
        lookback_days=180,
    )

    assert report.total_corpus == 6
    assert report.positive_label_count == 3
    assert report.negative_label_count == 3
    assert report.flagged_count == 3
    assert report.true_positive == 2
    assert report.false_positive == 1
    assert report.true_negative == 2
    assert report.false_negative == 1
    assert report.precision == pytest.approx(2 / 3)
    assert report.recall == pytest.approx(2 / 3)
    assert report.f1 == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_resolution_risk_precision_recall_audit_handles_empty_corpus() -> None:
    ch = _FakeClickHouse([])

    report = await resolution_risk_precision_recall_audit(
        ch,  # type: ignore[arg-type]
        asked_at=datetime(2026, 4, 24, tzinfo=UTC),
        lookback_days=180,
    )

    assert report.total_corpus == 0
    assert report.precision is None
    assert report.recall is None
    assert report.f1 is None


@pytest.mark.asyncio
async def test_resolution_risk_precision_recall_audit_passes_lookback_in_query() -> None:
    asked_at = datetime(2026, 4, 24, 12, tzinfo=UTC)
    ch = _FakeClickHouse([(True, True)])

    await resolution_risk_precision_recall_audit(
        ch,  # type: ignore[arg-type]
        asked_at=asked_at,
        lookback_days=30,
    )

    _, params = ch.calls[0]
    assert params is not None
    assert params["asof"] == asked_at
    assert params["start"] == asked_at - timedelta(days=30)


# ---------------------------------------------------------------------------
# Top-level audit driver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_m4_audit_aggregates_pieces(monkeypatch: pytest.MonkeyPatch) -> None:
    asked_at = datetime(2026, 4, 24, tzinfo=UTC)
    samples = [
        _audit_sample(
            _make_sample(social_post_count_24h=10.0, resolution_risk_score=0.4),
            outcome=1,
            has_event_time=True,
        ),
        _audit_sample(_make_sample(), outcome=0, has_event_time=False),
    ]

    async def _fake_replay(
        ch: object, *, asked_at: datetime, **kwargs: Any
    ) -> list[AuditSample]:
        return samples

    async def _fake_pr(
        ch: object, *, asked_at: datetime, lookback_days: int
    ) -> m4_audit.ResolutionRiskPrecisionRecall:
        return m4_audit.ResolutionRiskPrecisionRecall(
            total_corpus=4,
            positive_label_count=2,
            negative_label_count=2,
            flagged_count=2,
            true_positive=2,
            false_positive=0,
            true_negative=2,
            false_negative=0,
            precision=1.0,
            recall=1.0,
            f1=1.0,
        )

    monkeypatch.setattr(m4_audit, "replay_audit_samples", _fake_replay)
    monkeypatch.setattr(
        m4_audit, "resolution_risk_precision_recall_audit", _fake_pr
    )
    monkeypatch.setattr(
        m4_audit,
        "load_ensemble_registry",
        lambda _path: _RegistryStub(),
    )
    monkeypatch.setattr(
        m4_audit,
        "get_settings",
        lambda: SimpleNamespace(ensemble_registry_file="/tmp/registry.json"),
    )

    report: M4AuditReport = await m4_audit.run_m4_audit(
        object(),  # type: ignore[arg-type]
        asked_at=asked_at,
    )

    assert report.as_of == asked_at
    assert report.family_coverage.total_replayed == 2
    assert report.family_coverage.samples_with_social == 1
    assert report.family_coverage.samples_with_event_time == 1
    assert report.family_coverage.samples_with_resolution_risk == 1
    assert {stat.family for stat in report.family_ablations} == {
        "social",
        "resolution_risk",
    }
    assert report.resolution_risk_precision_recall.precision == pytest.approx(1.0)
