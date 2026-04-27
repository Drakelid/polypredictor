"""Executable M4 exit-criteria audit helpers.

This module turns the remaining M4 checks into measurable evidence:

* social, event-time, and resolution-risk feature-family coverage on the
  PIT-replayed resolved-market corpus
* per-family ablation Brier deltas over the same replay (the new families
  introduced by M4 — social, resolution-risk — are the ones audited here;
  M3 families are already covered by :mod:`api.signal_ablation` and
  :mod:`api.m3_audit`)
* resolution-risk precision/recall against the persisted
  ``resolution_risk_corpus`` (M4.4 training corpus)

Like the M1/M2/M3/M7 audits, this produces evidence but does not self-certify
the exit criteria as passed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import EnsembleSample

from .backtest_walk_forward import load_resolved_market_corpus
from .clickhouse import get_async_client
from .ensemble_registry import EnsembleRegistry, load_ensemble_registry
from .event_time import event_time_context_asof
from .markets import model_for_market
from .settings import get_settings

SOCIAL_FIELDS: tuple[str, ...] = (
    "social_post_count_24h",
    "social_reach_volume_24h",
    "social_novelty_score_latest",
    "social_sentiment_dispersion_24h",
    "social_tone_shift_24h_vs_7d",
)

AUDITED_FAMILIES: tuple[str, ...] = ("social", "resolution_risk")


@dataclass(frozen=True)
class AuditSample:
    """One PIT-replayed resolved sample used by both coverage and ablation."""

    condition_id: str
    asked_at: datetime
    resolved_at: datetime
    outcome: int
    sample: EnsembleSample
    has_social: bool
    has_event_time: bool
    has_resolution_risk: bool


@dataclass(frozen=True)
class FamilyCoverageReport:
    total_replayed: int
    samples_with_social: int
    samples_with_event_time: int
    samples_with_resolution_risk: int
    social_coverage_ratio: float | None
    event_time_coverage_ratio: float | None
    resolution_risk_coverage_ratio: float | None


@dataclass(frozen=True)
class FamilyAblationStat:
    family: str
    samples_evaluated: int
    samples_with_signal: int
    non_zero_contribution_samples: int
    mean_abs_probability_delta: float | None
    brier_with_signal: float | None
    brier_without_signal: float | None
    incremental_brier_gain: float | None


@dataclass(frozen=True)
class ResolutionRiskPrecisionRecall:
    total_corpus: int
    positive_label_count: int
    negative_label_count: int
    flagged_count: int
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: float | None
    recall: float | None
    f1: float | None


@dataclass(frozen=True)
class M4AuditReport:
    as_of: datetime
    horizon_hours: int
    resolved_lookback_days: int
    resolved_limit: int
    resolution_risk_corpus_lookback_days: int
    family_coverage: FamilyCoverageReport
    family_ablations: list[FamilyAblationStat]
    resolution_risk_precision_recall: ResolutionRiskPrecisionRecall


def sample_has_social(sample: EnsembleSample | None) -> bool:
    if sample is None:
        return False
    return any(getattr(sample, name) is not None for name in SOCIAL_FIELDS)


def sample_has_resolution_risk(sample: EnsembleSample | None) -> bool:
    return sample is not None and sample.resolution_risk_score is not None


def ablate_social_features(sample: EnsembleSample) -> EnsembleSample:
    return replace(sample, **{name: None for name in SOCIAL_FIELDS})


def ablate_resolution_risk(sample: EnsembleSample) -> EnsembleSample:
    return replace(sample, resolution_risk_score=None)


def _ablate_family(sample: EnsembleSample, family: str) -> EnsembleSample:
    if family == "social":
        return ablate_social_features(sample)
    if family == "resolution_risk":
        return ablate_resolution_risk(sample)
    raise ValueError(f"unknown M4 ablation family: {family}")


def _signal_present(family: str, sample: EnsembleSample) -> bool:
    if family == "social":
        return sample_has_social(sample)
    if family == "resolution_risk":
        return sample_has_resolution_risk(sample)
    raise ValueError(f"unknown M4 ablation family: {family}")


async def replay_audit_samples(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    horizon_hours: int = 24,
    resolved_lookback_days: int = 90,
    resolved_limit: int = 500,
) -> list[AuditSample]:
    """Replay the resolved corpus through the served model and capture samples."""
    corpus_rows, _ = await load_resolved_market_corpus(
        ch,
        resolved_from=asked_at - timedelta(days=resolved_lookback_days),
        resolved_to=asked_at,
        limit=resolved_limit,
    )
    samples: list[AuditSample] = []
    for corpus_row in corpus_rows:
        if corpus_row.resolved_outcome not in {"YES", "NO"}:
            continue
        sample_asked_at = corpus_row.resolved_at - timedelta(hours=horizon_hours)
        detail = await model_for_market(
            ch,
            condition_id=corpus_row.condition_id,
            asked_at=sample_asked_at,
        )
        if detail is None or detail.ensemble_sample is None:
            continue
        ensemble_sample = detail.ensemble_sample
        event_ctx = await event_time_context_asof(
            ch,
            condition_id=corpus_row.condition_id,
            asked_at=sample_asked_at,
        )
        has_event_time = (
            event_ctx.next_event is not None or event_ctx.previous_event is not None
        )
        samples.append(
            AuditSample(
                condition_id=corpus_row.condition_id,
                asked_at=sample_asked_at,
                resolved_at=corpus_row.resolved_at,
                outcome=1 if corpus_row.resolved_outcome == "YES" else 0,
                sample=ensemble_sample,
                has_social=sample_has_social(ensemble_sample),
                has_event_time=has_event_time,
                has_resolution_risk=sample_has_resolution_risk(ensemble_sample),
            )
        )
    return samples


def family_coverage_from_samples(
    samples: list[AuditSample],
) -> FamilyCoverageReport:
    total = len(samples)
    with_social = sum(1 for sample in samples if sample.has_social)
    with_event_time = sum(1 for sample in samples if sample.has_event_time)
    with_resolution_risk = sum(1 for sample in samples if sample.has_resolution_risk)
    return FamilyCoverageReport(
        total_replayed=total,
        samples_with_social=with_social,
        samples_with_event_time=with_event_time,
        samples_with_resolution_risk=with_resolution_risk,
        social_coverage_ratio=(with_social / total) if total else None,
        event_time_coverage_ratio=(with_event_time / total) if total else None,
        resolution_risk_coverage_ratio=(with_resolution_risk / total) if total else None,
    )


def family_ablations_from_samples(
    samples: list[AuditSample],
    *,
    registry: EnsembleRegistry | None,
    families: tuple[str, ...] = AUDITED_FAMILIES,
) -> list[FamilyAblationStat]:
    if registry is None:
        return [_empty_family_stat(family) for family in families]
    accumulators: dict[str, dict[str, list[float] | int]] = {
        family: {
            "samples_with_signal": 0,
            "non_zero": 0,
            "deltas": [],
            "full_brier": [],
            "ablated_brier": [],
        }
        for family in families
    }
    for audit_sample in samples:
        model = registry.model_for_type(audit_sample.sample.market_type)
        if model is None:
            continue
        full_prob = model.predict_probability(audit_sample.sample)
        outcome = float(audit_sample.outcome)
        full_brier = (full_prob - outcome) ** 2
        for family in families:
            ablated = _ablate_family(audit_sample.sample, family)
            ablated_prob = model.predict_probability(ablated)
            ablated_brier = (ablated_prob - outcome) ** 2
            delta = abs(full_prob - ablated_prob)
            acc = accumulators[family]
            acc["full_brier"].append(full_brier)  # type: ignore[union-attr]
            acc["ablated_brier"].append(ablated_brier)  # type: ignore[union-attr]
            acc["deltas"].append(delta)  # type: ignore[union-attr]
            if _signal_present(family, audit_sample.sample):
                acc["samples_with_signal"] = int(acc["samples_with_signal"]) + 1  # type: ignore[arg-type]
            if delta > 1e-9:
                acc["non_zero"] = int(acc["non_zero"]) + 1  # type: ignore[arg-type]
    stats: list[FamilyAblationStat] = []
    for family in families:
        acc = accumulators[family]
        full_brier_list: list[float] = acc["full_brier"]  # type: ignore[assignment]
        ablated_brier_list: list[float] = acc["ablated_brier"]  # type: ignore[assignment]
        deltas: list[float] = acc["deltas"]  # type: ignore[assignment]
        n = len(full_brier_list)
        if n == 0:
            stats.append(_empty_family_stat(family))
            continue
        mean_full = sum(full_brier_list) / n
        mean_abl = sum(ablated_brier_list) / n
        mean_delta = sum(deltas) / n
        stats.append(
            FamilyAblationStat(
                family=family,
                samples_evaluated=n,
                samples_with_signal=int(acc["samples_with_signal"]),  # type: ignore[arg-type]
                non_zero_contribution_samples=int(acc["non_zero"]),  # type: ignore[arg-type]
                mean_abs_probability_delta=mean_delta,
                brier_with_signal=mean_full,
                brier_without_signal=mean_abl,
                incremental_brier_gain=mean_abl - mean_full,
            )
        )
    return stats


def _empty_family_stat(family: str) -> FamilyAblationStat:
    return FamilyAblationStat(
        family=family,
        samples_evaluated=0,
        samples_with_signal=0,
        non_zero_contribution_samples=0,
        mean_abs_probability_delta=None,
        brier_with_signal=None,
        brier_without_signal=None,
        incremental_brier_gain=None,
    )


async def resolution_risk_precision_recall_audit(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_days: int = 180,
) -> ResolutionRiskPrecisionRecall:
    """Compute precision/recall on the held-out UMA-dispute set.

    The query reads the most recent corpus row per market within the lookback
    window. ``had_dispute`` is treated as the ground-truth label and
    ``risk_flagged`` is the classifier prediction recorded at first resolution.
    """
    result = await ch.query(
        """
        SELECT had_dispute, risk_flagged
        FROM resolution_risk_corpus
        WHERE observed_at <= {asof:DateTime64(3)}
          AND first_resolution_event_time >= {start:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
        """,
        parameters={
            "asof": asked_at,
            "start": asked_at - timedelta(days=lookback_days),
        },
    )
    tp = fp = tn = fn = 0
    positive = 0
    flagged = 0
    for row in result.result_rows:
        had_dispute = bool(row[0])
        risk_flagged = bool(row[1])
        if had_dispute:
            positive += 1
        if risk_flagged:
            flagged += 1
        if had_dispute and risk_flagged:
            tp += 1
        elif had_dispute and not risk_flagged:
            fn += 1
        elif not had_dispute and risk_flagged:
            fp += 1
        else:
            tn += 1
    total = tp + fp + tn + fn
    precision = (tp / (tp + fp)) if (tp + fp) else None
    recall = (tp / (tp + fn)) if (tp + fn) else None
    f1: float | None = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2.0 * precision * recall / (precision + recall)
    return ResolutionRiskPrecisionRecall(
        total_corpus=total,
        positive_label_count=positive,
        negative_label_count=total - positive,
        flagged_count=flagged,
        true_positive=tp,
        false_positive=fp,
        true_negative=tn,
        false_negative=fn,
        precision=precision,
        recall=recall,
        f1=f1,
    )


async def run_m4_audit(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
    horizon_hours: int = 24,
    resolved_lookback_days: int = 90,
    resolved_limit: int = 500,
    resolution_risk_corpus_lookback_days: int = 180,
) -> M4AuditReport:
    as_of = asked_at or datetime.now(tz=UTC)
    settings = get_settings()
    registry = load_ensemble_registry(settings.ensemble_registry_file)
    samples = await replay_audit_samples(
        ch,
        asked_at=as_of,
        horizon_hours=horizon_hours,
        resolved_lookback_days=resolved_lookback_days,
        resolved_limit=resolved_limit,
    )
    coverage = family_coverage_from_samples(samples)
    ablations = family_ablations_from_samples(samples, registry=registry)
    pr = await resolution_risk_precision_recall_audit(
        ch,
        asked_at=as_of,
        lookback_days=resolution_risk_corpus_lookback_days,
    )
    return M4AuditReport(
        as_of=as_of,
        horizon_hours=horizon_hours,
        resolved_lookback_days=resolved_lookback_days,
        resolved_limit=resolved_limit,
        resolution_risk_corpus_lookback_days=resolution_risk_corpus_lookback_days,
        family_coverage=coverage,
        family_ablations=ablations,
        resolution_risk_precision_recall=pr,
    )


def _report_to_json(report: M4AuditReport) -> str:
    payload = asdict(report)
    coverage = payload.get("family_coverage")
    if isinstance(coverage, dict):
        for key in (
            "social_coverage_ratio",
            "event_time_coverage_ratio",
            "resolution_risk_coverage_ratio",
        ):
            value = coverage.get(key)
            if isinstance(value, float):
                coverage[key] = round(value, 6)
    for stat in payload.get("family_ablations", []):
        for key in (
            "mean_abs_probability_delta",
            "brier_with_signal",
            "brier_without_signal",
            "incremental_brier_gain",
        ):
            value = stat.get(key)
            if isinstance(value, float):
                stat[key] = round(value, 6)
    pr = payload.get("resolution_risk_precision_recall")
    if isinstance(pr, dict):
        for key in ("precision", "recall", "f1"):
            value = pr.get(key)
            if isinstance(value, float):
                pr[key] = round(value, 6)
    return json.dumps(payload, indent=2, default=str)


async def _main() -> None:
    ch = await get_async_client()
    try:
        report = await run_m4_audit(ch)
        print(_report_to_json(report))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
