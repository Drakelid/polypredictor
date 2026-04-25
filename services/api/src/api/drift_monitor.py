"""Nightly drift-monitoring driver (M6.3 / PRD §6.9, §8).

This is the missing I/O layer over the pure scoring helpers in
``packages/model/src/model/backtest.py``:

1. Replay PIT-served predictions on a canonical pre-resolution horizon.
2. Compute rolling 7d / 30d / 90d Brier, ECE, and conformal coverage.
3. Persist append-only snapshots into ``model_drift_metrics``.
4. Evaluate the 30d per-type skill streak and append disable / re-enable
   transitions into ``model_disable_log`` when the state flips.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient
from ingest.writers import (
    FEATURE_DRIFT_METRICS_COLS,
    MODEL_DISABLE_LOG_COLS,
    MODEL_DRIFT_METRICS_COLS,
    feature_drift_metric_row,
    model_disable_log_row,
    model_drift_metric_row,
)
from model import (
    DailySkillSample,
    MarketType,
    StratumReport,
    WalkForwardSample,
    brier_skill,
    kl_divergence,
    population_stability_index,
    rolling_window_reports,
    should_auto_disable,
)

from .backtest_walk_forward import load_resolved_market_corpus
from .clickhouse import get_async_client
from .markets import model_for_market
from .model_status import model_disable_status_batch_asof
from .settings import get_settings


@dataclass(frozen=True)
class DriftReplaySample:
    condition_id: str
    model_sample: WalkForwardSample
    baseline_prob: float | None

    def baseline_sample(self) -> WalkForwardSample | None:
        if self.baseline_prob is None:
            return None
        return WalkForwardSample(
            market_type=self.model_sample.market_type,
            asked_at=self.model_sample.asked_at,
            resolved_at=self.model_sample.resolved_at,
            predicted_prob=float(self.baseline_prob),
            outcome=self.model_sample.outcome,
            time_to_resolution_s=self.model_sample.time_to_resolution_s,
            band_lo=self.model_sample.band_lo,
            band_hi=self.model_sample.band_hi,
            regime=self.model_sample.regime,
        )


@dataclass(frozen=True)
class DriftMonitorReport:
    asked_at: datetime
    horizon_hours: int
    replayed_samples: int
    metric_rows_written: int
    feature_metric_rows_written: int
    disable_rows_written: int
    feature_alert_count: int
    alerted_features: list[str]
    disabled_market_types: list[str]
    re_enabled_market_types: list[str]


FEATURE_DRIFT_FIELDS = (
    "spread",
    "book_imbalance_1pct",
    "book_imbalance_5pct",
    "momentum_1h",
    "momentum_24h",
    "momentum_7d",
    "realized_vol_24h",
    "informed_taker_flow_24h",
    "passive_maker_flow_24h",
    "decayed_directional_flow_24h",
)


def _market_type_from_detail(raw: str) -> MarketType:
    try:
        return MarketType(raw)
    except ValueError:
        return MarketType.MISC


def _flatten_report(report: object) -> list[StratumReport]:
    walk_report = report
    return [
        walk_report.overall,
        *walk_report.by_type,
        *walk_report.by_regime,
        *walk_report.strata,
    ]


def _report_key(stratum: StratumReport) -> tuple[str, str, str]:
    return (stratum.market_type, stratum.ttr_bucket, stratum.regime or "")


def _report_map(report: object) -> dict[tuple[str, str, str], StratumReport]:
    return {_report_key(stratum): stratum for stratum in _flatten_report(report)}


def _feature_values_from_rows(
    rows: list[tuple[object, ...]],
) -> dict[str, list[float]]:
    out = {feature_name: [] for feature_name in FEATURE_DRIFT_FIELDS}
    for row in rows:
        for feature_name, value in zip(FEATURE_DRIFT_FIELDS, row, strict=True):
            if value is None:
                continue
            out[feature_name].append(float(value))
    return out


async def load_drift_replay_samples(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    horizon_hours: int,
    resolved_lookback_days: int,
    limit: int,
) -> list[DriftReplaySample]:
    corpus_rows, _ = await load_resolved_market_corpus(
        ch,
        resolved_from=asked_at - timedelta(days=resolved_lookback_days),
        resolved_to=asked_at,
        limit=limit,
    )
    horizon = timedelta(hours=horizon_hours)
    samples: list[DriftReplaySample] = []
    for corpus_row in corpus_rows:
        if corpus_row.resolved_outcome not in {"YES", "NO"}:
            continue
        sample_asked_at = corpus_row.resolved_at - horizon
        if sample_asked_at > asked_at:
            continue
        detail = await model_for_market(
            ch,
            condition_id=corpus_row.condition_id,
            asked_at=sample_asked_at,
        )
        if detail is None or detail.model_prob is None:
            continue
        samples.append(
            DriftReplaySample(
                condition_id=corpus_row.condition_id,
                model_sample=WalkForwardSample(
                    market_type=_market_type_from_detail(detail.market_type),
                    asked_at=sample_asked_at.timestamp(),
                    resolved_at=corpus_row.resolved_at.timestamp(),
                    predicted_prob=float(detail.model_prob),
                    outcome=1 if corpus_row.resolved_outcome == "YES" else 0,
                    time_to_resolution_s=(corpus_row.resolved_at - sample_asked_at).total_seconds(),
                    band_lo=detail.band_lo,
                    band_hi=detail.band_hi,
                    regime=detail.regime_label,
                ),
                baseline_prob=detail.mid,
            )
        )
    return samples


async def load_feature_window_values(
    ch: AsyncClient,
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, list[float]]:
    if window_start >= window_end:
        return {feature_name: [] for feature_name in FEATURE_DRIFT_FIELDS}
    select_cols = ", ".join(FEATURE_DRIFT_FIELDS)
    result = await ch.query(
        f"""
        SELECT {select_cols}
        FROM market_features
        WHERE observed_at >= {{window_start:DateTime64(3)}}
          AND observed_at < {{window_end:DateTime64(3)}}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
        """,
        parameters={
            "window_start": window_start,
            "window_end": window_end,
        },
    )
    return _feature_values_from_rows(result.result_rows)


def build_drift_metric_rows(
    samples: list[DriftReplaySample],
    *,
    observed_at: datetime,
) -> list[tuple[Any, ...]]:
    asked_at_ts = observed_at.timestamp()
    model_reports = rolling_window_reports(
        [sample.model_sample for sample in samples],
        asked_at=asked_at_ts,
    )
    baseline_reports = rolling_window_reports(
        [
            baseline_sample
            for sample in samples
            if (baseline_sample := sample.baseline_sample()) is not None
        ],
        asked_at=asked_at_ts,
    )
    baseline_by_window = {
        report.window_label: _report_map(report.report)
        for report in baseline_reports
    }
    rows: list[tuple[Any, ...]] = []
    for report in model_reports:
        baseline_map = baseline_by_window.get(report.window_label, {})
        for stratum in _flatten_report(report.report):
            baseline_stratum = baseline_map.get(_report_key(stratum))
            baseline_brier = None
            if (
                baseline_stratum is not None
                and baseline_stratum.sample_count == stratum.sample_count
            ):
                baseline_brier = baseline_stratum.brier
            rows.append(
                model_drift_metric_row(
                    window_label=report.window_label,
                    market_type=stratum.market_type,
                    ttr_bucket=stratum.ttr_bucket,
                    regime=stratum.regime,
                    sample_count=stratum.sample_count,
                    brier=stratum.brier,
                    ece=stratum.ece,
                    coverage=stratum.coverage,
                    avg_predicted=stratum.avg_predicted,
                    avg_outcome=stratum.avg_outcome,
                    baseline_brier=baseline_brier,
                    brier_skill=(
                        brier_skill(stratum.brier, baseline_brier)
                        if baseline_brier is not None
                        else None
                    ),
                    observed_at=observed_at,
                )
            )
    return rows


def build_feature_drift_rows(
    *,
    reference_values: dict[str, list[float]],
    current_values: dict[str, list[float]],
    observed_at: datetime,
    psi_threshold: float,
    min_samples: int,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for feature_name in FEATURE_DRIFT_FIELDS:
        reference = list(reference_values.get(feature_name, []))
        current = list(current_values.get(feature_name, []))
        psi = population_stability_index(reference, current)
        kl = kl_divergence(reference, current)
        sufficient = min(len(reference), len(current)) >= min_samples
        rows.append(
            feature_drift_metric_row(
                feature_name=feature_name,
                reference_count=len(reference),
                current_count=len(current),
                psi=psi,
                kl_divergence=kl,
                psi_threshold=psi_threshold,
                is_alert=sufficient and psi > psi_threshold,
                observed_at=observed_at,
            )
        )
    return rows


async def load_daily_skill_history(
    ch: AsyncClient,
    *,
    market_type: str,
    asked_at: datetime,
    lookback_days: int,
) -> list[DailySkillSample]:
    result = await ch.query(
        """
        SELECT observed_at, brier, baseline_brier
        FROM model_drift_metrics
        WHERE window_label = '30d'
          AND market_type = {market_type:String}
          AND ttr_bucket = 'overall'
          AND regime = ''
          AND observed_at <= {asof:DateTime64(3)}
          AND observed_at >= {floor:DateTime64(3)}
        ORDER BY observed_at ASC
        """,
        parameters={
            "market_type": market_type,
            "asof": asked_at,
            "floor": asked_at - timedelta(days=lookback_days),
        },
    )
    history: list[DailySkillSample] = []
    for observed_at, model_brier, baseline_brier in result.result_rows:
        if baseline_brier is None:
            continue
        history.append(
            DailySkillSample(
                asked_at=observed_at.timestamp(),
                model_brier=float(model_brier),
                baseline_brier=float(baseline_brier),
            )
        )
    return history


async def build_disable_transition_rows(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    market_types: list[str],
    disable_streak_days: int,
    history_lookback_days: int,
) -> list[tuple[Any, ...]]:
    current_status = await model_disable_status_batch_asof(
        ch,
        market_types=market_types,
        asked_at=asked_at,
    )
    rows: list[tuple[Any, ...]] = []
    for market_type in market_types:
        history = await load_daily_skill_history(
            ch,
            market_type=market_type,
            asked_at=asked_at,
            lookback_days=history_lookback_days,
        )
        decision = should_auto_disable(
            history,
            consecutive_days=disable_streak_days,
        )
        status = current_status.get(market_type)
        latest_skill = None
        if history:
            latest_skill = brier_skill(
                history[-1].model_brier,
                history[-1].baseline_brier,
            )
        if decision.should_disable and not (status and status.is_disabled):
            rows.append(
                model_disable_log_row(
                    market_type=market_type,
                    action="disabled",
                    consecutive_days=decision.consecutive_days,
                    threshold_days=decision.threshold_days,
                    reason=decision.reason,
                    observed_at=asked_at,
                )
            )
            continue
        # Re-enable only on actual recovery, not merely on "no data" or on a
        # neutral streak after the baseline fallback has already kicked in.
        if (
            status is not None
            and status.is_disabled
            and latest_skill is not None
            and latest_skill > 0.0
        ):
            rows.append(
                model_disable_log_row(
                    market_type=market_type,
                    action="re_enabled",
                    consecutive_days=0,
                    threshold_days=disable_streak_days,
                    reason=f"30d Brier skill recovered above baseline ({latest_skill:.4f})",
                    observed_at=asked_at,
                )
            )
    return rows


async def run_drift_monitor(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
) -> DriftMonitorReport:
    settings = get_settings()
    observed_at = asked_at or datetime.now(tz=UTC)
    replay_samples = await load_drift_replay_samples(
        ch,
        asked_at=observed_at,
        horizon_hours=settings.drift_monitor_horizon_hours,
        resolved_lookback_days=settings.drift_monitor_resolved_lookback_days,
        limit=settings.drift_monitor_limit,
    )
    current_window_start = observed_at - timedelta(days=settings.feature_drift_current_window_days)
    reference_window_start = current_window_start - timedelta(days=settings.feature_drift_reference_window_days)
    reference_values = await load_feature_window_values(
        ch,
        window_start=reference_window_start,
        window_end=current_window_start,
    )
    current_values = await load_feature_window_values(
        ch,
        window_start=current_window_start,
        window_end=observed_at,
    )
    metric_rows = build_drift_metric_rows(replay_samples, observed_at=observed_at)
    feature_drift_rows = build_feature_drift_rows(
        reference_values=reference_values,
        current_values=current_values,
        observed_at=observed_at,
        psi_threshold=settings.feature_drift_alert_threshold_psi,
        min_samples=settings.feature_drift_min_samples,
    )
    if metric_rows:
        await ch.insert(
            "model_drift_metrics",
            metric_rows,
            column_names=MODEL_DRIFT_METRICS_COLS,
        )
    if feature_drift_rows:
        await ch.insert(
            "feature_drift_metrics",
            feature_drift_rows,
            column_names=FEATURE_DRIFT_METRICS_COLS,
        )
    market_types = sorted(
        {
            str(row[1])
            for row in metric_rows
            if str(row[0]) == "30d"
            and str(row[1]) != "overall"
            and str(row[2]) == "overall"
            and str(row[3]) == ""
        }
    )
    disable_rows = await build_disable_transition_rows(
        ch,
        asked_at=observed_at,
        market_types=market_types,
        disable_streak_days=settings.drift_monitor_disable_streak_days,
        history_lookback_days=settings.drift_monitor_history_lookback_days,
    )
    if disable_rows:
        await ch.insert(
            "model_disable_log",
            disable_rows,
            column_names=MODEL_DISABLE_LOG_COLS,
        )
    return DriftMonitorReport(
        asked_at=observed_at,
        horizon_hours=settings.drift_monitor_horizon_hours,
        replayed_samples=len(replay_samples),
        metric_rows_written=len(metric_rows),
        feature_metric_rows_written=len(feature_drift_rows),
        disable_rows_written=len(disable_rows),
        feature_alert_count=sum(1 for row in feature_drift_rows if bool(row[6])),
        alerted_features=[
            str(row[0]) for row in feature_drift_rows if bool(row[6])
        ],
        disabled_market_types=[
            str(row[0]) for row in disable_rows if str(row[1]) == "disabled"
        ],
        re_enabled_market_types=[
            str(row[0]) for row in disable_rows if str(row[1]) == "re_enabled"
        ],
    )


def _round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_round_floats(item) for item in value]
    if isinstance(value, dict):
        return {key: _round_floats(item) for key, item in value.items()}
    return value


async def _main() -> None:
    ch = await get_async_client()
    try:
        report = await run_drift_monitor(ch)
        print(json.dumps(_round_floats(asdict(report)), indent=2, default=str))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
