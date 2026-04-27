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
from typing import Any

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
    SiblingMarket,
    SiblingPrior,
    SiblingQuote,
    SplitConformalRegistry,
    build_threshold_pairs,
    classify,
    probability_for_market,
    sibling_prior_for_market,
)

from . import asof as asof_q
from . import model_status
from .adversarial_flow import (
    AdversarialFlowContext,
    adversarial_flow_asof,
    adversarial_flow_batch_contexts,
)
from .classifications import (
    ClassificationRow,
    classification_asof,
    classifications_batch_asof,
)
from .cme_fedwatch import CMEFedWatchClient
from .concentration import (
    MarketConcentration,
    concentration_asof,
    concentration_batch_asof,
)
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
from .macro_features import MacroFeatureContext, load_macro_feature_context
from .model_status import ModelDisableStatus, model_disable_status_asof
from .onchain_features import (
    OnchainFeatureContext,
    load_onchain_feature_context,
)
from .regime import regime_label_asof
from .resolution_risk import (
    ResolutionRiskRow,
    resolution_risk_asof,
    resolution_risk_batch_asof,
)
from .settings import Settings, get_settings
from .smart_money import (
    SmartMoneyPerMarket,
    smart_money_asof,
    smart_money_batch_asof,
)
from .social_features import (
    SocialFeatures,
    social_features_batch_asof,
    social_features_for_market,
)
from .tuning import TuningAdjustmentContext, TuningProfile, apply_tuning_adjustment


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
    # M3 Polymarket-native signals (nullable when data has not yet landed).
    smart_money_consensus: float | None
    smart_money_sample_wallets: int | None
    smart_money_dominant: str | None
    concentration_score: float | None
    concentration_whale_flag: bool | None
    resolution_risk_score: float | None
    resolution_risk_level: str | None
    resolution_risk_flagged: bool
    adversarial_flow_score: float | None
    adversarial_flow_flagged: bool
    thin_book: bool


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


def _tuning_context_for_row(
    *,
    model_prob: float | None,
    sibling_prior: SiblingPrior | None,
    smart_money: SmartMoneyPerMarket | None,
    concentration: MarketConcentration | None,
    resolution_risk: ResolutionRiskRow | None,
    adversarial_flow: AdversarialFlowContext | None,
) -> TuningAdjustmentContext:
    return TuningAdjustmentContext(
        model_prob=model_prob,
        smart_money_consensus=(
            smart_money.consensus_score if smart_money is not None else None
        ),
        smart_money_dominant=(
            smart_money.dominant_outcome if smart_money is not None else None
        ),
        sibling_implied_prior=(
            sibling_prior.implied_prior if sibling_prior is not None else None
        ),
        concentration_score=_concentration_score(concentration),
        resolution_risk_score=(
            resolution_risk.risk_score if resolution_risk is not None else None
        ),
        adversarial_flow_score=(
            adversarial_flow.score if adversarial_flow is not None else None
        ),
    )


def _sibling_market_rows(
    snaps: list[dict[str, object]],
    classifications: dict[str, ClassificationRow],
    mids: dict[str, float],
) -> dict[str, SiblingMarket]:
    out: dict[str, SiblingMarket] = {}
    for snap in snaps:
        condition_id = str(snap["condition_id"])
        classification = classifications.get(condition_id)
        if classification is None:
            continue
        if classification.market_type not in {MarketType.THRESHOLD, MarketType.MULTI_OUTCOME}:
            continue
        token_ids = list(snap.get("token_ids") or [])  # type: ignore[arg-type]
        yes_mid = mids.get(token_ids[0]) if token_ids else None
        out[condition_id] = SiblingMarket(
            condition_id=condition_id,
            question=str(snap["question"]),
            event_id=str(snap.get("event_id") or "") or None,
            market_type=classification.market_type,
            asset=classification.features.asset,
            direction=classification.features.direction,
            strike=classification.features.strike,
            resolution_date=classification.features.resolution_date,
            quote=SiblingQuote(
                mid=yes_mid,
                best_bid=None,
                best_ask=None,
            ),
        )
    return out


def _sibling_priors_by_condition(
    sibling_markets: dict[str, SiblingMarket],
) -> dict[str, SiblingPrior]:
    threshold_pairs = build_threshold_pairs(list(sibling_markets.values()))
    out: dict[str, SiblingPrior] = {}
    for condition_id, market in sibling_markets.items():
        prior = sibling_prior_for_market(market, threshold_pairs)
        if prior is not None and prior.implied_prior is not None:
            out[condition_id] = prior
    return out


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


def _is_crypto_market(snap: dict[str, object]) -> bool:
    category = str(snap.get("category") or "").lower()
    tags = [str(tag).lower() for tag in snap.get("tags") or []]  # type: ignore[union-attr]
    text = " ".join(
        [
            category,
            str(snap.get("question") or "").lower(),
            str(snap.get("slug") or "").lower(),
            " ".join(tags),
        ]
    )
    crypto_terms = {
        "crypto",
        "bitcoin",
        "btc",
        "ethereum",
        "eth",
        "solana",
        "sol",
        "xrp",
        "doge",
        "binance",
        "coinbase",
        "stablecoin",
        "usdt",
        "usdc",
        "defi",
        "etf",
    }
    return any(term in text for term in crypto_terms)


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


def _concentration_score(concentration: MarketConcentration | None) -> float | None:
    """Single-axis summary of how concentrated holders are.

    Prefers the pre-computed ``max_gini`` (max of YES/NO Gini). Falls back to
    ``max(top1_pct)`` when Gini isn't computable (e.g. < 2 holders in the
    top-N), which is still a monotone concentration proxy.
    """
    if concentration is None:
        return None
    if concentration.max_gini is not None:
        return concentration.max_gini
    top1s = [
        value
        for value in (concentration.yes_top1_pct, concentration.no_top1_pct)
        if value is not None
    ]
    return max(top1s) if top1s else None


def _smart_money_consensus_feature(
    smart_money: SmartMoneyPerMarket | None,
    concentration: MarketConcentration | None,
    *,
    concentration_threshold: float,
) -> float | None:
    """PRD: down-weight ``smart_money_consensus`` when concentration > 0.6.

    When concentration is high the "smart money" signal is much more likely
    to be a handful of whales, not diffuse conviction, so we attenuate the
    raw consensus score toward zero before the ensemble sees it. Below the
    threshold the consensus passes through unchanged.
    """
    if smart_money is None:
        return None
    raw = smart_money.consensus_score
    score = _concentration_score(concentration)
    if score is None or score <= concentration_threshold:
        return raw
    # Linear attenuation from the threshold to a fully concentrated market.
    # At score == threshold the feature is unchanged; at score == 1 it is
    # scaled to zero. Between those, we shrink proportionally.
    span = max(1e-9, 1.0 - concentration_threshold)
    attenuation = max(0.0, 1.0 - (score - concentration_threshold) / span)
    return raw * attenuation


def _ensemble_sample_for_row(
    *,
    classification: ClassificationRow,
    pipeline_result: PipelineResult,
    market_mid: float | None,
    sibling_prior: SiblingPrior | None,
    feature_snapshot: FeatureSnapshotRow | None,
    smart_money: SmartMoneyPerMarket | None,
    concentration: MarketConcentration | None,
    resolution_risk: ResolutionRiskRow | None = None,
    adversarial_flow: AdversarialFlowContext | None = None,
    regime_label: str | None = None,
    onchain: OnchainFeatureContext | None = None,
    macro: MacroFeatureContext | None = None,
    social: SocialFeatures | None = None,
    concentration_threshold: float,
    asked_at: datetime,
) -> EnsembleSample | None:
    if pipeline_result.baseline.probability is None or feature_snapshot is None:
        return None
    onchain_asset_features = (
        onchain.for_asset(classification.features.asset)
        if onchain is not None
        else None
    )
    return EnsembleSample(
        market_type=classification.market_type,
        p_base=float(pipeline_result.baseline.probability),
        market_mid=market_mid,
        sibling_implied_prior=(
            sibling_prior.implied_prior
            if sibling_prior is not None
            else None
        ),
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
        smart_money_consensus=_smart_money_consensus_feature(
            smart_money,
            concentration,
            concentration_threshold=concentration_threshold,
        ),
        holder_concentration=_concentration_score(concentration),
        resolution_risk_score=(
            resolution_risk.risk_score if resolution_risk is not None else None
        ),
        adversarial_flow_score=(
            adversarial_flow.score if adversarial_flow is not None else None
        ),
        regime_label=regime_label,
        onchain_exchange_inflow_z=(
            onchain_asset_features.exchange_inflow_z
            if onchain_asset_features is not None
            else None
        ),
        onchain_exchange_outflow_z=(
            onchain_asset_features.exchange_outflow_z
            if onchain_asset_features is not None
            else None
        ),
        onchain_whale_count_delta_pct=(
            onchain_asset_features.whale_count_delta_pct
            if onchain_asset_features is not None
            else None
        ),
        onchain_stablecoin_supply_delta_pct=(
            onchain.stablecoin_supply_delta_pct if onchain is not None else None
        ),
        macro_fed_funds_30d_delta=(
            macro.fed_funds_30d_delta if macro is not None else None
        ),
        macro_treasury_10y_30d_delta=(
            macro.treasury_10y_30d_delta if macro is not None else None
        ),
        macro_yield_curve_2s10s=(
            macro.yield_curve_2s10s if macro is not None else None
        ),
        macro_cpi_yoy_pct=(macro.cpi_yoy_pct if macro is not None else None),
        social_post_count_24h=(
            float(social.post_count_24h) if social is not None else None
        ),
        social_reach_volume_24h=(
            float(social.reach_volume_24h) if social is not None else None
        ),
        social_novelty_score_latest=(
            float(social.novelty_score_latest) if social is not None else None
        ),
        social_sentiment_dispersion_24h=(
            float(social.sentiment_dispersion_24h) if social is not None else None
        ),
        social_tone_shift_24h_vs_7d=(
            float(social.tone_shift_24h_vs_7d) if social is not None else None
        ),
        asked_at=asked_at.timestamp(),
    )


def _apply_ensemble_refinement(
    *,
    registry: EnsembleRegistry | None,
    classification: ClassificationRow,
    pipeline_result: PipelineResult,
    market_mid: float | None,
    sibling_prior: SiblingPrior | None,
    feature_snapshot: FeatureSnapshotRow | None,
    smart_money: SmartMoneyPerMarket | None,
    concentration: MarketConcentration | None,
    resolution_risk: ResolutionRiskRow | None = None,
    adversarial_flow: AdversarialFlowContext | None = None,
    regime_label: str | None = None,
    onchain: OnchainFeatureContext | None = None,
    macro: MacroFeatureContext | None = None,
    social: SocialFeatures | None = None,
    disable_status: ModelDisableStatus | None = None,
    concentration_threshold: float,
    adversarial_flow_blend_floor: float,
    asked_at: datetime,
) -> tuple[float | None, str, str | None, list[str]]:
    baseline_prob = pipeline_result.displayed_probability
    baseline_reasons = list(pipeline_result.baseline.reasons)
    # PRD §8 auto-disable: when the per-type model is in `disabled` state
    # (drift driver flagged ≥7 consecutive negative-skill days) we MUST
    # short-circuit the ensemble and serve the baseline / market_mid path
    # instead. The reason is appended so the UI can show "auto-disabled".
    if disable_status is not None and disable_status.is_disabled:
        return (
            baseline_prob,
            "baseline_disabled",
            None,
            [
                *baseline_reasons,
                f"Ensemble auto-disabled for {classification.market_type.value}: "
                f"{disable_status.reason}",
            ],
        )
    if registry is None:
        return baseline_prob, "baseline", None, baseline_reasons
    if registry.model_for_type(classification.market_type) is None:
        return baseline_prob, "baseline", None, baseline_reasons

    sample = _ensemble_sample_for_row(
        classification=classification,
        pipeline_result=pipeline_result,
        market_mid=market_mid,
        sibling_prior=sibling_prior,
        feature_snapshot=feature_snapshot,
        smart_money=smart_money,
        concentration=concentration,
        resolution_risk=resolution_risk,
        adversarial_flow=adversarial_flow,
        regime_label=regime_label,
        onchain=onchain,
        macro=macro,
        social=social,
        concentration_threshold=concentration_threshold,
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
    if (
        adversarial_flow is not None
        and baseline_prob is not None
        and adversarial_flow.score > 0.0
    ):
        attenuation = max(
            adversarial_flow_blend_floor,
            1.0 - adversarial_flow.score,
        )
        refined = baseline_prob + (refined - baseline_prob) * attenuation
        model_reasons.append(
            f"Adversarial-flow guard applied; ensemble delta scaled to {attenuation:.2f}"
        )
    return refined, "ensemble", "per_type_ensemble_v1", model_reasons


def _apply_conformal_interval(
    *,
    registry: SplitConformalRegistry | None,
    classification: ClassificationRow,
    model_prob: float | None,
    time_to_resolution_s: float | None,
    uncertainty_multiplier: float,
    resolution_risk_multiplier: float,
    regime: str | None = None,
) -> tuple[float | None, float | None, float | None, str | None]:
    if registry is None or model_prob is None:
        return None, None, None, None
    interval = registry.interval(
        predicted_prob=model_prob,
        market_type=classification.market_type,
        time_to_resolution_s=time_to_resolution_s,
        uncertainty_multiplier=uncertainty_multiplier,
        resolution_risk_multiplier=resolution_risk_multiplier,
        regime=regime,
    )
    if interval is None:
        return None, None, None, None
    lo, hi = interval
    return lo, hi, registry.coverage, "split_conformal_mondrian"


async def list_markets(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    limit: int = 200,
    crypto_only: bool = False,
    tuning_profile: TuningProfile | None = None,
) -> list[MarketListRow]:
    all_snaps = await _latest_markets(ch, asked_at, limit=max(limit, 500))
    if not all_snaps:
        return []
    visible_snaps = [snap for snap in all_snaps if _is_crypto_market(snap)] if crypto_only else all_snaps
    snaps = visible_snaps[:limit]
    if not snaps:
        return []
    settings = get_settings()

    all_tokens: list[str] = []
    for s in all_snaps:
        all_tokens.extend(list(s.get("token_ids") or []))  # type: ignore[arg-type]
    mids = await _latest_mids_for_tokens(ch, all_tokens, asked_at)
    all_condition_ids = [str(s["condition_id"]) for s in all_snaps]
    condition_ids = [str(s["condition_id"]) for s in snaps]
    all_classifications = await classifications_batch_asof(ch, all_condition_ids, asked_at)
    classifications = {condition_id: all_classifications[condition_id] for condition_id in condition_ids if condition_id in all_classifications}
    feature_snapshots = await feature_snapshots_batch_asof(ch, condition_ids, asked_at)
    token_ids_by_condition = {
        str(s["condition_id"]): next(iter(s.get("token_ids") or []), "")
        for s in snaps
    }
    adversarial_rows = await adversarial_flow_batch_contexts(
        ch,
        condition_ids=condition_ids,
        token_ids_by_condition=token_ids_by_condition,
        feature_snapshots=feature_snapshots,
        asked_at=asked_at,
        settings=settings,
    )
    smart_money_rows = await smart_money_batch_asof(ch, condition_ids, asked_at)
    concentration_rows = await concentration_batch_asof(ch, condition_ids, asked_at)
    resolution_risk_rows = await resolution_risk_batch_asof(ch, condition_ids, asked_at)
    sibling_priors = _sibling_priors_by_condition(
        _sibling_market_rows(all_snaps, all_classifications, mids)
    )
    multi_outcome_contexts = await _multi_outcome_contexts_for_markets(ch, snaps, asked_at)
    long_tail_candidates = await load_long_tail_resolved_markets(ch, asked_at=asked_at)
    regime_row = await regime_label_asof(ch, asked_at)
    # On-chain feature context (PRD §6.3): one trailing-30d batched read at
    # request time builds the per-asset z-scores / pct-deltas dict; per-market
    # population is then a dict lookup keyed by classification.features.asset.
    onchain_context = await load_onchain_feature_context(ch, asked_at)
    # Macro feature context (PRD §5.2 / §6.3): asset-agnostic FRED-derived
    # scalars (rates, curve, CPI YoY). One batched read per request.
    macro_context = await load_macro_feature_context(ch, asked_at)
    # Auto-disable status (M6.3) is per market_type, ~6 distinct values, so we
    # batch-load it once for the whole list rather than per-market.
    distinct_market_types = sorted(
        {
            cls.market_type.value
            for cls in classifications.values()
        }
    )
    disable_status_by_type = await model_status.model_disable_status_batch_asof(
        ch,
        market_types=distinct_market_types,
        asked_at=asked_at,
    )
    # M4.1 social features (PRD §6.3) — single batched query over the
    # full list. Fanning out one query per market would 200x the read
    # amplification on external_events; the batched helper does one
    # `hasAny` query and buckets posts per condition_id in Python.
    social_features_by_cid = await social_features_batch_asof(
        ch,
        condition_ids=[str(snap["condition_id"]) for snap in snaps],
        asked_at=asked_at,
    )

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
                sibling_prior=sibling_priors.get(cid),
                feature_snapshot=feature_snapshots.get(cid),
                smart_money=smart_money_rows.get(cid),
                concentration=concentration_rows.get(cid),
                resolution_risk=resolution_risk_rows.get(cid),
                adversarial_flow=adversarial_rows.get(cid),
                regime_label=regime_row.label if regime_row is not None else None,
                onchain=onchain_context,
                macro=macro_context,
                social=social_features_by_cid.get(cid),
                disable_status=disable_status_by_type.get(
                    classification.market_type.value
                ),
                concentration_threshold=settings.holder_concentration_down_weight_threshold,
                adversarial_flow_blend_floor=settings.adversarial_flow_blend_floor,
                asked_at=asked_at,
            )
            sm_row = smart_money_rows.get(cid)
            conc_row = concentration_rows.get(cid)
            risk_row = resolution_risk_rows.get(cid)
            adversarial_row = adversarial_rows.get(cid)
            tuning = apply_tuning_adjustment(
                profile=tuning_profile,
                context=_tuning_context_for_row(
                    model_prob=model_prob,
                    sibling_prior=sibling_priors.get(cid),
                    smart_money=sm_row,
                    concentration=conc_row,
                    resolution_risk=risk_row,
                    adversarial_flow=adversarial_row,
                ),
                concentration_threshold=settings.holder_concentration_down_weight_threshold,
            )
            model_prob = tuning.tuned_probability
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
                    edge_bps=_edge_bps_with_resolution_risk(
                        model_prob=model_prob,
                        market_mid=yes_mid,
                        resolution_risk=risk_row,
                        suppress_threshold=settings.resolution_risk_edge_suppress_threshold,
                    ),
                    needs_review=classification.needs_review,
                    confidence=classification.confidence,
                    time_to_resolution_s=_time_to_resolution_s(
                        snap.get("end_date"),  # type: ignore[arg-type]
                        asked_at,
                    ),
                    smart_money_consensus=(
                        sm_row.consensus_score if sm_row is not None else None
                    ),
                    smart_money_sample_wallets=(
                        sm_row.sample_wallets if sm_row is not None else None
                    ),
                    smart_money_dominant=(
                        sm_row.dominant_outcome if sm_row is not None else None
                    ),
                    concentration_score=_concentration_score(conc_row),
                    concentration_whale_flag=(
                        conc_row.any_whale_flag if conc_row is not None else None
                    ),
                    resolution_risk_score=(
                        risk_row.risk_score if risk_row is not None else None
                    ),
                    resolution_risk_level=(
                        risk_row.risk_level if risk_row is not None else None
                    ),
                    resolution_risk_flagged=(
                        risk_row.is_flagged if risk_row is not None else False
                    ),
                    adversarial_flow_score=(
                        adversarial_row.score if adversarial_row is not None else None
                    ),
                    adversarial_flow_flagged=(
                        adversarial_row.is_flagged
                        if adversarial_row is not None
                        else False
                    ),
                    thin_book=(
                        adversarial_row.thin_book if adversarial_row is not None else False
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
    distribution_samples: list[tuple[float, float]] | None
    model_source: str
    refinement_source: str | None
    baseline_source: str
    tuning_profile: str | None
    tuning_preset: str | None
    tuning_log_odds_shift: float
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
    # M3 Polymarket-native signal badges.
    smart_money_consensus: float | None
    smart_money_sample_wallets: int | None
    smart_money_dominant: str | None
    concentration_score: float | None
    concentration_whale_flag: bool | None
    concentration_yes_top1_pct: float | None
    concentration_no_top1_pct: float | None
    resolution_risk_score: float | None
    resolution_risk_level: str | None
    resolution_risk_flagged: bool
    resolution_risk_reasons: list[str]
    adversarial_flow_score: float | None
    adversarial_flow_flagged: bool
    adversarial_flow_reasons: list[str]
    thin_book: bool
    # M6.2 regime — null when no daily tag has been written yet.
    regime_label: str | None
    regime_confidence: float | None
    regime_classifier: str | None
    regime_observed_at: datetime | None
    # M6.3 auto-disable — when true, the ensemble is short-circuited and the
    # baseline path serves the displayed probability. Null when this market
    # type has never been touched by the auto-disable rule.
    model_disabled: bool
    model_disabled_reason: str | None
    model_disabled_consecutive_days: int | None
    model_disabled_observed_at: datetime | None
    # Internal: PIT-built EnsembleSample for this market at ``asked_at``.
    # Excluded from the JSON payload (the retrain pipeline reads it directly).
    ensemble_sample: EnsembleSample | None = None


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
        "sibling_implied_prior_logit": "Sibling-implied prior",
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
        "smart_money_consensus": "Smart-money consensus",
        "holder_concentration": "Holder concentration",
        "resolution_risk_score": "Resolution risk",
        "adversarial_flow_score": "Adversarial flow",
        "regime_bull_trend": "Bull-trend regime",
        "regime_bear_trend": "Bear-trend regime",
        "regime_chop": "Chop regime",
        "regime_liquidity_crisis": "Liquidity-crisis regime",
    }
    return labels.get(feature_name, feature_name.replace("_", " "))


def _format_feature_value(feature_name: str, feature_value: float | None) -> str:
    if feature_value is None:
        return "n/a"
    if feature_name in {
        "p_base_logit",
        "market_mid_logit",
        "sibling_implied_prior_logit",
        "realized_vol_24h",
        "resolution_risk_score",
        "adversarial_flow_score",
    }:
        return f"{feature_value * 100:.1f}%"
    if feature_name.startswith("regime_"):
        return "active" if feature_value >= 0.5 else "inactive"
    return f"{feature_value:+.3f}"


def _effective_resolution_risk_multiplier(
    row: ResolutionRiskRow | None,
    *,
    default: float,
) -> float:
    if row is None:
        return default
    return max(default, row.risk_multiplier)


def _edge_bps_with_resolution_risk(
    *,
    model_prob: float | None,
    market_mid: float | None,
    resolution_risk: ResolutionRiskRow | None,
    suppress_threshold: float,
) -> float | None:
    if resolution_risk is not None and resolution_risk.risk_score >= suppress_threshold:
        return None
    return _edge_bps(model_prob, market_mid)


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
    narrator: Any | None = None,
) -> tuple[list[str], list[dict[str, object]]]:
    if registry is None or sample is None or model_source != "ensemble":
        return [], []
    # Prefer the SHAP-style explanation so the "Why not the market's price?"
    # panel and the top-3 drivers show contributions vs. the training-set
    # average (additive SHAP), not raw vs. zero — the former is what the
    # PRD §6.5 calls for. Falls back to the raw path on any registry error.
    explanation = registry.explain_prediction_shap(sample)
    if explanation is None:
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
    top_three = ranked[:3]
    if narrator is None:
        summaries = [_driver_summary(contribution) for contribution in top_three]
    else:
        # Operator wired an LLM narrator. Pass label + sign only — guardrails
        # in narrate_top_drivers reject any output that contains a number or
        # estimation language and fall back per-line.
        from .llm_narrator import narrate_top_drivers

        summaries = narrate_top_drivers(
            drivers=[
                (_attribution_label(contribution.feature_name), contribution.score_contribution)
                for contribution in top_three
            ],
            narrator=narrator,
        )
    return summaries, attributions


async def model_for_market(
    ch: AsyncClient,
    *,
    condition_id: str,
    asked_at: datetime,
    tuning_profile: TuningProfile | None = None,
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
    settings = get_settings()

    classification = await classification_asof(ch, condition_id, asked_at)
    feature_snapshot = await feature_snapshot_asof(ch, condition_id, asked_at)
    smart_money_row = await smart_money_asof(ch, condition_id, asked_at)
    concentration_row = await concentration_asof(ch, condition_id, asked_at)
    regime_row = await regime_label_asof(ch, asked_at)
    onchain_context = await load_onchain_feature_context(ch, asked_at)
    macro_context = await load_macro_feature_context(ch, asked_at)
    # M4.1 structured social features. One per-market query against
    # external_events.event_kind='social' over the trailing 7d window.
    # Today's social rows are Reddit-only; X joins once the M4.1 source
    # decision is made. Wired into the ensemble booster path.
    social_context = await social_features_for_market(
        ch,
        condition_id=condition_id,
        asked_at=asked_at,
    )
    disable_status = (
        await model_disable_status_asof(
            ch,
            market_type=classification.market_type.value,
            asked_at=asked_at,
        )
        if classification is not None
        else None
    )
    resolution_risk_row = await resolution_risk_asof(ch, condition_id, asked_at)
    adversarial_flow_row = await adversarial_flow_asof(
        ch,
        condition_id=condition_id,
        token_id=tok_ids[0] if tok_ids else None,
        feature_snapshot=feature_snapshot,
        asked_at=asked_at,
        settings=settings,
    )
    active_snaps = await _latest_markets(ch, asked_at, limit=500)
    active_condition_ids = [str(s["condition_id"]) for s in active_snaps]
    active_classifications = await classifications_batch_asof(
        ch, active_condition_ids, asked_at
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
    time_to_resolution_s = _time_to_resolution_s(
        snap.get("end_date"),  # type: ignore[arg-type]
        asked_at,
    )
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
        sibling_prior=sibling_priors.get(condition_id),
        feature_snapshot=feature_snapshot,
        smart_money=smart_money_row,
        concentration=concentration_row,
        resolution_risk=resolution_risk_row,
        adversarial_flow=adversarial_flow_row,
        regime_label=regime_row.label if regime_row is not None else None,
        onchain=onchain_context,
        macro=macro_context,
        social=social_context,
        disable_status=disable_status,
        concentration_threshold=settings.holder_concentration_down_weight_threshold,
        adversarial_flow_blend_floor=settings.adversarial_flow_blend_floor,
        asked_at=asked_at,
    )
    tuning = apply_tuning_adjustment(
        profile=tuning_profile,
        context=_tuning_context_for_row(
            model_prob=model_prob,
            sibling_prior=sibling_priors.get(condition_id),
            smart_money=smart_money_row,
            concentration=concentration_row,
            resolution_risk=resolution_risk_row,
            adversarial_flow=adversarial_flow_row,
        ),
        concentration_threshold=settings.holder_concentration_down_weight_threshold,
    )
    model_prob = tuning.tuned_probability
    model_reasons.extend(tuning.reasons)
    ensemble_sample = _ensemble_sample_for_row(
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
        onchain=onchain_context,
        macro=macro_context,
        social=social_context,
        concentration_threshold=settings.holder_concentration_down_weight_threshold,
        asked_at=asked_at,
    )
    driver_summaries, feature_attributions = _feature_attributions_for_sample(
        registry=registry,
        model_source=model_source,
        sample=ensemble_sample,
    )
    edge_bps = _edge_bps_with_resolution_risk(
        model_prob=model_prob,
        market_mid=yes_mid,
        resolution_risk=resolution_risk_row,
        suppress_threshold=settings.resolution_risk_edge_suppress_threshold,
    )
    if edge_bps is None:
        kelly_side, kelly_fraction, kelly_uncapped_fraction = (None, None, None)
    else:
        kelly_side, kelly_fraction, kelly_uncapped_fraction = _kelly_recommendation(
            model_prob=model_prob,
            market_mid=yes_mid,
            fractional_multiplier=settings.fractional_kelly_default,
            cap=settings.kelly_cap_default,
        )
    resolution_risk_multiplier = _effective_resolution_risk_multiplier(
        resolution_risk_row,
        default=settings.resolution_risk_multiplier_default,
    )
    band_lo, band_hi, band_coverage, uncertainty_source = _apply_conformal_interval(
        registry=conformal_registry,
        classification=classification,
        model_prob=model_prob,
        time_to_resolution_s=time_to_resolution_s,
        uncertainty_multiplier=pipeline_result.baseline.uncertainty_multiplier,
        resolution_risk_multiplier=resolution_risk_multiplier,
        regime=regime_row.label if regime_row is not None else None,
    )
    if resolution_risk_row is not None and resolution_risk_row.is_flagged:
        model_reasons.append(
            f"Resolution-risk {resolution_risk_row.risk_level}; uncertainty widened"
        )
    if adversarial_flow_row.is_flagged:
        model_reasons.append("Adversarial-flow risk elevated; ensemble refinement down-weighted")
    if adversarial_flow_row.thin_book:
        model_reasons.append("thin_book: top-of-book depth below threshold")
    if edge_bps is None:
        model_reasons.append("edge suppressed: high resolution risk")
        
    distribution_samples = None
    if classification.market_type in (MarketType.RANGE, MarketType.MULTI_OUTCOME):
        from . import distribution_utils as dist_utils
        if classification.market_type is MarketType.RANGE and band_lo is not None and band_hi is not None:
            # Gaussian approx from bands (assuming ~90% coverage width = 3.29 sigma)
            import math
            import random
            sigma = (band_hi - band_lo) / 3.29
            mu = (band_hi + band_lo) / 2.0
            if sigma > 0:
                samples = [random.gauss(mu, sigma) for _ in range(1000)]
                distribution_samples = dist_utils.percentile_grid(samples, n_points=settings.distribution_n_points)
        elif classification.market_type is MarketType.MULTI_OUTCOME:
            if pipeline_result.baseline.probability is not None:
                # Interpolate softmax output probabilities (since they constitute a true CDF in sorted outcome order)
                multi_context = multi_outcome_contexts.get(condition_id)
                if multi_context and multi_context.raw:
                    samples = sorted(float(v) for v in multi_context.raw)
                    distribution_samples = dist_utils.percentile_grid(samples, n_points=settings.distribution_n_points)

    return MarketModelDetail(
        condition_id=condition_id,
        question=str(snap["question"]),
        market_type=str(classification.market_type.value),
        confidence=classification.confidence,
        needs_review=classification.needs_review,
        classifier=classification.classifier,
        mid=yes_mid,
        model_prob=model_prob,
        distribution_samples=distribution_samples,
        model_source=model_source,
        refinement_source=refinement_source,
        baseline_source=str(pipeline_result.displayed_source.value),
        tuning_profile=tuning_profile.name if tuning_profile is not None else None,
        tuning_preset=tuning_profile.preset if tuning_profile is not None else None,
        tuning_log_odds_shift=tuning.total_log_odds_shift,
        edge_bps=edge_bps,
        uncertainty_multiplier=pipeline_result.baseline.uncertainty_multiplier,
        band_lo=band_lo,
        band_hi=band_hi,
        band_coverage=band_coverage,
        uncertainty_source=uncertainty_source,
        resolution_risk_multiplier=resolution_risk_multiplier,
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
        smart_money_consensus=(
            smart_money_row.consensus_score if smart_money_row is not None else None
        ),
        smart_money_sample_wallets=(
            smart_money_row.sample_wallets if smart_money_row is not None else None
        ),
        smart_money_dominant=(
            smart_money_row.dominant_outcome if smart_money_row is not None else None
        ),
        concentration_score=_concentration_score(concentration_row),
        concentration_whale_flag=(
            concentration_row.any_whale_flag if concentration_row is not None else None
        ),
        concentration_yes_top1_pct=(
            concentration_row.yes_top1_pct if concentration_row is not None else None
        ),
        concentration_no_top1_pct=(
            concentration_row.no_top1_pct if concentration_row is not None else None
        ),
        resolution_risk_score=(
            resolution_risk_row.risk_score if resolution_risk_row is not None else None
        ),
        resolution_risk_level=(
            resolution_risk_row.risk_level if resolution_risk_row is not None else None
        ),
        resolution_risk_flagged=(
            resolution_risk_row.is_flagged if resolution_risk_row is not None else False
        ),
        resolution_risk_reasons=(
            list(resolution_risk_row.reasons) if resolution_risk_row is not None else []
        ),
        adversarial_flow_score=adversarial_flow_row.score,
        adversarial_flow_flagged=adversarial_flow_row.is_flagged,
        adversarial_flow_reasons=list(adversarial_flow_row.reasons),
        thin_book=adversarial_flow_row.thin_book,
        regime_label=regime_row.label if regime_row is not None else None,
        regime_confidence=regime_row.confidence if regime_row is not None else None,
        regime_classifier=regime_row.classifier if regime_row is not None else None,
        regime_observed_at=regime_row.observed_at if regime_row is not None else None,
        model_disabled=disable_status.is_disabled if disable_status is not None else False,
        model_disabled_reason=disable_status.reason if disable_status is not None else None,
        model_disabled_consecutive_days=(
            disable_status.consecutive_days if disable_status is not None else None
        ),
        model_disabled_observed_at=(
            disable_status.observed_at if disable_status is not None else None
        ),
        ensemble_sample=ensemble_sample,
    )


async def history_for_market(
    ch: AsyncClient,
    *,
    condition_id: str,
    asked_at: datetime,
    window_hours: int = 24 * 7,
    max_points: int = 96,
    tuning_profile: TuningProfile | None = None,
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
        detail = await model_for_market(
            ch,
            condition_id=condition_id,
            asked_at=row.event_time,
            tuning_profile=tuning_profile,
        )
        history.append(
            MarketHistoryPoint(
                event_time=row.event_time,
                market_mid=row.mid,
                model_prob=detail.model_prob if detail is not None else None,
            )
        )
    return history
