"""FastAPI app entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import asof as asof_q
from . import backtest_report as backtest_report_q
from . import clob_credentials as clob_credentials_q
from . import concentration as concentration_q
from . import dp_aggregates as dp_aggregates_q
from . import drift_report as drift_report_q
from . import event_time as event_time_q
from . import external_events as external_events_q
from . import features as features_q
from . import journal as journal_q
from . import journal_autosync as journal_autosync_q
from . import markets as markets_q
from . import onchain_metrics as onchain_metrics_q
from . import polymarket_account as polymarket_account_q
from . import source_health as source_health_q
from . import privacy_prefs as privacy_prefs_q
from . import push_prefs as push_prefs_q
from . import regime as regime_q
from . import signals as signals_q
from . import smart_money as smart_money_q
from . import tuning as tuning_q
from .clickhouse import get_async_client
from .postgres import get_async_pool
from .settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.ch = await get_async_client()
    app.state.pg = await get_async_pool()
    app.state.journal_auto_sync = journal_autosync_q.UserJournalAutoSyncService(
        pool=app.state.pg,
        ch=app.state.ch,
        settings=get_settings(),
    )
    await app.state.journal_auto_sync.refresh()
    try:
        yield
    finally:
        await app.state.journal_auto_sync.stop()
        await app.state.ch.close()
        await app.state.pg.close()


app = FastAPI(title="PolyPredictor API", version="0.0.1", lifespan=lifespan)


def get_ch() -> Any:
    return app.state.ch


def get_pg() -> Any:
    return app.state.pg


class JournalCallCreateRequest(BaseModel):
    condition_id: str
    outcome: str = Field(pattern="^(YES|NO)$")
    size_usdc: float = Field(gt=0)


class PushPreferencesRequest(BaseModel):
    email_enabled: bool = False
    email_to: str | None = None
    webhook_enabled: bool = False
    webhook_url: str | None = None
    min_severity: float = Field(default=1.0, ge=0)
    event_types: list[str] = Field(default_factory=list)
    condition_ids: list[str] = Field(default_factory=list)


class PrivacyPreferencesRequest(BaseModel):
    cross_user_learning_opt_in: bool = False


class ClobCredentialRequest(BaseModel):
    api_key: str | None = None
    api_secret: str | None = None
    passphrase: str | None = None
    proxy_wallet: str | None = None


class PolymarketAddressRequest(BaseModel):
    proxy_wallet: str | None = None


class TuningProfileRequest(BaseModel):
    preset: str = Field(pattern="^(conservative|balanced|aggressive|custom)$")
    log_odds_shifts: dict[str, float] = Field(default_factory=dict)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    s = get_settings()
    return {"status": "ok", "env": s.app_env}


@app.get("/v1/markets/{condition_id}/asof")
async def market_asof(
    condition_id: str,
    at: datetime,
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    """Return the market row as it was known at ``at`` (UTC).

    This is the canonical PIT read path — clients MUST use it for any
    historical feature assembly.
    """
    row = await asof_q.latest_market_snapshot_asof(ch, condition_id, at)
    if row is None:
        raise HTTPException(status_code=404, detail="market not known at that time")
    return {
        "condition_id": row.condition_id,
        "question": row.question,
        "active": row.active,
        "closed": row.closed,
        "volume_usdc": row.volume_usdc,
        "liquidity_usdc": row.liquidity_usdc,
        "open_interest_usdc": row.open_interest_usdc,
        "end_date": row.end_date.isoformat() if row.end_date else None,
        "token_ids": row.token_ids,
        "event_time": row.event_time.isoformat(),
        "observed_at": row.observed_at.isoformat(),
    }


@app.get("/v1/quotes/{token_id}/asof")
async def quote_asof(
    token_id: str,
    at: datetime,
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    row = await asof_q.latest_quote_asof(ch, token_id, at)
    if row is None:
        raise HTTPException(status_code=404, detail="no quote known at that time")
    return {
        "token_id": row.token_id,
        "condition_id": row.condition_id,
        "mid": row.mid,
        "best_bid": row.best_bid,
        "best_ask": row.best_ask,
        "spread": row.spread,
        "event_time": row.event_time.isoformat(),
        "observed_at": row.observed_at.isoformat(),
    }


@app.get("/v1/features/{condition_id}/asof")
async def market_features_asof(
    condition_id: str,
    at: datetime,
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    row = await features_q.feature_snapshot_asof(ch, condition_id, at)
    if row is None:
        raise HTTPException(status_code=404, detail="no features known at that time")
    return {
        "condition_id": row.condition_id,
        "token_id": row.token_id,
        "mid": row.mid,
        "spread": row.spread,
        "book_imbalance_1pct": row.book_imbalance_1pct,
        "book_imbalance_5pct": row.book_imbalance_5pct,
        "momentum_1h": row.momentum_1h,
        "momentum_24h": row.momentum_24h,
        "momentum_7d": row.momentum_7d,
        "realized_vol_24h": row.realized_vol_24h,
        "informed_taker_flow_24h": row.informed_taker_flow_24h,
        "passive_maker_flow_24h": row.passive_maker_flow_24h,
        "decayed_directional_flow_24h": row.decayed_directional_flow_24h,
        "event_time": row.event_time.isoformat(),
        "observed_at": row.observed_at.isoformat(),
    }


@app.get("/v1/event-time/{condition_id}/asof")
async def event_time_asof(
    condition_id: str,
    at: datetime,
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    context = await event_time_q.event_time_context_asof(ch, condition_id, at)
    return {
        "condition_id": context.condition_id,
        "in_event_window_24h": context.in_event_window_24h,
        "event_asset": context.event_asset,
        "hours_to_next_event": context.hours_to_next_event,
        "hours_since_previous_event": context.hours_since_previous_event,
        "pre_event_atm_iv_now": context.pre_event_atm_iv_now,
        "pre_event_atm_iv_24h_ago": context.pre_event_atm_iv_24h_ago,
        "pre_event_iv_runup_24h": context.pre_event_iv_runup_24h,
        "post_event_atm_iv_pre_1h": context.post_event_atm_iv_pre_1h,
        "post_event_atm_iv_24h": context.post_event_atm_iv_24h,
        "post_event_iv_crush_pre_1h_to_24h": context.post_event_iv_crush_pre_1h_to_24h,
        "post_event_mid_1h": context.post_event_mid_1h,
        "post_event_mid_24h": context.post_event_mid_24h,
        "post_event_drift_1h_to_24h": context.post_event_drift_1h_to_24h,
        "next_event": (
            {
                "event_kind": context.next_event.event_kind,
                "source": context.next_event.source,
                "source_id": context.next_event.source_id,
                "title": context.next_event.title,
                "release_time": (
                    context.next_event.release_time.isoformat()
                    if context.next_event.release_time is not None
                    else None
                ),
                "consensus_value": context.next_event.consensus_value,
                "actual_value": context.next_event.actual_value,
                "surprise_value": context.next_event.surprise_value,
                "value_unit": context.next_event.value_unit,
                "event_time": context.next_event.event_time.isoformat(),
                "observed_at": context.next_event.observed_at.isoformat(),
            }
            if context.next_event is not None
            else None
        ),
        "previous_event": (
            {
                "event_kind": context.previous_event.event_kind,
                "source": context.previous_event.source,
                "source_id": context.previous_event.source_id,
                "title": context.previous_event.title,
                "release_time": (
                    context.previous_event.release_time.isoformat()
                    if context.previous_event.release_time is not None
                    else None
                ),
                "consensus_value": context.previous_event.consensus_value,
                "actual_value": context.previous_event.actual_value,
                "surprise_value": context.previous_event.surprise_value,
                "value_unit": context.previous_event.value_unit,
                "event_time": context.previous_event.event_time.isoformat(),
                "observed_at": context.previous_event.observed_at.isoformat(),
            }
            if context.previous_event is not None
            else None
        ),
    }


@app.get("/v1/markets")
async def markets_list(
    limit: int = Query(200, ge=1, le=500),
    ch: AsyncClient = Depends(get_ch),
    pg: Any = Depends(get_pg),
) -> list[dict[str, Any]]:
    """Dashboard list — one row per market with model probability + edge.

    Reads the point-in-time stack (snapshot + quote + classification) at
    wall-clock now, runs each market through the baseline pipeline, and
    exposes the displayed probability + the source badge.
    """
    asked_at = datetime.now(tz=UTC)
    settings = get_settings()
    tuning_profile = await tuning_q.get_active_profile(pool=pg, settings=settings)
    rows = await markets_q.list_markets(
        ch,
        asked_at=asked_at,
        limit=limit,
        tuning_profile=tuning_profile,
    )
    return [
        {
            "condition_id": r.condition_id,
            "question": r.question,
            "slug": r.slug,
            "category": r.category,
            "tags": r.tags,
            "volume_usdc": r.volume_usdc,
            "liquidity_usdc": r.liquidity_usdc,
            "mid": r.mid,
            "model_prob": r.model_prob,
            "model_source": r.model_source,
            "refinement_source": r.refinement_source,
            "baseline_source": r.baseline_source,
            "market_type": r.market_type,
            "confidence": r.confidence,
            "needs_review": r.needs_review,
            "edge_bps": r.edge_bps,
            "time_to_resolution_s": r.time_to_resolution_s,
            "smart_money_consensus": r.smart_money_consensus,
            "smart_money_sample_wallets": r.smart_money_sample_wallets,
            "smart_money_dominant": r.smart_money_dominant,
            "concentration_score": r.concentration_score,
            "concentration_whale_flag": r.concentration_whale_flag,
            "resolution_risk_score": r.resolution_risk_score,
            "resolution_risk_level": r.resolution_risk_level,
            "resolution_risk_flagged": r.resolution_risk_flagged,
            "adversarial_flow_score": r.adversarial_flow_score,
            "adversarial_flow_flagged": r.adversarial_flow_flagged,
            "thin_book": r.thin_book,
        }
        for r in rows
    ]


@app.get("/v1/drift-monitor")
async def drift_monitor_snapshot(
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    asked_at = datetime.now(tz=UTC)
    snapshot = await drift_report_q.latest_drift_snapshot_asof(ch, asked_at=asked_at)
    if snapshot is None:
        return {
            "observed_at": None,
            "model_metrics": [],
            "feature_metrics": [],
        }
    return {
        "observed_at": snapshot.observed_at.isoformat(),
        "model_metrics": [
            {
                "window_label": row.window_label,
                "market_type": row.market_type,
                "ttr_bucket": row.ttr_bucket,
                "regime": row.regime,
                "sample_count": row.sample_count,
                "brier": row.brier,
                "ece": row.ece,
                "coverage": row.coverage,
                "avg_predicted": row.avg_predicted,
                "avg_outcome": row.avg_outcome,
                "baseline_brier": row.baseline_brier,
                "brier_skill": row.brier_skill,
                "observed_at": row.observed_at.isoformat(),
            }
            for row in snapshot.model_metrics
        ],
        "feature_metrics": [
            {
                "feature_name": row.feature_name,
                "reference_count": row.reference_count,
                "current_count": row.current_count,
                "psi": row.psi,
                "kl_divergence": row.kl_divergence,
                "psi_threshold": row.psi_threshold,
                "is_alert": row.is_alert,
                "observed_at": row.observed_at.isoformat(),
            }
            for row in snapshot.feature_metrics
        ],
    }


@app.get("/v1/backtest/walk-forward")
async def backtest_walk_forward_report(
    lookback_days: int = Query(90, ge=1, le=365),
    horizon_hours: int = Query(24, ge=1, le=24 * 14),
    limit: int = Query(500, ge=1, le=2000),
    ch: AsyncClient = Depends(get_ch),
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    asked_at = datetime.now(tz=UTC)
    settings = get_settings()
    tuning_profile = await tuning_q.get_active_profile(pool=pg, settings=settings)
    report = await backtest_report_q.build_backtest_ui_report(
        ch,
        asked_at=asked_at,
        lookback_days=lookback_days,
        horizon_hours=horizon_hours,
        limit=limit,
        tuning_profile=tuning_profile,
    )
    return {
        "lookback_days": report.lookback_days,
        "horizon_hours": report.horizon_hours,
        "total_samples": report.total_samples,
        "calibration_points": [
            {
                "bucket_mid": point.bucket_mid,
                "avg_predicted": point.avg_predicted,
                "hit_rate": point.hit_rate,
                "count": point.count,
            }
            for point in report.calibration_points
        ],
        "rows": [
            {
                "condition_id": row.condition_id,
                "market_type": row.market_type,
                "regime": row.regime,
                "asked_at": row.asked_at.isoformat(),
                "resolved_at": row.resolved_at.isoformat(),
                "predicted_prob": row.predicted_prob,
                "outcome": row.outcome,
                "brier_contribution": row.brier_contribution,
                "time_to_resolution_s": row.time_to_resolution_s,
                "band_lo": row.band_lo,
                "band_hi": row.band_hi,
                "band_hit": row.band_hit,
            }
            for row in report.rows
        ],
        "corpus": {
            "total_resolved_markets": report.corpus.total_resolved_markets,
            "binary_markets": report.corpus.binary_markets,
            "invalid_markets": report.corpus.invalid_markets,
            "disputed_markets": report.corpus.disputed_markets,
            "low_volume_markets": report.corpus.low_volume_markets,
            "missing_snapshot_markets": report.corpus.missing_snapshot_markets,
        },
        "tuning_comparison": (
            {
                "profile_name": report.tuning_comparison.profile_name,
                "profile_preset": report.tuning_comparison.profile_preset,
                "total_samples": report.tuning_comparison.total_samples,
                "default_brier": report.tuning_comparison.default_brier,
                "tuned_brier": report.tuning_comparison.tuned_brier,
                "brier_delta": report.tuning_comparison.brier_delta,
                "default_calibration_points": [
                    {
                        "bucket_mid": point.bucket_mid,
                        "avg_predicted": point.avg_predicted,
                        "hit_rate": point.hit_rate,
                        "count": point.count,
                    }
                    for point in report.tuning_comparison.default_calibration_points
                ],
                "tuned_calibration_points": [
                    {
                        "bucket_mid": point.bucket_mid,
                        "avg_predicted": point.avg_predicted,
                        "hit_rate": point.hit_rate,
                        "count": point.count,
                    }
                    for point in report.tuning_comparison.tuned_calibration_points
                ],
            }
            if report.tuning_comparison is not None
            else None
        ),
    }


@app.get("/v1/markets/{condition_id}/model")
async def market_model(
    condition_id: str,
    ch: AsyncClient = Depends(get_ch),
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    """Model-probability payload for the market detail view.

    Returns the baseline + classifier provenance so the UI can render both
    the big-number probability and the "Why this number?" trust badge.
    """
    asked_at = datetime.now(tz=UTC)
    settings = get_settings()
    tuning_profile = await tuning_q.get_active_profile(pool=pg, settings=settings)
    detail = await markets_q.model_for_market(
        ch,
        condition_id=condition_id,
        asked_at=asked_at,
        tuning_profile=tuning_profile,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="market not known")
    return {
        "condition_id": detail.condition_id,
        "question": detail.question,
        "market_type": detail.market_type,
        "confidence": detail.confidence,
        "needs_review": detail.needs_review,
        "classifier": detail.classifier,
        "mid": detail.mid,
        "model_prob": detail.model_prob,
        "model_source": detail.model_source,
        "refinement_source": detail.refinement_source,
        "baseline_source": detail.baseline_source,
        "tuning_profile": detail.tuning_profile,
        "tuning_preset": detail.tuning_preset,
        "tuning_log_odds_shift": detail.tuning_log_odds_shift,
        "edge_bps": detail.edge_bps,
        "uncertainty_multiplier": detail.uncertainty_multiplier,
        "band_lo": detail.band_lo,
        "band_hi": detail.band_hi,
        "band_coverage": detail.band_coverage,
        "uncertainty_source": detail.uncertainty_source,
        "resolution_risk_multiplier": detail.resolution_risk_multiplier,
        "kelly_side": detail.kelly_side,
        "kelly_fraction": detail.kelly_fraction,
        "kelly_uncapped_fraction": detail.kelly_uncapped_fraction,
        "kelly_fractional_multiplier": detail.kelly_fractional_multiplier,
        "kelly_cap": detail.kelly_cap,
        "reasons": detail.reasons,
        "model_reasons": detail.model_reasons,
        "classifier_reasons": detail.classifier_reasons,
        "driver_summaries": detail.driver_summaries,
        "feature_attributions": detail.feature_attributions,
        "smart_money_consensus": detail.smart_money_consensus,
        "smart_money_sample_wallets": detail.smart_money_sample_wallets,
        "smart_money_dominant": detail.smart_money_dominant,
        "concentration_score": detail.concentration_score,
        "concentration_whale_flag": detail.concentration_whale_flag,
        "concentration_yes_top1_pct": detail.concentration_yes_top1_pct,
        "concentration_no_top1_pct": detail.concentration_no_top1_pct,
        "resolution_risk_score": detail.resolution_risk_score,
        "resolution_risk_level": detail.resolution_risk_level,
        "resolution_risk_flagged": detail.resolution_risk_flagged,
        "resolution_risk_reasons": detail.resolution_risk_reasons,
        "adversarial_flow_score": detail.adversarial_flow_score,
        "adversarial_flow_flagged": detail.adversarial_flow_flagged,
        "adversarial_flow_reasons": detail.adversarial_flow_reasons,
        "thin_book": detail.thin_book,
        "regime_label": detail.regime_label,
        "regime_confidence": detail.regime_confidence,
        "regime_classifier": detail.regime_classifier,
        "regime_observed_at": (
            detail.regime_observed_at.isoformat()
            if detail.regime_observed_at is not None
            else None
        ),
        "model_disabled": detail.model_disabled,
        "model_disabled_reason": detail.model_disabled_reason,
        "model_disabled_consecutive_days": detail.model_disabled_consecutive_days,
        "model_disabled_observed_at": (
            detail.model_disabled_observed_at.isoformat()
            if detail.model_disabled_observed_at is not None
            else None
        ),
    }


@app.get("/v1/regime")
async def regime_now(
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    """Latest live regime label (M6.2). 404 when nothing has been tagged yet."""
    asked_at = datetime.now(tz=UTC)
    row = await regime_q.regime_label_asof(ch, asked_at)
    if row is None:
        raise HTTPException(status_code=404, detail="no regime tagged yet")
    return {
        "regime_date": row.regime_date.isoformat(),
        "label": row.label,
        "confidence": row.confidence,
        "classifier": row.classifier,
        "btc_realized_vol_24h": row.btc_realized_vol_24h,
        "btc_realized_vol_7d": row.btc_realized_vol_7d,
        "btc_momentum_7d": row.btc_momentum_7d,
        "btc_ndx_correlation_30d": row.btc_ndx_correlation_30d,
        "stablecoin_supply_delta_7d": row.stablecoin_supply_delta_7d,
        "reasons": row.reasons,
        "event_time": row.event_time.isoformat(),
        "observed_at": row.observed_at.isoformat(),
    }


@app.get("/v1/signals")
async def signal_feed(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    limit: int = Query(100, ge=1, le=500),
    event_type: list[str] | None = Query(default=None),
    condition_id: str | None = Query(default=None),
    min_severity: float | None = Query(default=None, ge=0),
    ch: AsyncClient = Depends(get_ch),
) -> list[dict[str, Any]]:
    """Unified signal feed — whale flow today, arb + large-print in M4+.

    Filters:
    * ``event_type`` — repeat to include multiple types (e.g. ``?event_type=whale_open&event_type=whale_resize``)
    * ``condition_id`` — scope to a single market (used by the detail page)
    * ``min_severity`` — floor on normalized signal weight for feed triage
    """
    asked_at = datetime.now(tz=UTC)
    events = await signals_q.list_signals(
        ch,
        asked_at=asked_at,
        lookback_hours=lookback_hours,
        limit=limit,
        event_types=event_type,
        condition_id=condition_id,
        min_severity=min_severity,
    )
    return [
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "condition_id": event.condition_id,
            "severity": event.severity,
            "actor": event.actor,
            "direction": event.direction,
            "size_delta_usdc": event.size_delta_usdc,
            "prior_size_usdc": event.prior_size_usdc,
            "current_size_usdc": event.current_size_usdc,
            "payload": event.payload,
            "event_time": event.event_time.isoformat(),
            "observed_at": event.observed_at.isoformat(),
        }
        for event in events
    ]


@app.get("/v1/external-events")
async def external_event_feed(
    lookback_hours: int = Query(72, ge=1, le=24 * 30),
    limit: int = Query(100, ge=1, le=500),
    event_kind: list[str] | None = Query(default=None),
    source: list[str] | None = Query(default=None),
    condition_id: str | None = Query(default=None),
    include_scheduled: bool = Query(default=False),
    ch: AsyncClient = Depends(get_ch),
) -> list[dict[str, Any]]:
    asked_at = datetime.now(tz=UTC)
    events = await external_events_q.list_external_events(
        ch,
        asked_at=asked_at,
        lookback_hours=lookback_hours,
        limit=limit,
        event_kinds=event_kind,
        sources=source,
        condition_id=condition_id,
        include_scheduled=include_scheduled,
    )
    return [
        {
            "event_kind": event.event_kind,
            "source": event.source,
            "source_uri": event.source_uri,
            "source_id": event.source_id,
            "related_markets": event.related_markets,
            "author": event.author,
            "title": event.title,
            "body": event.body,
            "url": event.url,
            "metadata": event.metadata,
            "event_time": event.event_time.isoformat(),
            "observed_at": event.observed_at.isoformat(),
            "age_seconds": event.age_seconds,
            "decay_half_life_minutes": event.decay_half_life_minutes,
            "freshness_weight": event.freshness_weight,
        }
        for event in events
    ]


@app.get("/v1/source-health")
async def source_health(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    source: list[str] | None = Query(default=None),
    include_timeseries: bool = Query(default=False),
    bucket_minutes: int = Query(60, ge=5, le=24 * 60),
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    asked_at = datetime.now(tz=UTC)
    summaries = await source_health_q.source_health_summary_asof(
        ch,
        asked_at=asked_at,
        lookback_hours=lookback_hours,
        sources=source,
    )
    payload: dict[str, Any] = {
        "asked_at": asked_at.isoformat(),
        "lookback_hours": lookback_hours,
        "sources": [
            {
                "source": s.source,
                "total_requests": s.total_requests,
                "ok_count": s.ok_count,
                "error_count": s.error_count,
                "rate_limited_count": s.rate_limited_count,
                "timeout_count": s.timeout_count,
                "failure_rate": s.failure_rate,
                "rate_limited_rate": s.rate_limited_rate,
                "p50_latency_ms": s.p50_latency_ms,
                "p95_latency_ms": s.p95_latency_ms,
                "last_observed_at": (
                    s.last_observed_at.isoformat() if s.last_observed_at else None
                ),
            }
            for s in summaries
        ],
    }
    if include_timeseries:
        buckets = await source_health_q.source_health_timeseries(
            ch,
            asked_at=asked_at,
            lookback_hours=lookback_hours,
            bucket_minutes=bucket_minutes,
            sources=source,
        )
        payload["timeseries"] = [
            {
                "source": b.source,
                "bucket_start": b.bucket_start.isoformat(),
                "total_requests": b.total_requests,
                "failure_count": b.failure_count,
                "rate_limited_count": b.rate_limited_count,
            }
            for b in buckets
        ]
    return payload


@app.get("/v1/onchain-metrics")
async def onchain_metrics_feed(
    lookback_hours: int = Query(7 * 24, ge=1, le=24 * 90),
    limit: int = Query(200, ge=1, le=1000),
    source: list[str] | None = Query(default=None),
    asset: list[str] | None = Query(default=None),
    category: list[str] | None = Query(default=None),
    metric_id: list[str] | None = Query(default=None),
    ch: AsyncClient = Depends(get_ch),
) -> list[dict[str, Any]]:
    asked_at = datetime.now(tz=UTC)
    rows = await onchain_metrics_q.list_onchain_metrics(
        ch,
        asked_at=asked_at,
        lookback_hours=lookback_hours,
        limit=limit,
        sources=source,
        assets=asset,
        categories=category,
        metric_ids=metric_id,
    )
    return [
        {
            "source": row.source,
            "metric_id": row.metric_id,
            "metric_title": row.metric_title,
            "asset": row.asset,
            "chain": row.chain,
            "category": row.category,
            "period_date": row.period_date.date().isoformat(),
            "value": row.value,
            "units": row.units,
            "event_time": row.event_time.isoformat(),
            "observed_at": row.observed_at.isoformat(),
        }
        for row in rows
    ]


@app.get("/v1/push-preferences")
async def push_preferences(
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    prefs = await push_prefs_q.get_preferences(pool=pg, settings=settings)
    return {
        "email_enabled": prefs.email_enabled,
        "email_to": prefs.email_to,
        "webhook_enabled": prefs.webhook_enabled,
        "webhook_url": prefs.webhook_url,
        "min_severity": prefs.min_severity,
        "event_types": prefs.event_types,
        "condition_ids": prefs.condition_ids,
        "updated_at": prefs.updated_at.isoformat() if prefs.updated_at else None,
    }


@app.get("/v1/privacy-preferences")
async def privacy_preferences(
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    prefs = await privacy_prefs_q.get_preferences(pool=pg, settings=settings)
    return {
        "cross_user_learning_opt_in": prefs.cross_user_learning_opt_in,
        "updated_at": prefs.updated_at.isoformat() if prefs.updated_at else None,
    }


@app.get("/v1/privacy/dp-aggregates")
async def privacy_dp_aggregates(
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    rows = await dp_aggregates_q.latest_dp_aggregates(pool=pg)
    return {
        "rows": [
            {
                "cohort_kind": row.cohort_kind,
                "bucket_label": row.bucket_label,
                "source_user_count": row.source_user_count,
                "raw_call_count": row.raw_call_count,
                "noisy_call_count": row.noisy_call_count,
                "raw_hit_rate": row.raw_hit_rate,
                "noisy_hit_rate": row.noisy_hit_rate,
                "epsilon": row.epsilon,
                "min_users": row.min_users,
                "min_calls": row.min_calls,
                "lookback_start": row.lookback_start.isoformat(),
                "lookback_end": row.lookback_end.isoformat(),
                "observed_at": row.observed_at.isoformat(),
            }
            for row in rows
        ]
    }


@app.put("/v1/privacy-preferences")
async def update_privacy_preferences(
    payload: PrivacyPreferencesRequest,
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    prefs = await privacy_prefs_q.update_preferences(
        pool=pg,
        settings=settings,
        payload=privacy_prefs_q.PrivacyPreferencesInput(
            cross_user_learning_opt_in=payload.cross_user_learning_opt_in,
        ),
    )
    return {
        "cross_user_learning_opt_in": prefs.cross_user_learning_opt_in,
        "updated_at": prefs.updated_at.isoformat() if prefs.updated_at else None,
    }


@app.put("/v1/push-preferences")
async def update_push_preferences(
    payload: PushPreferencesRequest,
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        prefs = await push_prefs_q.update_preferences(
            pool=pg,
            settings=settings,
            payload=push_prefs_q.PushPreferencesInput(
                email_enabled=payload.email_enabled,
                email_to=payload.email_to,
                webhook_enabled=payload.webhook_enabled,
                webhook_url=payload.webhook_url,
                min_severity=payload.min_severity,
                event_types=payload.event_types,
                condition_ids=payload.condition_ids,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "email_enabled": prefs.email_enabled,
        "email_to": prefs.email_to,
        "webhook_enabled": prefs.webhook_enabled,
        "webhook_url": prefs.webhook_url,
        "min_severity": prefs.min_severity,
        "event_types": prefs.event_types,
        "condition_ids": prefs.condition_ids,
        "updated_at": prefs.updated_at.isoformat() if prefs.updated_at else None,
    }


@app.get("/v1/polymarket-address")
async def polymarket_address(
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    link = await polymarket_account_q.get_linked_address(
        pool=pg,
        settings=settings,
    )
    return {
        "proxy_wallet": link.proxy_wallet,
        "verified_at": link.verified_at.isoformat() if link.verified_at else None,
        "summary": (
            {
                "proxy_wallet": link.summary.proxy_wallet,
                "verified_at": (
                    link.summary.verified_at.isoformat()
                    if link.summary.verified_at is not None
                    else None
                ),
                "open_positions": link.summary.open_positions,
                "redeemable_positions": link.summary.redeemable_positions,
                "total_position_value_usdc": link.summary.total_position_value_usdc,
                "total_earnings_usdc": link.summary.total_earnings_usdc,
                "recent_trades": [
                    {
                        "trade_id": trade.trade_id,
                        "condition_id": trade.condition_id,
                        "outcome": trade.outcome,
                        "side": trade.side,
                        "price": trade.price,
                        "size": trade.size,
                        "timestamp": trade.timestamp,
                    }
                    for trade in link.summary.recent_trades
                ],
            }
            if link.summary is not None
            else None
        ),
    }


@app.get("/v1/polymarket-clob-credentials")
async def polymarket_clob_credentials(
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    status = await clob_credentials_q.get_credential_status(pool=pg, settings=settings)
    return {
        "configured": status.configured,
        "proxy_wallet": status.proxy_wallet,
        "created_at": status.created_at.isoformat() if status.created_at else None,
        "rotated_at": status.rotated_at.isoformat() if status.rotated_at else None,
    }


@app.put("/v1/polymarket-clob-credentials")
async def update_polymarket_clob_credentials(
    payload: ClobCredentialRequest,
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        status = await clob_credentials_q.update_credentials(
            pool=pg,
            settings=settings,
            payload=clob_credentials_q.ClobCredentialInput(
                api_key=payload.api_key,
                api_secret=payload.api_secret,
                passphrase=payload.passphrase,
                proxy_wallet=payload.proxy_wallet,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await app.state.journal_auto_sync.refresh()
    return {
        "configured": status.configured,
        "proxy_wallet": status.proxy_wallet,
        "created_at": status.created_at.isoformat() if status.created_at else None,
        "rotated_at": status.rotated_at.isoformat() if status.rotated_at else None,
    }


@app.put("/v1/polymarket-address")
async def update_polymarket_address(
    payload: PolymarketAddressRequest,
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        link = await polymarket_account_q.update_linked_address(
            pool=pg,
            settings=settings,
            payload=polymarket_account_q.PolymarketAddressInput(
                proxy_wallet=payload.proxy_wallet,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "proxy_wallet": link.proxy_wallet,
        "verified_at": link.verified_at.isoformat() if link.verified_at else None,
        "summary": (
            {
                "proxy_wallet": link.summary.proxy_wallet,
                "verified_at": (
                    link.summary.verified_at.isoformat()
                    if link.summary.verified_at is not None
                    else None
                ),
                "open_positions": link.summary.open_positions,
                "redeemable_positions": link.summary.redeemable_positions,
                "total_position_value_usdc": link.summary.total_position_value_usdc,
                "total_earnings_usdc": link.summary.total_earnings_usdc,
                "recent_trades": [
                    {
                        "trade_id": trade.trade_id,
                        "condition_id": trade.condition_id,
                        "outcome": trade.outcome,
                        "side": trade.side,
                        "price": trade.price,
                        "size": trade.size,
                        "timestamp": trade.timestamp,
                    }
                    for trade in link.summary.recent_trades
                ],
            }
            if link.summary is not None
            else None
        ),
    }


@app.get("/v1/markets/{condition_id}/concentration")
async def market_concentration(
    condition_id: str,
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    """Holder-concentration summary (Gini + whale flag) for the detail badge."""
    asked_at = datetime.now(tz=UTC)
    row = await concentration_q.concentration_asof(ch, condition_id, asked_at)
    if row is None:
        raise HTTPException(
            status_code=404, detail="no concentration snapshot known for that market"
        )
    return {
        "condition_id": row.condition_id,
        "yes_gini": row.yes_gini,
        "no_gini": row.no_gini,
        "max_gini": row.max_gini,
        "yes_top1_pct": row.yes_top1_pct,
        "no_top1_pct": row.no_top1_pct,
        "yes_top5_pct": row.yes_top5_pct,
        "no_top5_pct": row.no_top5_pct,
        "yes_whale_flag": row.yes_whale_flag,
        "no_whale_flag": row.no_whale_flag,
        "any_whale_flag": row.any_whale_flag,
        "yes_holders_count": row.yes_holders_count,
        "no_holders_count": row.no_holders_count,
        "observed_at": row.observed_at.isoformat(),
    }


@app.get("/v1/markets/{condition_id}/smart-money")
async def market_smart_money(
    condition_id: str,
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    """Smart-money consensus + 24h directional delta for a single market.

    Returns the latest qualified-wallet rollup (YES/NO wallet counts, size,
    avg entry, USDC notional, consensus score) plus the directional delta
    against the snapshot 24h earlier.
    """
    asked_at = datetime.now(tz=UTC)
    view = await smart_money_q.smart_money_view(ch, condition_id, asked_at)
    if view is None:
        raise HTTPException(
            status_code=404, detail="no smart-money rollup known for that market"
        )

    def _row_dict(row: smart_money_q.SmartMoneyPerMarket) -> dict[str, Any]:
        return {
            "yes_wallets": row.yes_wallets,
            "no_wallets": row.no_wallets,
            "yes_size": row.yes_size,
            "no_size": row.no_size,
            "yes_size_usdc": row.yes_size_usdc,
            "no_size_usdc": row.no_size_usdc,
            "yes_avg_entry": row.yes_avg_entry,
            "no_avg_entry": row.no_avg_entry,
            "net_size_usdc": row.net_size_usdc,
            "consensus_score": row.consensus_score,
            "sample_wallets": row.sample_wallets,
            "dominant_outcome": row.dominant_outcome,
            "observed_at": row.observed_at.isoformat(),
        }

    return {
        "condition_id": view.latest.condition_id,
        "latest": _row_dict(view.latest),
        "prior_24h": _row_dict(view.prior_24h) if view.prior_24h is not None else None,
        "directional_delta_usdc": view.directional_delta_usdc,
        "consensus_delta_24h": view.consensus_delta_24h,
    }


@app.get("/v1/markets/{condition_id}/history")
async def market_history(
    condition_id: str,
    window_hours: int = Query(24 * 7, ge=1, le=24 * 30),
    max_points: int = Query(96, ge=8, le=240),
    ch: AsyncClient = Depends(get_ch),
    pg: Any = Depends(get_pg),
) -> list[dict[str, Any]]:
    asked_at = datetime.now(tz=UTC)
    settings = get_settings()
    tuning_profile = await tuning_q.get_active_profile(pool=pg, settings=settings)
    rows = await markets_q.history_for_market(
        ch,
        condition_id=condition_id,
        asked_at=asked_at,
        window_hours=window_hours,
        max_points=max_points,
        tuning_profile=tuning_profile,
    )
    return [
        {
            "event_time": row.event_time.isoformat(),
            "market_mid": row.market_mid,
            "model_prob": row.model_prob,
        }
        for row in rows
    ]


@app.get("/v1/journal/calls")
async def journal_calls(
    ch: AsyncClient = Depends(get_ch),
    pg: Any = Depends(get_pg),
) -> list[dict[str, Any]]:
    settings = get_settings()
    rows = await journal_q.list_calls(pool=pg, ch=ch, settings=settings)
    return [
        {
            "id": row.id,
            "condition_id": row.condition_id,
            "outcome": row.outcome,
            "side": row.side,
            "size_usdc": row.size_usdc,
            "entry_price": row.entry_price,
            "model_prob_at_call": row.model_prob_at_call,
            "model_band_lo_at_call": row.model_band_lo_at_call,
            "model_band_hi_at_call": row.model_band_hi_at_call,
            "market_mid_at_call": row.market_mid_at_call,
            "created_at": row.created_at.isoformat(),
            "resolved_outcome": row.resolved_outcome,
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            "pnl_usdc": row.pnl_usdc,
            "brier_contribution": row.brier_contribution,
            "predicted_edge_bps": row.predicted_edge_bps,
            "realized_edge_bps": row.realized_edge_bps,
            "call_confidence": row.call_confidence,
        }
        for row in rows
    ]


@app.post("/v1/journal/calls")
async def create_journal_call(
    payload: JournalCallCreateRequest,
    ch: AsyncClient = Depends(get_ch),
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        row = await journal_q.create_manual_call(
            pool=pg,
            ch=ch,
            settings=settings,
            payload=journal_q.CreateJournalCallInput(
                condition_id=payload.condition_id,
                outcome=payload.outcome,
                size_usdc=payload.size_usdc,
            ),
            asked_at=datetime.now(tz=UTC),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": row.id,
        "condition_id": row.condition_id,
        "outcome": row.outcome,
        "side": row.side,
        "size_usdc": row.size_usdc,
        "entry_price": row.entry_price,
        "model_prob_at_call": row.model_prob_at_call,
        "market_mid_at_call": row.market_mid_at_call,
        "created_at": row.created_at.isoformat(),
    }


@app.get("/v1/journal/summary")
async def journal_summary(
    ch: AsyncClient = Depends(get_ch),
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    summary = await journal_q.summary(pool=pg, ch=ch, settings=settings)
    return {
        "total_calls": summary.total_calls,
        "resolved_calls": summary.resolved_calls,
        "unresolved_calls": summary.unresolved_calls,
        "avg_brier": summary.avg_brier,
        "total_pnl_usdc": summary.total_pnl_usdc,
        "resolution_sync": (
            {
                "proxy_wallet": summary.resolution_sync.proxy_wallet,
                "verified_at": (
                    summary.resolution_sync.verified_at.isoformat()
                    if summary.resolution_sync.verified_at is not None
                    else None
                ),
                "open_positions": summary.resolution_sync.open_positions,
                "redeemable_positions": summary.resolution_sync.redeemable_positions,
                "total_position_value_usdc": summary.resolution_sync.total_position_value_usdc,
                "total_earnings_usdc": summary.resolution_sync.total_earnings_usdc,
            }
            if summary.resolution_sync is not None
            else None
        ),
        "confidence_buckets": summary.confidence_buckets,
        "best_calls": [
            {
                "id": row.id,
                "condition_id": row.condition_id,
                "outcome": row.outcome,
                "pnl_usdc": row.pnl_usdc,
                "predicted_edge_bps": row.predicted_edge_bps,
            }
            for row in summary.best_calls
        ],
        "worst_calls": [
            {
                "id": row.id,
                "condition_id": row.condition_id,
                "outcome": row.outcome,
                "pnl_usdc": row.pnl_usdc,
                "predicted_edge_bps": row.predicted_edge_bps,
            }
            for row in summary.worst_calls
        ],
        "edge_scatter": summary.edge_scatter,
        "calibration_points": summary.calibration_points,
    }


@app.get("/v1/tuning-profile")
async def tuning_profile(
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    profile = await tuning_q.get_active_profile(pool=pg, settings=settings)
    return {
        "name": profile.name,
        "preset": profile.preset,
        "log_odds_shifts": profile.log_odds_shifts,
        "is_active": profile.is_active,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


@app.put("/v1/tuning-profile")
async def update_tuning_profile(
    payload: TuningProfileRequest,
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        profile = await tuning_q.update_active_profile(
            pool=pg,
            settings=settings,
            payload=tuning_q.TuningProfileInput(
                preset=payload.preset,
                log_odds_shifts=payload.log_odds_shifts,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "name": profile.name,
        "preset": profile.preset,
        "log_odds_shifts": profile.log_odds_shifts,
        "is_active": profile.is_active,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }
