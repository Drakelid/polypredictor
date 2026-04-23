"""Market list + model endpoints (M1).

The dashboard list joins three streams per market:
  1. Latest ``markets_snapshots`` row (catalog + volume/liquidity).
  2. Latest ``market_quotes`` row per token (market mid).
  3. Latest ``market_classifications`` row (type + features).

and dispatches through :func:`model.probability_for_market` to produce the
displayed probability + edge. When required baseline inputs (spot, IV) aren't
available yet — they arrive fully in M5 — we fall back to ``market_mid`` per
the pipeline's contract. The UI badge reflects which baseline produced the
number so users can judge trust.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import (
    BaselineInputs,
    ClassificationResult,
    DeribitClient,
    EnsembleRegistry,
    EnsembleSample,
    FeatureContribution,
    MarketType,
    PipelineResult,
    SplitConformalRegistry,
    classify,
    probability_for_market,
)

from . import asof as asof_q
from .classifications import (
    ClassificationRow,
    classification_asof,
    classifications_batch_asof,
)
from .cme_fedwatch import CMEFedWatchClient
from .conformal_registry import load_conformal_registry
from .discrete_inputs import resolve_discrete_record
from .ensemble_registry import load_ensemble_registry
from .features import FeatureSnapshotRow, feature_snapshot_asof, feature_snapshots_batch_asof
from .long_tail_priors import (
    LongTailPriorContext,
    LongTailResolvedMarket,
    load_long_tail_resolved_markets,
    long_tail_prior_context,
)
from .settings import Settings, get_settings


@dataclass(frozen=True)
class MarketListRow:
    condition_id: str
    question: str
    slug: str
    category: str | None
    tags: list[str]
    volume_usdc: float
    liquidity_usdc: float
    end_date: datetime | None
    token_ids: list[str]
    # Live quote on the YES leg — convention: first token is YES for binaries.
    mid: float | None
    # Model outputs.
    market_type: str
    model_prob: float | None
    model_source: str
    refinement_source: str | None
    baseline_source: str
    edge_bps: float | None
    needs_review: bool
    confidence: float
    time_to_resolution_s: float | None


@dataclass(frozen=True)
class DiscreteBaselineConfig:
    baselines_file: str | None = None
    fomc_fedwatch_prob: float | None = None
    cpi_consensus_point: float | None = None
    cpi_consensus_stddev: float | None = None
    nfp_consensus_point: float | None = None
    nfp_consensus_stddev: float | None = None


@dataclass(frozen=True)
class MultiOutcomeContext:
    raw: list[float]
    self_index: int


@dataclass(frozen=True)
class BaselineContext:
    multi_outcome: MultiOutcomeContext | None = None
    long_tail: LongTailPriorContext | None = None
    long_tail_candidates: list[LongTailResolvedMarket] | None = None


async def _latest_markets(
    ch: AsyncClient, asked_at: datetime, *, limit: int
) -> list[dict[str, object]]:
    """Latest snapshot per ``condition_id`` ordered by volume.

    ``LIMIT 1 BY condition_id`` keeps the one freshest row per market without
    a window-function or subquery — ClickHouse-native and cheap.
    """
    query = """
        SELECT condition_id, event_id, question, slug, category, tags, volume_usdc,
               liquidity_usdc, end_date, token_ids, active, closed, archived,
               observed_at
        FROM markets_snapshots
        WHERE observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
        ORDER BY volume_usdc DESC
        LIMIT {limit:UInt32}
    """
    result = await ch.query(query, parameters={"asof": asked_at, "limit": limit})
    out: list[dict[str, object]] = []
    for r in result.result_rows:
        if not bool(r[10]) or bool(r[11]) or bool(r[12]):
            continue  # skip inactive/closed/archived — dashboard is live markets
        out.append(
            {
                "condition_id": str(r[0]),
                "event_id": str(r[1] or ""),
                "question": str(r[2]),
                "slug": str(r[3]),
                "category": str(r[4]) if r[4] else None,
                "tags": list(r[5]) if r[5] else [],
                "volume_usdc": float(r[6]),
                "liquidity_usdc": float(r[7]),
                "end_date": r[8],
                "token_ids": list(r[9]) if r[9] else [],
                "observed_at": r[13],
            }
        )
    return out


async def _latest_mids_for_tokens(
    ch: AsyncClient, token_ids: list[str], asked_at: datetime
) -> dict[str, float]:
    """Latest mid per token, batched. Tokens we've never seen return absent."""
    if not token_ids:
        return {}
    query = """
        SELECT token_id, mid
        FROM market_quotes
        WHERE token_id IN {toks:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY token_id, observed_at DESC
        LIMIT 1 BY token_id
    """
    result = await ch.query(
        query, parameters={"toks": list(token_ids), "asof": asked_at}
    )
    return {str(r[0]): float(r[1]) for r in result.result_rows}


async def _latest_sibling_markets_for_events(
    ch: AsyncClient, event_ids: list[str], asked_at: datetime
) -> dict[str, list[dict[str, object]]]:
    if not event_ids:
        return {}
    query = """
        SELECT condition_id, event_id, token_ids, active, closed, archived
        FROM markets_snapshots
        WHERE event_id IN {event_ids:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
    """
    result = await ch.query(
        query,
        parameters={"event_ids": list(event_ids), "asof": asked_at},
    )
    out: dict[str, list[dict[str, object]]] = {}
    for row in result.result_rows:
        if not bool(row[3]) or bool(row[4]) or bool(row[5]):
            continue
        event_id = str(row[1] or "")
        if not event_id:
            continue
        out.setdefault(event_id, []).append(
            {
                "condition_id": str(row[0]),
                "event_id": event_id,
                "token_ids": list(row[2]) if row[2] else [],
            }
        )
    for siblings in out.values():
        siblings.sort(key=lambda snap: str(snap["condition_id"]))
    return out


def _build_multi_outcome_contexts(
    siblings_by_event: dict[str, list[dict[str, object]]],
    mids: dict[str, float],
) -> dict[str, MultiOutcomeContext]:
    contexts: dict[str, MultiOutcomeContext] = {}
    for siblings in siblings_by_event.values():
        ordered = sorted(siblings, key=lambda snap: str(snap["condition_id"]))
        raw_pairs: list[tuple[str, float]] = []
        for snap in ordered:
            token_ids = list(snap.get("token_ids") or [])  # type: ignore[arg-type]
            if not token_ids:
                continue
            mid = mids.get(str(token_ids[0]))
            if mid is None:
                continue
            raw_pairs.append((str(snap["condition_id"]), mid))
        if len(raw_pairs) < 2:
            continue
        raw = [mid for _, mid in raw_pairs]
        for idx, (condition_id, _) in enumerate(raw_pairs):
            contexts[condition_id] = MultiOutcomeContext(raw=raw, self_index=idx)
    return contexts


async def _multi_outcome_contexts_for_markets(
    ch: AsyncClient,
    snaps: list[dict[str, object]],
    asked_at: datetime,
) -> dict[str, MultiOutcomeContext]:
    event_ids = sorted(
        {
            str(snap.get("event_id") or "")
            for snap in snaps
            if str(snap.get("event_id") or "")
        }
    )
    siblings_by_event = await _latest_sibling_markets_for_events(ch, event_ids, asked_at)
    sibling_yes_tokens: list[str] = []
    for siblings in siblings_by_event.values():
        for snap in siblings:
            token_ids = list(snap.get("token_ids") or [])  # type: ignore[arg-type]
            if token_ids:
                sibling_yes_tokens.append(str(token_ids[0]))
    sibling_mids = await _latest_mids_for_tokens(ch, sibling_yes_tokens, asked_at)
    return _build_multi_outcome_contexts(siblings_by_event, sibling_mids)


def _discrete_config_from_settings(settings: Settings) -> DiscreteBaselineConfig:
    return DiscreteBaselineConfig(
        baselines_file=settings.discrete_baselines_file,
        fomc_fedwatch_prob=settings.fomc_fedwatch_prob,
        cpi_consensus_point=settings.cpi_consensus_point,
        cpi_consensus_stddev=settings.cpi_consensus_stddev,
        nfp_consensus_point=settings.nfp_consensus_point,
        nfp_consensus_stddev=settings.nfp_consensus_stddev,
    )


def _deribit_currency(asset: str | None) -> str | None:
    return {
        "BTC": "BTC",
        "ETH": "ETH",
        "SOL": "SOL",
    }.get(asset or "")


def _horizon_days(asked_at: datetime, resolution_date: datetime | None) -> float | None:
    if resolution_date is None:
        return None
    if resolution_date.tzinfo is None:
        resolution_date = resolution_date.replace(tzinfo=UTC)
    return (resolution_date - asked_at).total_seconds() / 86_400.0


async def _baseline_inputs_for(
    *,
    classification: ClassificationResult,
    snap: dict[str, object],
    market_mid: float | None,
    asked_at: datetime,
    deribit: DeribitClient | None,
    fedwatch: CMEFedWatchClient | None,
    discrete: DiscreteBaselineConfig,
    context: BaselineContext | None = None,
) -> BaselineInputs:
    inputs = BaselineInputs(
        features=classification.features,
        asked_at=asked_at.timestamp(),
        market_mid=market_mid,
    )

    if classification.market_type in (MarketType.THRESHOLD, MarketType.RANGE) and deribit is not None:
        currency = _deribit_currency(classification.features.asset)
        horizon_days = _horizon_days(asked_at, classification.features.resolution_date)
        if currency is not None and horizon_days is not None and horizon_days > 0:
            strike = classification.features.strike
            direction = classification.features.direction
            if classification.market_type is MarketType.RANGE:
                lo = classification.features.range_low
                hi = classification.features.range_high
                if lo is not None and hi is not None:
                    strike = (lo + hi) / 2.0
                direction = "above"
            if strike is not None and direction is not None:
                iv = await deribit.fetch_iv(
                    currency=currency,
                    strike=float(strike),
                    direction=direction,
                    horizon_days=horizon_days,
                )
                if iv is not None:
                    return BaselineInputs(
                        features=classification.features,
                        asked_at=asked_at.timestamp(),
                        market_mid=market_mid,
                        spot=iv.index_price,
                        realized_vol=iv.realized_vol,
                        implied_vol_atm=iv.atm_iv,
                        implied_vol_strike=iv.strike_iv,
                    )

    if classification.market_type is MarketType.DISCRETE_EVENT:
        family = classification.features.event_family
        record = resolve_discrete_record(
            path=discrete.baselines_file,
            event_family=family,
            condition_id=str(snap.get("condition_id") or ""),
            slug=str(snap.get("slug") or ""),
            question=str(snap.get("question") or ""),
        )
        if record is None and family == "fomc" and fedwatch is not None:
            record = await fedwatch.implied_record_for_market(
                question=str(snap.get("question") or ""),
                slug=str(snap.get("slug") or ""),
                now=asked_at,
            )
        fedwatch_prob = (
            record.fedwatch_prob if record is not None else discrete.fomc_fedwatch_prob
        )
        consensus_point = (
            record.consensus_point
            if record is not None and record.consensus_point is not None
            else _consensus_point_for_family(discrete, family)
        )
        consensus_stddev = (
            record.consensus_stddev
            if record is not None and record.consensus_stddev is not None
            else _consensus_stddev_for_family(discrete, family)
        )
        if family == "fomc" and fedwatch_prob is not None:
            return BaselineInputs(
                features=classification.features,
                asked_at=asked_at.timestamp(),
                market_mid=market_mid,
                fedwatch_prob=fedwatch_prob,
            )
        if family == "cpi" and (
            consensus_point is not None
            and consensus_stddev is not None
        ):
            return BaselineInputs(
                features=classification.features,
                asked_at=asked_at.timestamp(),
                market_mid=market_mid,
                consensus_point=consensus_point,
                consensus_stddev=consensus_stddev,
            )
        if family == "nfp" and (
            consensus_point is not None
            and consensus_stddev is not None
        ):
            return BaselineInputs(
                features=classification.features,
                asked_at=asked_at.timestamp(),
                market_mid=market_mid,
                consensus_point=consensus_point,
                consensus_stddev=consensus_stddev,
            )

    if (
        classification.market_type is MarketType.MULTI_OUTCOME
        and context is not None
        and context.multi_outcome is not None
    ):
        return BaselineInputs(
            features=classification.features,
            asked_at=asked_at.timestamp(),
            market_mid=market_mid,
            multi_outcome_raw=list(context.multi_outcome.raw),
            multi_outcome_self_index=context.multi_outcome.self_index,
        )

    if classification.market_type is MarketType.LONG_TAIL_BINARY:
        long_tail = context.long_tail if context is not None else None
        if long_tail is not None:
            return BaselineInputs(
                features=classification.features,
                asked_at=asked_at.timestamp(),
                market_mid=market_mid,
                embedding_prior=long_tail.embedding_prior,
                historical_base_rate=long_tail.historical_base_rate,
            )

    return inputs


def _consensus_point_for_family(
    discrete: DiscreteBaselineConfig, family: str | None
) -> float | None:
    if family == "cpi":
        return discrete.cpi_consensus_point
    if family == "nfp":
        return discrete.nfp_consensus_point
    return None


def _consensus_stddev_for_family(
    discrete: DiscreteBaselineConfig, family: str | None
) -> float | None:
    if family == "cpi":
        return discrete.cpi_consensus_stddev
    if family == "nfp":
        return discrete.nfp_consensus_stddev
    return None


async def _pipeline_for_row(
    *,
    snap: dict[str, object],
    mid: float | None,
    classification: ClassificationRow | None,
    asked_at: datetime,
    deribit: DeribitClient | None,
    fedwatch: CMEFedWatchClient | None,
    discrete: DiscreteBaselineConfig,
    context: BaselineContext | None = None,
) -> tuple[PipelineResult, ClassificationRow]:
    """Run the model pipeline against one market. If the classifier table has
    no row yet (e.g. ingest hasn't classified this market), fall back to an
    on-the-fly classify so the API still returns a type.
    """
    if classification is None:
        result = classify(
            question=str(snap["question"]),
            description=None,
            tags=list(snap.get("tags") or []),
            end_date=snap.get("end_date"),  # type: ignore[arg-type]
            multi_outcome_sibling_count=(
                len(context.multi_outcome.raw)
                if context is not None and context.multi_outcome is not None
                else 0
            ),
        )
        classification = ClassificationRow(
            condition_id=str(snap["condition_id"]),
            market_type=result.market_type,
            confidence=result.confidence,
            needs_review=result.needs_review,
            reviewer_flag="pending" if result.needs_review else "",
            classifier="regex_v1",
            features=result.features,
            reasons=result.reasons,
            event_time=asked_at,
            observed_at=asked_at,
        )

    # Reconstruct a ClassificationResult-shaped object for the pipeline.
    cls_result = ClassificationResult(
        market_type=classification.market_type,
        confidence=classification.confidence,
        features=classification.features,
        reasons=classification.reasons,
    )
    if (
        cls_result.market_type is MarketType.LONG_TAIL_BINARY
        and context is not None
        and context.long_tail is None
        and context.long_tail_candidates is not None
    ):
        context = BaselineContext(
            multi_outcome=context.multi_outcome,
            long_tail=long_tail_prior_context(
                current_condition_id=str(snap["condition_id"]),
                question=str(snap["question"]),
                slug=str(snap.get("slug") or ""),
                tags=list(snap.get("tags") or []),  # type: ignore[arg-type]
                classification=cls_result,
                candidates=context.long_tail_candidates,
            ),
            long_tail_candidates=context.long_tail_candidates,
        )
    inputs = await _baseline_inputs_for(
        classification=cls_result,
        snap=snap,
        market_mid=mid,
        asked_at=asked_at,
        deribit=deribit,
        fedwatch=fedwatch,
        discrete=discrete,
        context=context,
    )
    return probability_for_market(cls_result, inputs), classification


def _time_to_resolution_s(end_date: datetime | None, asked_at: datetime) -> float | None:
    if end_date is None:
        return None
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=asked_at.tzinfo)
    return (end_date - asked_at).total_seconds()


def _edge_bps(model_prob: float | None, market_mid: float | None) -> float | None:
    if model_prob is None or market_mid is None:
        return None
    return (model_prob - market_mid) * 10_000.0


def _ensemble_sample_for_row(
    *,
    classification: ClassificationRow,
    pipeline_result: PipelineResult,
    market_mid: float | None,
    feature_snapshot: FeatureSnapshotRow | None,
    asked_at: datetime,
) -> EnsembleSample | None:
    if pipeline_result.baseline.probability is None or feature_snapshot is None:
        return None
    return EnsembleSample(
        market_type=classification.market_type,
        p_base=float(pipeline_result.baseline.probability),
        market_mid=market_mid,
        spread=feature_snapshot.spread,
        book_imbalance_1pct=feature_snapshot.book_imbalance_1pct,
        book_imbalance_5pct=feature_snapshot.book_imbalance_5pct,
        momentum_1h=feature_snapshot.momentum_1h,
        momentum_24h=feature_snapshot.momentum_24h,
        momentum_7d=feature_snapshot.momentum_7d,
        realized_vol_24h=feature_snapshot.realized_vol_24h,
        informed_taker_flow_24h=feature_snapshot.informed_taker_flow_24h,
        passive_maker_flow_24h=feature_snapshot.passive_maker_flow_24h,
        decayed_directional_flow_24h=feature_snapshot.decayed_directional_flow_24h,
        asked_at=asked_at.timestamp(),
    )


def _apply_ensemble_refinement(
    *,
    registry: EnsembleRegistry | None,
    classification: ClassificationRow,
    pipeline_result: PipelineResult,
    market_mid: float | None,
    feature_snapshot: FeatureSnapshotRow | None,
    asked_at: datetime,
) -> tuple[float | None, str, str | None, list[str]]:
    baseline_prob = pipeline_result.displayed_probability
    baseline_reasons = list(pipeline_result.baseline.reasons)
    if registry is None:
        return baseline_prob, "baseline", None, baseline_reasons
    if registry.model_for_type(classification.market_type) is None:
        return baseline_prob, "baseline", None, baseline_reasons

    sample = _ensemble_sample_for_row(
        classification=classification,
        pipeline_result=pipeline_result,
        market_mid=market_mid,
        feature_snapshot=feature_snapshot,
        asked_at=asked_at,
    )
    if sample is None:
        reason = (
            "ensemble skipped: no persisted feature snapshot"
            if feature_snapshot is None
            else "ensemble skipped: no baseline probability"
        )
        return baseline_prob, "baseline", None, [*baseline_reasons, reason]

    refined = registry.predict_probability(sample)
    if refined is None:
        return baseline_prob, "baseline", None, baseline_reasons
    model_reasons = [
        f"Per-type ensemble refinement over {pipeline_result.displayed_source.value}",
        f"PIT feature snapshot @ {feature_snapshot.observed_at.isoformat()}",
    ]
    return refined, "ensemble", "per_type_ensemble_v1", model_reasons


def _apply_conformal_interval(
    *,
    registry: SplitConformalRegistry | None,
    classification: ClassificationRow,
    model_prob: float | None,
    time_to_resolution_s: float | None,
    uncertainty_multiplier: float,
    resolution_risk_multiplier: float,
) -> tuple[float | None, float | None, float | None, str | None]:
    if registry is None or model_prob is None:
        return None, None, None, None
    interval = registry.interval(
        predicted_prob=model_prob,
        market_type=classification.market_type,
        time_to_resolution_s=time_to_resolution_s,
        uncertainty_multiplier=uncertainty_multiplier,
        resolution_risk_multiplier=resolution_risk_multiplier,
    )
    if interval is None:
        return None, None, None, None
    lo, hi = interval
    return lo, hi, registry.coverage, "split_conformal_mondrian"


async def list_markets(
    ch: AsyncClient, *, asked_at: datetime, limit: int = 200
) -> list[MarketListRow]:
    snaps = await _latest_markets(ch, asked_at, limit=limit)
    if not snaps:
        return []

    all_tokens: list[str] = []
    for s in snaps:
        all_tokens.extend(list(s.get("token_ids") or []))  # type: ignore[arg-type]
    mids = await _latest_mids_for_tokens(ch, all_tokens, asked_at)
    classifications = await classifications_batch_asof(
        ch, [str(s["condition_id"]) for s in snaps], asked_at
    )
    feature_snapshots = await feature_snapshots_batch_asof(
        ch, [str(s["condition_id"]) for s in snaps], asked_at
    )
    multi_outcome_contexts = await _multi_outcome_contexts_for_markets(ch, snaps, asked_at)
    long_tail_candidates = await load_long_tail_resolved_markets(ch, asked_at=asked_at)

    settings = get_settings()
    discrete = _discrete_config_from_settings(settings)
    registry = load_ensemble_registry(settings.ensemble_registry_file)
    rows: list[MarketListRow] = []
    async with AsyncExitStack() as stack:
        deribit = await stack.enter_async_context(
            DeribitClient(base_url=settings.deribit_base)
        )
        fedwatch = None
        if settings.cme_api_id and settings.cme_api_secret:
            fedwatch = await stack.enter_async_context(
                CMEFedWatchClient(
                    base_url=settings.cme_fedwatch_base,
                    token_url=settings.cme_oauth_token_url,
                    api_id=settings.cme_api_id,
                    api_secret=settings.cme_api_secret,
                    application_name=settings.cme_application_name,
                    application_vendor=settings.cme_application_vendor,
                    application_version=settings.cme_application_version,
                    user_agent=f"{settings.cme_application_name}/{settings.cme_application_version}",
                )
            )
        for snap in snaps:
            tok_ids = list(snap.get("token_ids") or [])  # type: ignore[arg-type]
            yes_mid: float | None = mids.get(tok_ids[0]) if tok_ids else None
            cid = str(snap["condition_id"])
            classification = classifications.get(cid)
            if classification is not None and classification.market_type is MarketType.LONG_TAIL_BINARY:
                long_tail = long_tail_prior_context(
                    current_condition_id=cid,
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
            else:
                long_tail = None
            result, classification = await _pipeline_for_row(
                snap=snap,
                mid=yes_mid,
                classification=classification,
                asked_at=asked_at,
                deribit=deribit,
                fedwatch=fedwatch,
                discrete=discrete,
                context=BaselineContext(
                    multi_outcome=multi_outcome_contexts.get(cid),
                    long_tail=long_tail,
                    long_tail_candidates=long_tail_candidates,
                ),
            )
            model_prob, model_source, refinement_source, _ = _apply_ensemble_refinement(
                registry=registry,
                classification=classification,
                pipeline_result=result,
                market_mid=yes_mid,
                feature_snapshot=feature_snapshots.get(cid),
                asked_at=asked_at,
            )
            rows.append(
                MarketListRow(
                    condition_id=cid,
                    question=str(snap["question"]),
                    slug=str(snap["slug"]),
                    category=snap.get("category"),  # type: ignore[arg-type]
                    tags=list(snap.get("tags") or []),  # type: ignore[arg-type]
                    volume_usdc=float(snap["volume_usdc"]),  # type: ignore[arg-type]
                    liquidity_usdc=float(snap["liquidity_usdc"]),  # type: ignore[arg-type]
                    end_date=snap.get("end_date"),  # type: ignore[arg-type]
                    token_ids=tok_ids,
                    mid=yes_mid,
                    market_type=str(classification.market_type.value),
                    model_prob=model_prob,
                    model_source=model_source,
                    refinement_source=refinement_source,
                    baseline_source=str(result.displayed_source.value),
                    edge_bps=_edge_bps(model_prob, yes_mid),
                    needs_review=classification.needs_review,
                    confidence=classification.confidence,
                    time_to_resolution_s=_time_to_resolution_s(
                        snap.get("end_date"),  # type: ignore[arg-type]
                        asked_at,
                    ),
                )
            )
    return rows


@dataclass(frozen=True)
class MarketModelDetail:
    condition_id: str
    question: str
    market_type: str
    confidence: float
    needs_review: bool
    classifier: str
    mid: float | None
    model_prob: float | None
    model_source: str
    refinement_source: str | None
    baseline_source: str
    edge_bps: float | None
    uncertainty_multiplier: float
    band_lo: float | None
    band_hi: float | None
    band_coverage: float | None
    uncertainty_source: str | None
    resolution_risk_multiplier: float
    kelly_side: str | None
    kelly_fraction: float | None
    kelly_uncapped_fraction: float | None
    kelly_fractional_multiplier: float
    kelly_cap: float
    reasons: list[str]
    model_reasons: list[str]
    classifier_reasons: list[str]
    driver_summaries: list[str]
    feature_attributions: list[dict[str, object]]


@dataclass(frozen=True)
class MarketHistoryPoint:
    event_time: datetime
    market_mid: float
    model_prob: float | None


def _kelly_recommendation(
    *,
    model_prob: float | None,
    market_mid: float | None,
    fractional_multiplier: float,
    cap: float,
) -> tuple[str | None, float | None, float | None]:
    if model_prob is None or market_mid is None:
        return None, None, None
    if model_prob > market_mid and market_mid < 1.0:
        full_kelly = (model_prob - market_mid) / max(1.0 - market_mid, 1e-9)
        if full_kelly <= 0:
            return None, None, None
        return "YES", min(full_kelly * fractional_multiplier, cap), full_kelly
    if model_prob < market_mid and market_mid > 0.0:
        full_kelly = (market_mid - model_prob) / max(market_mid, 1e-9)
        if full_kelly <= 0:
            return None, None, None
        return "NO", min(full_kelly * fractional_multiplier, cap), full_kelly
    return None, None, None


def _sample_history_rows(rows: list[asof_q.QuoteRow], max_points: int) -> list[asof_q.QuoteRow]:
    if len(rows) <= max_points:
        return rows
    if max_points <= 1:
        return [rows[-1]]
    indices = {
        round(idx * (len(rows) - 1) / (max_points - 1))
        for idx in range(max_points)
    }
    return [rows[idx] for idx in sorted(indices)]


def _attribution_label(feature_name: str) -> str:
    labels = {
        "p_base_logit": "Baseline prior",
        "market_mid_logit": "Market price",
        "spread": "Spread",
        "book_imbalance_1pct": "1% book imbalance",
        "book_imbalance_5pct": "5% book imbalance",
        "momentum_1h": "1h momentum",
        "momentum_24h": "24h momentum",
        "momentum_7d": "7d momentum",
        "realized_vol_24h": "24h realized vol",
        "informed_taker_flow_24h": "Informed taker flow",
        "passive_maker_flow_24h": "Passive maker flow",
        "decayed_directional_flow_24h": "Decayed directional flow",
    }
    return labels.get(feature_name, feature_name.replace("_", " "))


def _format_feature_value(feature_name: str, feature_value: float | None) -> str:
    if feature_value is None:
        return "n/a"
    if feature_name in {"p_base_logit", "market_mid_logit", "realized_vol_24h"}:
        return f"{feature_value * 100:.1f}%"
    return f"{feature_value:+.3f}"


def _driver_summary(contribution: FeatureContribution) -> str:
    label = _attribution_label(contribution.feature_name)
    direction = "higher" if contribution.score_contribution >= 0 else "lower"
    return (
        f"{label} pushed the ensemble {direction}; "
        f"value {_format_feature_value(contribution.feature_name, contribution.feature_value)}, "
        f"score {contribution.score_contribution:+.3f}."
    )


def _feature_attributions_for_sample(
    *,
    registry: EnsembleRegistry | None,
    model_source: str,
    sample: EnsembleSample | None,
) -> tuple[list[str], list[dict[str, object]]]:
    if registry is None or sample is None or model_source != "ensemble":
        return [], []
    explanation = registry.explain_prediction(sample)
    if explanation is None:
        return [], []
    ranked = [
        contribution
        for contribution in explanation.contributions
        if contribution.feature_name != "intercept"
    ]
    attributions = [
        {
            "feature_name": contribution.feature_name,
            "label": _attribution_label(contribution.feature_name),
            "feature_value": contribution.feature_value,
            "score_contribution": contribution.score_contribution,
            "transformed_value": contribution.transformed_value,
        }
        for contribution in ranked
    ]
    return [
        _driver_summary(contribution)
        for contribution in ranked[:3]
    ], attributions


async def model_for_market(
    ch: AsyncClient, *, condition_id: str, asked_at: datetime
) -> MarketModelDetail | None:
    """Model-probability + provenance payload for the detail view."""
    query = """
        SELECT condition_id, event_id, question, slug, category, tags, volume_usdc,
               liquidity_usdc, end_date, token_ids, observed_at
        FROM markets_snapshots
        WHERE condition_id = {cond:String}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(
        query, parameters={"cond": condition_id, "asof": asked_at}
    )
    rows = result.result_rows
    if not rows:
        return None
    r = rows[0]
    snap = {
        "condition_id": str(r[0]),
        "event_id": str(r[1] or ""),
        "question": str(r[2]),
        "slug": str(r[3]),
        "category": str(r[4]) if r[4] else None,
        "tags": list(r[5]) if r[5] else [],
        "volume_usdc": float(r[6]),
        "liquidity_usdc": float(r[7]),
        "end_date": r[8],
        "token_ids": list(r[9]) if r[9] else [],
    }
    tok_ids = list(snap["token_ids"])  # type: ignore[arg-type]
    mids = await _latest_mids_for_tokens(ch, tok_ids, asked_at)
    yes_mid = mids.get(tok_ids[0]) if tok_ids else None

    classification = await classification_asof(ch, condition_id, asked_at)
    feature_snapshot = await feature_snapshot_asof(ch, condition_id, asked_at)
    multi_outcome_contexts = await _multi_outcome_contexts_for_markets(ch, [snap], asked_at)
    long_tail_candidates = await load_long_tail_resolved_markets(ch, asked_at=asked_at)
    time_to_resolution_s = _time_to_resolution_s(
        snap.get("end_date"),  # type: ignore[arg-type]
        asked_at,
    )
    settings = get_settings()
    discrete = _discrete_config_from_settings(settings)
    registry = load_ensemble_registry(settings.ensemble_registry_file)
    conformal_registry = load_conformal_registry(settings.conformal_registry_file)
    async with AsyncExitStack() as stack:
        deribit = await stack.enter_async_context(
            DeribitClient(base_url=settings.deribit_base)
        )
        fedwatch = None
        if settings.cme_api_id and settings.cme_api_secret:
            fedwatch = await stack.enter_async_context(
                CMEFedWatchClient(
                    base_url=settings.cme_fedwatch_base,
                    token_url=settings.cme_oauth_token_url,
                    api_id=settings.cme_api_id,
                    api_secret=settings.cme_api_secret,
                    application_name=settings.cme_application_name,
                    application_vendor=settings.cme_application_vendor,
                    application_version=settings.cme_application_version,
                    user_agent=f"{settings.cme_application_name}/{settings.cme_application_version}",
                )
            )
        long_tail = None
        if classification is not None and classification.market_type is MarketType.LONG_TAIL_BINARY:
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
    model_prob, model_source, refinement_source, model_reasons = _apply_ensemble_refinement(
        registry=registry,
        classification=classification,
        pipeline_result=pipeline_result,
        market_mid=yes_mid,
        feature_snapshot=feature_snapshot,
        asked_at=asked_at,
    )
    driver_summaries, feature_attributions = _feature_attributions_for_sample(
        registry=registry,
        model_source=model_source,
        sample=_ensemble_sample_for_row(
            classification=classification,
            pipeline_result=pipeline_result,
            market_mid=yes_mid,
            feature_snapshot=feature_snapshot,
            asked_at=asked_at,
        ),
    )
    kelly_side, kelly_fraction, kelly_uncapped_fraction = _kelly_recommendation(
        model_prob=model_prob,
        market_mid=yes_mid,
        fractional_multiplier=settings.fractional_kelly_default,
        cap=settings.kelly_cap_default,
    )
    band_lo, band_hi, band_coverage, uncertainty_source = _apply_conformal_interval(
        registry=conformal_registry,
        classification=classification,
        model_prob=model_prob,
        time_to_resolution_s=time_to_resolution_s,
        uncertainty_multiplier=pipeline_result.baseline.uncertainty_multiplier,
        resolution_risk_multiplier=settings.resolution_risk_multiplier_default,
    )
    return MarketModelDetail(
        condition_id=condition_id,
        question=str(snap["question"]),
        market_type=str(classification.market_type.value),
        confidence=classification.confidence,
        needs_review=classification.needs_review,
        classifier=classification.classifier,
        mid=yes_mid,
        model_prob=model_prob,
        model_source=model_source,
        refinement_source=refinement_source,
        baseline_source=str(pipeline_result.displayed_source.value),
        edge_bps=_edge_bps(model_prob, yes_mid),
        uncertainty_multiplier=pipeline_result.baseline.uncertainty_multiplier,
        band_lo=band_lo,
        band_hi=band_hi,
        band_coverage=band_coverage,
        uncertainty_source=uncertainty_source,
        resolution_risk_multiplier=settings.resolution_risk_multiplier_default,
        kelly_side=kelly_side,
        kelly_fraction=kelly_fraction,
        kelly_uncapped_fraction=kelly_uncapped_fraction,
        kelly_fractional_multiplier=settings.fractional_kelly_default,
        kelly_cap=settings.kelly_cap_default,
        reasons=list(pipeline_result.baseline.reasons),
        model_reasons=model_reasons,
        classifier_reasons=list(classification.reasons),
        driver_summaries=driver_summaries,
        feature_attributions=feature_attributions,
    )


async def history_for_market(
    ch: AsyncClient,
    *,
    condition_id: str,
    asked_at: datetime,
    window_hours: int = 24 * 7,
    max_points: int = 96,
) -> list[MarketHistoryPoint]:
    snapshot = await asof_q.latest_market_snapshot_asof(ch, condition_id, asked_at)
    if snapshot is None or not snapshot.token_ids:
        return []
    start = asked_at
    if window_hours > 0:
        start = datetime.fromtimestamp(
            asked_at.timestamp() - window_hours * 3600,
            tz=asked_at.tzinfo,
        )
    quote_rows = await asof_q.quotes_timeseries_pit(
        ch,
        token_id=snapshot.token_ids[0],
        start=start,
        end=asked_at,
    )
    sampled_rows = _sample_history_rows(quote_rows, max_points=max_points)
    history: list[MarketHistoryPoint] = []
    for row in sampled_rows:
        detail = await model_for_market(ch, condition_id=condition_id, asked_at=row.event_time)
        history.append(
            MarketHistoryPoint(
                event_time=row.event_time,
                market_mid=row.mid,
                model_prob=detail.model_prob if detail is not None else None,
            )
        )
    return history
