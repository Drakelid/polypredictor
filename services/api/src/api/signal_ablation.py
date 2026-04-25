"""Monthly per-signal ablation driver (M6.3 / PRD §6.9).

This is the executable evidence path for "per-signal incremental value":

1. Replay resolved binary markets point-in-time at a fixed pre-resolution
   horizon.
2. Reconstruct the served ensemble sample for each market.
3. Remove one model-side signal family at a time and compare Brier against the
   full served model.
4. Bootstrap the incremental Brier gain for each family.
5. Append metrics into ``signal_ablation_metrics`` and archive / re-enable
   transitions into ``signal_archive_log`` when the CI-based recommendation
   flips.

The archiving rule matches the PRD wording exactly: archive a signal family
when the bootstrap CI of its incremental Brier gain includes zero.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta

import numpy as np
from clickhouse_connect.driver.asyncclient import AsyncClient
from ingest.writers import (
    SIGNAL_ABLATION_METRICS_COLS,
    SIGNAL_ARCHIVE_LOG_COLS,
    signal_ablation_metric_row,
    signal_archive_log_row,
)
from model import ClassificationResult, DeribitClient, EnsembleSample, MarketType

from .adversarial_flow import AdversarialFlowContext, adversarial_flow_asof
from .backtest_walk_forward import load_resolved_market_corpus
from .classifications import classification_asof, classifications_batch_asof
from .clickhouse import get_async_client
from .cme_fedwatch import CMEFedWatchClient
from .concentration import concentration_asof
from .ensemble_registry import EnsembleRegistry, load_ensemble_registry
from .features import feature_snapshot_asof
from .long_tail_priors import load_long_tail_resolved_markets, long_tail_prior_context
from .markets import (
    BaselineContext,
    _discrete_config_from_settings,
    _ensemble_sample_for_row,
    _latest_markets,
    _latest_mids_for_tokens,
    _multi_outcome_contexts_for_markets,
    _pipeline_for_row,
    _sibling_market_rows,
    _sibling_priors_by_condition,
)
from .model_status import model_disable_status_asof
from .regime import regime_label_asof
from .resolution_risk import resolution_risk_asof
from .settings import get_settings
from .smart_money import smart_money_asof

SIGNAL_FAMILIES = (
    "smart_money",
    "holder_concentration",
    "sibling_prior",
    "resolution_risk",
    "adversarial_flow",
)


@dataclass(frozen=True)
class SignalFamilyAblation:
    signal_family: str
    sample_count: int
    signal_present_samples: int
    non_zero_contribution_samples: int
    mean_abs_probability_delta: float | None
    brier_with_signal: float | None
    brier_without_signal: float | None
    incremental_brier_gain: float | None
    bootstrap_ci_lo: float | None
    bootstrap_ci_hi: float | None
    ci_level: float
    bootstrap_iterations: int
    archive_recommended: bool
    model_version: str
    horizon_hours: int
    resolved_lookback_days: int


@dataclass(frozen=True)
class SignalArchiveStatus:
    signal_family: str
    is_archived: bool
    action: str
    sample_count: int
    incremental_brier_gain: float | None
    bootstrap_ci_lo: float | None
    bootstrap_ci_hi: float | None
    ci_level: float
    reason: str
    event_time: datetime
    observed_at: datetime


@dataclass(frozen=True)
class SignalAblationReport:
    asked_at: datetime
    horizon_hours: int
    resolved_lookback_days: int
    resolved_limit: int
    bootstrap_iterations: int
    ci_level: float
    metric_rows_written: int
    archive_rows_written: int
    archived_families: list[str]
    re_enabled_families: list[str]
    by_family: list[SignalFamilyAblation]


@dataclass(frozen=True)
class _FamilySampleDelta:
    full_brier: float
    ablated_brier: float
    probability_delta: float
    signal_present: bool


def _archive_status_from_row(row: tuple[object, ...]) -> SignalArchiveStatus:
    action = str(row[1])
    return SignalArchiveStatus(
        signal_family=str(row[0]),
        is_archived=action == "archived",
        action=action,
        sample_count=int(row[2]),
        incremental_brier_gain=float(row[3]) if row[3] is not None else None,
        bootstrap_ci_lo=float(row[4]) if row[4] is not None else None,
        bootstrap_ci_hi=float(row[5]) if row[5] is not None else None,
        ci_level=float(row[6]),
        reason=str(row[7]),
        event_time=row[8],  # type: ignore[arg-type]
        observed_at=row[9],  # type: ignore[arg-type]
    )


def _signal_present(
    family: str,
    sample: EnsembleSample,
    *,
    adversarial_flow: AdversarialFlowContext | None,
) -> bool:
    if family == "smart_money":
        return sample.smart_money_consensus is not None
    if family == "holder_concentration":
        return sample.holder_concentration is not None
    if family == "sibling_prior":
        return sample.sibling_implied_prior is not None
    if family == "resolution_risk":
        return sample.resolution_risk_score is not None
    if family == "adversarial_flow":
        return (
            sample.adversarial_flow_score is not None
            and abs(sample.adversarial_flow_score) > 1e-12
            and adversarial_flow is not None
            and adversarial_flow.score > 0.0
        )
    raise ValueError(f"unknown signal family: {family}")


def ablate_signal_family(sample: EnsembleSample, family: str) -> EnsembleSample:
    if family == "smart_money":
        return replace(sample, smart_money_consensus=None)
    if family == "holder_concentration":
        return replace(sample, holder_concentration=None)
    if family == "sibling_prior":
        return replace(sample, sibling_implied_prior=None)
    if family == "resolution_risk":
        return replace(sample, resolution_risk_score=None)
    if family == "adversarial_flow":
        return replace(sample, adversarial_flow_score=None)
    raise ValueError(f"unknown signal family: {family}")


def _apply_adversarial_guard(
    probability: float,
    *,
    baseline_prob: float | None,
    adversarial_flow: AdversarialFlowContext | None,
    blend_floor: float,
) -> float:
    if baseline_prob is None or adversarial_flow is None or adversarial_flow.score <= 0.0:
        return probability
    attenuation = max(blend_floor, 1.0 - adversarial_flow.score)
    return baseline_prob + (probability - baseline_prob) * attenuation


def bootstrap_mean_ci(
    values: list[float],
    *,
    iterations: int,
    ci_level: float,
    seed: int = 0,
) -> tuple[float | None, float | None]:
    if not values or iterations <= 0:
        return None, None
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = np.empty(iterations, dtype=float)
    for idx in range(iterations):
        sample = rng.choice(arr, size=len(arr), replace=True)
        means[idx] = float(np.mean(sample))
    alpha = max(0.0, min(1.0, 1.0 - ci_level)) / 2.0
    lo = float(np.quantile(means, alpha))
    hi = float(np.quantile(means, 1.0 - alpha))
    return lo, hi


def summarize_signal_family(
    family: str,
    deltas: list[_FamilySampleDelta],
    *,
    ci_level: float,
    bootstrap_iterations: int,
    min_samples: int,
    model_version: str,
    horizon_hours: int,
    resolved_lookback_days: int,
) -> SignalFamilyAblation:
    sample_count = len(deltas)
    signal_present_samples = sum(1 for item in deltas if item.signal_present)
    non_zero = sum(1 for item in deltas if item.probability_delta > 1e-9)
    if not deltas:
        return SignalFamilyAblation(
            signal_family=family,
            sample_count=0,
            signal_present_samples=0,
            non_zero_contribution_samples=0,
            mean_abs_probability_delta=None,
            brier_with_signal=None,
            brier_without_signal=None,
            incremental_brier_gain=None,
            bootstrap_ci_lo=None,
            bootstrap_ci_hi=None,
            ci_level=ci_level,
            bootstrap_iterations=bootstrap_iterations,
            archive_recommended=False,
            model_version=model_version,
            horizon_hours=horizon_hours,
            resolved_lookback_days=resolved_lookback_days,
        )
    mean_abs_probability_delta = sum(item.probability_delta for item in deltas) / sample_count
    brier_with_signal = sum(item.full_brier for item in deltas) / sample_count
    brier_without_signal = sum(item.ablated_brier for item in deltas) / sample_count
    incremental = brier_without_signal - brier_with_signal
    bootstrap_values = [item.ablated_brier - item.full_brier for item in deltas]
    ci_lo, ci_hi = bootstrap_mean_ci(
        bootstrap_values,
        iterations=bootstrap_iterations,
        ci_level=ci_level,
    )
    archive_recommended = (
        sample_count >= min_samples
        and ci_lo is not None
        and ci_hi is not None
        and ci_lo <= 0.0 <= ci_hi
    )
    return SignalFamilyAblation(
        signal_family=family,
        sample_count=sample_count,
        signal_present_samples=signal_present_samples,
        non_zero_contribution_samples=non_zero,
        mean_abs_probability_delta=mean_abs_probability_delta,
        brier_with_signal=brier_with_signal,
        brier_without_signal=brier_without_signal,
        incremental_brier_gain=incremental,
        bootstrap_ci_lo=ci_lo,
        bootstrap_ci_hi=ci_hi,
        ci_level=ci_level,
        bootstrap_iterations=bootstrap_iterations,
        archive_recommended=archive_recommended,
        model_version=model_version,
        horizon_hours=horizon_hours,
        resolved_lookback_days=resolved_lookback_days,
    )


async def signal_archive_status_batch_asof(
    ch: AsyncClient,
    *,
    signal_families: list[str],
    asked_at: datetime,
) -> dict[str, SignalArchiveStatus]:
    if not signal_families:
        return {}
    result = await ch.query(
        """
        SELECT signal_family, action, sample_count, incremental_brier_gain,
               bootstrap_ci_lo, bootstrap_ci_hi, ci_level, reason,
               event_time, observed_at
        FROM signal_archive_log
        WHERE signal_family IN {signal_families:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY signal_family, observed_at DESC
        LIMIT 1 BY signal_family
        """,
        parameters={"signal_families": list(signal_families), "asof": asked_at},
    )
    return {
        str(row[0]): _archive_status_from_row(row)
        for row in result.result_rows
    }


def build_signal_archive_transition_rows(
    reports: list[SignalFamilyAblation],
    *,
    current_status: dict[str, SignalArchiveStatus],
    observed_at: datetime,
) -> list[tuple[object, ...]]:
    def _format_ci(report: SignalFamilyAblation) -> str:
        if report.bootstrap_ci_lo is None or report.bootstrap_ci_hi is None:
            return "[n/a, n/a]"
        return f"[{report.bootstrap_ci_lo:.6f}, {report.bootstrap_ci_hi:.6f}]"

    rows: list[tuple[object, ...]] = []
    for report in reports:
        status = current_status.get(report.signal_family)
        currently_archived = status.is_archived if status is not None else False
        if report.archive_recommended and not currently_archived:
            rows.append(
                signal_archive_log_row(
                    signal_family=report.signal_family,
                    action="archived",
                    sample_count=report.sample_count,
                    incremental_brier_gain=report.incremental_brier_gain,
                    bootstrap_ci_lo=report.bootstrap_ci_lo,
                    bootstrap_ci_hi=report.bootstrap_ci_hi,
                    ci_level=report.ci_level,
                    reason=(
                        f"bootstrap {int(report.ci_level * 100)}% CI "
                        f"{_format_ci(report)} "
                        f"includes zero over {report.sample_count} samples"
                    ),
                    observed_at=observed_at,
                )
            )
        elif (
            not report.archive_recommended
            and currently_archived
            and report.bootstrap_ci_lo is not None
            and report.bootstrap_ci_hi is not None
        ):
            rows.append(
                signal_archive_log_row(
                    signal_family=report.signal_family,
                    action="re_enabled",
                    sample_count=report.sample_count,
                    incremental_brier_gain=report.incremental_brier_gain,
                    bootstrap_ci_lo=report.bootstrap_ci_lo,
                    bootstrap_ci_hi=report.bootstrap_ci_hi,
                    ci_level=report.ci_level,
                    reason=(
                        f"bootstrap {int(report.ci_level * 100)}% CI "
                        f"{_format_ci(report)} "
                        f"excludes zero over {report.sample_count} samples"
                    ),
                    observed_at=observed_at,
                )
            )
    return rows


async def _market_snapshot_row(
    ch: AsyncClient,
    *,
    condition_id: str,
    asked_at: datetime,
) -> dict[str, object] | None:
    result = await ch.query(
        """
        SELECT condition_id, event_id, question, slug, category, tags, volume_usdc,
               liquidity_usdc, end_date, token_ids
        FROM markets_snapshots
        WHERE condition_id = {cond:String}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        parameters={"cond": condition_id, "asof": asked_at},
    )
    rows = result.result_rows
    if not rows:
        return None
    row = rows[0]
    return {
        "condition_id": str(row[0]),
        "event_id": str(row[1] or ""),
        "question": str(row[2]),
        "slug": str(row[3] or ""),
        "category": str(row[4]) if row[4] else None,
        "tags": list(row[5]) if row[5] else [],
        "volume_usdc": float(row[6]),
        "liquidity_usdc": float(row[7]),
        "end_date": row[8],
        "token_ids": list(row[9]) if row[9] else [],
    }


async def _signal_family_deltas_for_resolution(
    ch: AsyncClient,
    *,
    condition_id: str,
    asked_at: datetime,
    registry: EnsembleRegistry,
    deribit: DeribitClient,
    fedwatch: CMEFedWatchClient | None,
) -> dict[str, _FamilySampleDelta] | None:
    settings = get_settings()
    snap = await _market_snapshot_row(ch, condition_id=condition_id, asked_at=asked_at)
    if snap is None:
        return None

    token_ids = list(snap.get("token_ids") or [])
    mids = await _latest_mids_for_tokens(ch, token_ids, asked_at)
    yes_mid = mids.get(str(token_ids[0])) if token_ids else None

    classification = await classification_asof(ch, condition_id, asked_at)
    if classification is None:
        return None
    feature_snapshot = await feature_snapshot_asof(ch, condition_id, asked_at)
    smart_money_row = await smart_money_asof(ch, condition_id, asked_at)
    concentration_row = await concentration_asof(ch, condition_id, asked_at)
    resolution_risk_row = await resolution_risk_asof(ch, condition_id, asked_at)
    adversarial_flow_row = await adversarial_flow_asof(
        ch,
        condition_id=condition_id,
        token_id=str(token_ids[0]) if token_ids else None,
        feature_snapshot=feature_snapshot,
        asked_at=asked_at,
        settings=settings,
    )
    regime_row = await regime_label_asof(ch, asked_at)
    disable_status = await model_disable_status_asof(
        ch,
        market_type=classification.market_type.value,
        asked_at=asked_at,
    )

    active_snaps = await _latest_markets(ch, asked_at, limit=500)
    active_condition_ids = [str(row["condition_id"]) for row in active_snaps]
    active_classifications = await classifications_batch_asof(
        ch,
        active_condition_ids,
        asked_at,
    )
    active_tokens: list[str] = []
    for row in active_snaps:
        active_tokens.extend(list(row.get("token_ids") or []))  # type: ignore[arg-type]
    active_mids = await _latest_mids_for_tokens(ch, active_tokens, asked_at)
    sibling_priors = _sibling_priors_by_condition(
        _sibling_market_rows(active_snaps, active_classifications, active_mids)
    )
    multi_outcome_contexts = await _multi_outcome_contexts_for_markets(ch, [snap], asked_at)
    long_tail_candidates = await load_long_tail_resolved_markets(ch, asked_at=asked_at)
    discrete = _discrete_config_from_settings(settings)

    long_tail = None
    if classification.market_type is MarketType.LONG_TAIL_BINARY:
        long_tail = long_tail_prior_context(
            current_condition_id=condition_id,
            question=str(snap["question"]),
            slug=str(snap["slug"]),
            tags=list(snap.get("tags") or []),  # type: ignore[arg-type]
            classification=ClassificationResult(
                market_type=classification.market_type,
                confidence=classification.confidence,
                features=classification.features,
                reasons=classification.reasons,
            ),
            candidates=long_tail_candidates,
        )

    pipeline_result, classification = await _pipeline_for_row(
        snap=snap,
        mid=yes_mid,
        classification=classification,
        asked_at=asked_at,
        deribit=deribit,
        fedwatch=fedwatch,
        discrete=discrete,
        context=BaselineContext(
            multi_outcome=multi_outcome_contexts.get(condition_id),
            long_tail=long_tail,
            long_tail_candidates=long_tail_candidates,
        ),
    )
    sample = _ensemble_sample_for_row(
        classification=classification,
        pipeline_result=pipeline_result,
        market_mid=yes_mid,
        sibling_prior=sibling_priors.get(condition_id),
        feature_snapshot=feature_snapshot,
        smart_money=smart_money_row,
        concentration=concentration_row,
        resolution_risk=resolution_risk_row,
        adversarial_flow=adversarial_flow_row,
        regime_label=regime_row.label if regime_row is not None else None,
        concentration_threshold=settings.holder_concentration_down_weight_threshold,
        asked_at=asked_at,
    )
    if sample is None:
        return None
    model = registry.model_for_type(sample.market_type)
    if model is None:
        return None

    baseline_prob = pipeline_result.displayed_probability
    if disable_status is not None and disable_status.is_disabled:
        if baseline_prob is None:
            return None
        return {
            family: _FamilySampleDelta(
                full_brier=baseline_prob,
                ablated_brier=baseline_prob,
                probability_delta=0.0,
                signal_present=_signal_present(
                    family,
                    sample,
                    adversarial_flow=adversarial_flow_row,
                ),
            )
            for family in SIGNAL_FAMILIES
        }

    full_prob = model.predict_probability(sample)
    full_prob = _apply_adversarial_guard(
        full_prob,
        baseline_prob=baseline_prob,
        adversarial_flow=adversarial_flow_row,
        blend_floor=settings.adversarial_flow_blend_floor,
    )
    out: dict[str, _FamilySampleDelta] = {}
    for family in SIGNAL_FAMILIES:
        ablated_sample = ablate_signal_family(sample, family)
        ablated_prob = model.predict_probability(ablated_sample)
        if family != "adversarial_flow":
            ablated_prob = _apply_adversarial_guard(
                ablated_prob,
                baseline_prob=baseline_prob,
                adversarial_flow=adversarial_flow_row,
                blend_floor=settings.adversarial_flow_blend_floor,
            )
        out[family] = _FamilySampleDelta(
            full_brier=full_prob,
            ablated_brier=ablated_prob,
            probability_delta=abs(full_prob - ablated_prob),
            signal_present=_signal_present(
                family,
                sample,
                adversarial_flow=adversarial_flow_row,
            ),
        )
    return out


async def signal_family_ablation_reports(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    horizon_hours: int,
    resolved_lookback_days: int,
    resolved_limit: int,
    bootstrap_iterations: int,
    ci_level: float,
    min_samples: int,
) -> list[SignalFamilyAblation]:
    settings = get_settings()
    registry = load_ensemble_registry(settings.ensemble_registry_file)
    if registry is None:
        return []

    corpus_rows, _ = await load_resolved_market_corpus(
        ch,
        resolved_from=asked_at - timedelta(days=resolved_lookback_days),
        resolved_to=asked_at,
        limit=resolved_limit,
    )
    by_family: dict[str, list[_FamilySampleDelta]] = defaultdict(list)
    model_version = "per_type_ensemble_v1"

    async with DeribitClient(base_url=settings.deribit_base) as deribit:
        fedwatch = None
        if settings.cme_api_id and settings.cme_api_secret:
            fedwatch = CMEFedWatchClient(
                base_url=settings.cme_fedwatch_base,
                token_url=settings.cme_oauth_token_url,
                api_id=settings.cme_api_id,
                api_secret=settings.cme_api_secret,
                application_name=settings.cme_application_name,
                application_vendor=settings.cme_application_vendor,
                application_version=settings.cme_application_version,
                user_agent=f"{settings.cme_application_name}/{settings.cme_application_version}",
            )
            await fedwatch.__aenter__()
        try:
            for corpus_row in corpus_rows:
                if corpus_row.resolved_outcome not in {"YES", "NO"}:
                    continue
                sample_asked_at = corpus_row.resolved_at - timedelta(hours=horizon_hours)
                family_probs = await _signal_family_deltas_for_resolution(
                    ch,
                    condition_id=corpus_row.condition_id,
                    asked_at=sample_asked_at,
                    registry=registry,
                    deribit=deribit,
                    fedwatch=fedwatch,
                )
                if family_probs is None:
                    continue
                outcome = 1.0 if corpus_row.resolved_outcome == "YES" else 0.0
                for family, family_delta in family_probs.items():
                    by_family[family].append(
                        _FamilySampleDelta(
                            full_brier=(family_delta.full_brier - outcome) ** 2,
                            ablated_brier=(family_delta.ablated_brier - outcome) ** 2,
                            probability_delta=family_delta.probability_delta,
                            signal_present=family_delta.signal_present,
                        )
                    )
        finally:
            if fedwatch is not None:
                await fedwatch.__aexit__(None, None, None)

    return [
        summarize_signal_family(
            family,
            by_family.get(family, []),
            ci_level=ci_level,
            bootstrap_iterations=bootstrap_iterations,
            min_samples=min_samples,
            model_version=model_version,
            horizon_hours=horizon_hours,
            resolved_lookback_days=resolved_lookback_days,
        )
        for family in SIGNAL_FAMILIES
    ]


async def run_signal_ablation(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
) -> SignalAblationReport:
    settings = get_settings()
    observed_at = asked_at or datetime.now(UTC)
    reports = await signal_family_ablation_reports(
        ch,
        asked_at=observed_at,
        horizon_hours=settings.signal_ablation_horizon_hours,
        resolved_lookback_days=settings.signal_ablation_resolved_lookback_days,
        resolved_limit=settings.signal_ablation_limit,
        bootstrap_iterations=settings.signal_ablation_bootstrap_iterations,
        ci_level=settings.signal_ablation_ci_level,
        min_samples=settings.signal_ablation_min_samples,
    )
    metric_rows = [
        signal_ablation_metric_row(
            signal_family=report.signal_family,
            sample_count=report.sample_count,
            signal_present_samples=report.signal_present_samples,
            non_zero_contribution_samples=report.non_zero_contribution_samples,
            mean_abs_probability_delta=report.mean_abs_probability_delta,
            brier_with_signal=report.brier_with_signal,
            brier_without_signal=report.brier_without_signal,
            incremental_brier_gain=report.incremental_brier_gain,
            bootstrap_ci_lo=report.bootstrap_ci_lo,
            bootstrap_ci_hi=report.bootstrap_ci_hi,
            ci_level=report.ci_level,
            bootstrap_iterations=report.bootstrap_iterations,
            archive_recommended=report.archive_recommended,
            model_version=report.model_version,
            horizon_hours=report.horizon_hours,
            resolved_lookback_days=report.resolved_lookback_days,
            observed_at=observed_at,
        )
        for report in reports
    ]
    if metric_rows:
        await ch.insert(
            "signal_ablation_metrics",
            metric_rows,
            column_names=SIGNAL_ABLATION_METRICS_COLS,
        )

    current_status = await signal_archive_status_batch_asof(
        ch,
        signal_families=[report.signal_family for report in reports],
        asked_at=observed_at,
    )
    archive_rows = build_signal_archive_transition_rows(
        reports,
        current_status=current_status,
        observed_at=observed_at,
    )
    if archive_rows:
        await ch.insert(
            "signal_archive_log",
            archive_rows,
            column_names=SIGNAL_ARCHIVE_LOG_COLS,
        )

    archived_families = [str(row[0]) for row in archive_rows if row[1] == "archived"]
    re_enabled_families = [str(row[0]) for row in archive_rows if row[1] == "re_enabled"]
    return SignalAblationReport(
        asked_at=observed_at,
        horizon_hours=settings.signal_ablation_horizon_hours,
        resolved_lookback_days=settings.signal_ablation_resolved_lookback_days,
        resolved_limit=settings.signal_ablation_limit,
        bootstrap_iterations=settings.signal_ablation_bootstrap_iterations,
        ci_level=settings.signal_ablation_ci_level,
        metric_rows_written=len(metric_rows),
        archive_rows_written=len(archive_rows),
        archived_families=archived_families,
        re_enabled_families=re_enabled_families,
        by_family=reports,
    )


async def _main() -> None:
    async with get_async_client() as ch:
        report = await run_signal_ablation(ch)
    print(
        json.dumps(
            asdict(report),
            default=lambda value: value.isoformat() if isinstance(value, datetime) else value,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(_main())
