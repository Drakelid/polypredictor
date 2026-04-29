"""Executable M3 exit-criteria audit helpers.

This module turns the remaining M3 checks into measurable evidence:

* smart-money ablation on resolved-market replay
* arb replay recovery against historically emitted arb signals
* actionable signal-feed density over recent days

Like the M1/M2 audits, this produces evidence but does not self-certify the
exit criteria as passed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient
from ingest.settings import get_settings as get_ingest_settings
from ingest.workers import arb_checker as arb_worker
from model import (
    DeribitClient,
    MarketType,
    build_multi_outcome_groups,
    build_threshold_pairs,
    detect_multi_outcome_arbs,
    detect_threshold_arbs,
)

from .classifications import classification_asof, classifications_batch_asof
from .clickhouse import get_async_client
from .cme_fedwatch import CMEFedWatchClient
from .concentration import concentration_asof
from .ensemble_registry import load_ensemble_registry
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
from .settings import get_settings
from .smart_money import smart_money_asof


@dataclass(frozen=True)
class SmartMoneyAblationAudit:
    total_resolved_markets: int
    evaluated_samples: int
    samples_with_smart_money: int
    non_zero_contribution_samples: int
    mean_abs_probability_delta: float | None
    brier_with_smart_money: float | None
    brier_without_smart_money: float | None
    incremental_brier_gain: float | None


@dataclass(frozen=True)
class ArbReplayAudit:
    known_event_count: int
    replayed_events: int
    recovered_events: int
    recovery_rate: float | None


@dataclass(frozen=True)
class DailySignalVolume:
    day: str
    events: int


@dataclass(frozen=True)
class SignalFeedDensityAudit:
    lookback_days: int
    min_severity: float
    total_events: int
    average_events_per_day: float | None
    days_with_10_plus_events: int
    meets_threshold_each_day: bool
    by_day: list[DailySignalVolume]


@dataclass(frozen=True)
class M3AuditReport:
    as_of: datetime
    resolved_limit: int
    arb_event_limit: int
    signal_lookback_days: int
    smart_money_ablation: SmartMoneyAblationAudit
    arb_replay: ArbReplayAudit
    signal_feed_density: SignalFeedDensityAudit


async def latest_binary_resolutions(
    ch: AsyncClient,
    *,
    resolved_from: datetime | None = None,
    resolved_to: datetime | None = None,
    limit: int = 500,
) -> list[tuple[str, str, datetime]]:
    conditions: list[str] = ["resolved_outcome IN ('YES', 'NO')"]
    parameters: dict[str, object] = {"limit": limit}
    if resolved_from is not None:
        conditions.append("event_time >= {resolved_from:DateTime64(3)}")
        parameters["resolved_from"] = resolved_from
    if resolved_to is not None:
        conditions.append("event_time <= {resolved_to:DateTime64(3)}")
        parameters["resolved_to"] = resolved_to
    result = await ch.query(
        f"""
        SELECT condition_id, resolved_outcome, event_time
        FROM (
            SELECT condition_id, resolved_outcome, event_time
            FROM market_resolutions
            WHERE {' AND '.join(conditions)}
            ORDER BY condition_id, observed_at ASC
            LIMIT 1 BY condition_id
        )
        ORDER BY event_time DESC
        LIMIT {{limit:UInt32}}
        """,
        parameters=parameters,
    )
    return [(str(row[0]), str(row[1]), row[2]) for row in result.result_rows]


async def _market_snapshot_row(
    ch: AsyncClient,
    *,
    condition_id: str,
    asked_at: datetime,
) -> dict[str, object] | None:
    query = """
        SELECT condition_id, event_id, question, slug, category, tags, volume_usdc,
               liquidity_usdc, end_date, token_ids
        FROM markets_snapshots
        WHERE condition_id = {cond:String}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(query, parameters={"cond": condition_id, "asof": asked_at})
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


async def _smart_money_probabilities_for_resolution(
    ch: AsyncClient,
    *,
    condition_id: str,
    asked_at: datetime,
    deribit: DeribitClient,
    fedwatch: CMEFedWatchClient | None,
) -> tuple[float, float] | None:
    settings = get_settings()
    registry = load_ensemble_registry(settings.ensemble_registry_file)
    if registry is None:
        return None

    snap = await _market_snapshot_row(ch, condition_id=condition_id, asked_at=asked_at)
    if snap is None:
        return None
    token_ids = list(snap.get("token_ids") or [])
    mids = await _latest_mids_for_tokens(ch, token_ids, asked_at)
    yes_mid = mids.get(str(token_ids[0])) if token_ids else None

    classification = await classification_asof(ch, condition_id, asked_at)
    feature_snapshot = await feature_snapshot_asof(ch, condition_id, asked_at)
    smart_money_row = await smart_money_asof(ch, condition_id, asked_at)
    concentration_row = await concentration_asof(ch, condition_id, asked_at)
    if feature_snapshot is None or smart_money_row is None:
        return None

    active_snaps = await _latest_markets(ch, asked_at, limit=500)
    active_condition_ids = [str(row["condition_id"]) for row in active_snaps]
    active_classifications = await classifications_batch_asof(ch, active_condition_ids, asked_at)
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
    if classification is not None and classification.market_type is MarketType.LONG_TAIL_BINARY:
        from model import ClassificationResult

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
        concentration_threshold=settings.holder_concentration_down_weight_threshold,
        asked_at=asked_at,
    )
    if sample is None or sample.smart_money_consensus is None:
        return None
    model = registry.model_for_type(sample.market_type)
    if model is None:
        return None
    full = model.predict_probability(sample)
    ablated = model.predict_probability(replace(sample, smart_money_consensus=None))
    return full, ablated


async def smart_money_ablation_audit(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    resolved_limit: int = 250,
    resolved_lookback_days: int = 90,
    horizon_hours: int = 24,
) -> SmartMoneyAblationAudit:
    resolutions = await latest_binary_resolutions(
        ch,
        resolved_from=asked_at - timedelta(days=resolved_lookback_days),
        resolved_to=asked_at,
        limit=resolved_limit,
    )
    settings = get_settings()
    brier_full = 0.0
    brier_ablated = 0.0
    deltas: list[float] = []
    evaluated = 0
    samples_with_signal = 0
    non_zero = 0

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
        if fedwatch is not None:
            await fedwatch.__aenter__()
        try:
            for condition_id, resolved_outcome, resolved_at in resolutions:
                sample_asked_at = resolved_at - timedelta(hours=horizon_hours)
                probs = await _smart_money_probabilities_for_resolution(
                    ch,
                    condition_id=condition_id,
                    asked_at=sample_asked_at,
                    deribit=deribit,
                    fedwatch=fedwatch,
                )
                if probs is None:
                    continue
                full, ablated = probs
                realized = 1.0 if resolved_outcome == "YES" else 0.0
                evaluated += 1
                samples_with_signal += 1
                brier_full += (full - realized) ** 2
                brier_ablated += (ablated - realized) ** 2
                delta = abs(full - ablated)
                deltas.append(delta)
                if delta > 1e-9:
                    non_zero += 1
        finally:
            if fedwatch is not None:
                await fedwatch.__aexit__(None, None, None)

    return SmartMoneyAblationAudit(
        total_resolved_markets=len(resolutions),
        evaluated_samples=evaluated,
        samples_with_smart_money=samples_with_signal,
        non_zero_contribution_samples=non_zero,
        mean_abs_probability_delta=(sum(deltas) / len(deltas)) if deltas else None,
        brier_with_smart_money=(brier_full / evaluated) if evaluated else None,
        brier_without_smart_money=(brier_ablated / evaluated) if evaluated else None,
        incremental_brier_gain=((brier_ablated - brier_full) / evaluated) if evaluated else None,
    )


async def _recent_arb_events(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_days: int,
    limit: int,
) -> list[tuple[str, str, datetime]]:
    result = await ch.query(
        """
        SELECT event_id, condition_id, observed_at
        FROM signal_events
        WHERE event_type = 'arb'
          AND observed_at >= {start:DateTime64(3)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY observed_at DESC
        LIMIT {limit:UInt32}
        """,
        parameters={
            "start": asked_at - timedelta(days=lookback_days),
            "asof": asked_at,
            "limit": limit,
        },
    )
    return [(str(row[0]), str(row[1]), row[2]) for row in result.result_rows]


async def _replay_arb_event(
    ch: AsyncClient,
    *,
    event_id: str,
    condition_id: str,
    observed_at: datetime,
    limit: int = 500,
) -> bool:
    ingest_settings = get_ingest_settings()
    snapshots = await arb_worker._latest_active_markets(ch, asked_at=observed_at, limit=limit)
    condition_ids = [str(snapshot["condition_id"]) for snapshot in snapshots]
    classifications = await arb_worker._latest_classifications(
        ch,
        condition_ids,
        asked_at=observed_at,
    )
    yes_tokens = [
        str(token_ids[0])
        for snapshot in snapshots
        if (token_ids := list(snapshot.get("token_ids") or []))
    ]
    quotes = await arb_worker._latest_yes_quotes(ch, yes_tokens, asked_at=observed_at)
    sibling_markets = arb_worker._build_sibling_markets(snapshots, classifications, quotes)
    violations = [
        *detect_threshold_arbs(
            build_threshold_pairs(sibling_markets),
            event_time=observed_at,
            taker_fee_bps=ingest_settings.arb_taker_fee_bps,
        ),
        *detect_multi_outcome_arbs(
            build_multi_outcome_groups(sibling_markets),
            event_time=observed_at,
            taker_fee_bps=ingest_settings.arb_taker_fee_bps,
        ),
    ]
    return any(
        violation.event_id == event_id or violation.condition_id == condition_id
        for violation in violations
    )


async def arb_replay_audit(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    event_limit: int = 25,
    lookback_days: int = 30,
) -> ArbReplayAudit:
    events = await _recent_arb_events(
        ch,
        asked_at=asked_at,
        lookback_days=lookback_days,
        limit=event_limit,
    )
    replayed = 0
    recovered = 0
    for event_id, condition_id, observed_at in events:
        replayed += 1
        if await _replay_arb_event(
            ch,
            event_id=event_id,
            condition_id=condition_id,
            observed_at=observed_at,
        ):
            recovered += 1
    return ArbReplayAudit(
        known_event_count=len(events),
        replayed_events=replayed,
        recovered_events=recovered,
        recovery_rate=(recovered / replayed) if replayed else None,
    )


async def signal_feed_density_audit(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_days: int = 7,
    min_severity: float = 1.0,
) -> SignalFeedDensityAudit:
    result = await ch.query(
        """
        SELECT toDate(observed_at) AS day, count()
        FROM signal_events
        WHERE observed_at >= {start:DateTime64(3)}
          AND observed_at <= {asof:DateTime64(3)}
          AND severity >= {min_severity:Float64}
        GROUP BY day
        ORDER BY day ASC
        """,
        parameters={
            "start": asked_at - timedelta(days=lookback_days),
            "asof": asked_at,
            "min_severity": min_severity,
        },
    )
    counts = {str(day): int(count) for day, count in result.result_rows}
    by_day: list[DailySignalVolume] = []
    total = 0
    days_ge_10 = 0
    for offset in range(lookback_days):
        day = (asked_at.date() - timedelta(days=lookback_days - 1 - offset)).isoformat()
        count = counts.get(day, 0)
        total += count
        if count >= 10:
            days_ge_10 += 1
        by_day.append(DailySignalVolume(day=day, events=count))
    return SignalFeedDensityAudit(
        lookback_days=lookback_days,
        min_severity=min_severity,
        total_events=total,
        average_events_per_day=(total / lookback_days) if lookback_days else None,
        days_with_10_plus_events=days_ge_10,
        meets_threshold_each_day=(days_ge_10 == lookback_days) if lookback_days else False,
        by_day=by_day,
    )


async def run_m3_audit(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
    resolved_limit: int = 250,
    arb_event_limit: int = 25,
    signal_lookback_days: int = 7,
) -> M3AuditReport:
    as_of = asked_at or datetime.now(tz=UTC)
    smart_money = await smart_money_ablation_audit(
        ch,
        asked_at=as_of,
        resolved_limit=resolved_limit,
    )
    arb = await arb_replay_audit(
        ch,
        asked_at=as_of,
        event_limit=arb_event_limit,
    )
    signal_density = await signal_feed_density_audit(
        ch,
        asked_at=as_of,
        lookback_days=signal_lookback_days,
    )
    return M3AuditReport(
        as_of=as_of,
        resolved_limit=resolved_limit,
        arb_event_limit=arb_event_limit,
        signal_lookback_days=signal_lookback_days,
        smart_money_ablation=smart_money,
        arb_replay=arb,
        signal_feed_density=signal_density,
    )


def _report_to_json(report: M3AuditReport) -> str:
    payload = asdict(report)
    smart_money = payload.get("smart_money_ablation")
    if isinstance(smart_money, dict):
        for key in (
            "mean_abs_probability_delta",
            "brier_with_smart_money",
            "brier_without_smart_money",
            "incremental_brier_gain",
        ):
            value = smart_money.get(key)
            if isinstance(value, float):
                smart_money[key] = round(value, 6)
    arb = payload.get("arb_replay")
    if isinstance(arb, dict):
        value = arb.get("recovery_rate")
        if isinstance(value, float):
            arb["recovery_rate"] = round(value, 6)
    signal_density = payload.get("signal_feed_density")
    if isinstance(signal_density, dict):
        value = signal_density.get("average_events_per_day")
        if isinstance(value, float):
            signal_density["average_events_per_day"] = round(value, 6)
    return json.dumps(payload, indent=2, default=str)


async def _main() -> None:
    ch = await get_async_client()
    try:
        report = await run_m3_audit(ch)
        print(_report_to_json(report))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
