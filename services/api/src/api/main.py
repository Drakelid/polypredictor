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
from . import features as features_q
from . import journal as journal_q
from . import markets as markets_q
from .clickhouse import get_async_client
from .postgres import get_async_pool
from .settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.ch = await get_async_client()
    app.state.pg = await get_async_pool()
    try:
        yield
    finally:
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


@app.get("/v1/markets")
async def markets_list(
    limit: int = Query(200, ge=1, le=500),
    ch: AsyncClient = Depends(get_ch),
) -> list[dict[str, Any]]:
    """Dashboard list — one row per market with model probability + edge.

    Reads the point-in-time stack (snapshot + quote + classification) at
    wall-clock now, runs each market through the baseline pipeline, and
    exposes the displayed probability + the source badge.
    """
    asked_at = datetime.now(tz=UTC)
    rows = await markets_q.list_markets(ch, asked_at=asked_at, limit=limit)
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
        }
        for r in rows
    ]


@app.get("/v1/markets/{condition_id}/model")
async def market_model(
    condition_id: str,
    ch: AsyncClient = Depends(get_ch),
) -> dict[str, Any]:
    """Model-probability payload for the market detail view.

    Returns the baseline + classifier provenance so the UI can render both
    the big-number probability and the "Why this number?" trust badge.
    """
    asked_at = datetime.now(tz=UTC)
    detail = await markets_q.model_for_market(
        ch, condition_id=condition_id, asked_at=asked_at
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
    }


@app.get("/v1/markets/{condition_id}/history")
async def market_history(
    condition_id: str,
    window_hours: int = Query(24 * 7, ge=1, le=24 * 30),
    max_points: int = Query(96, ge=8, le=240),
    ch: AsyncClient = Depends(get_ch),
) -> list[dict[str, Any]]:
    asked_at = datetime.now(tz=UTC)
    rows = await markets_q.history_for_market(
        ch,
        condition_id=condition_id,
        asked_at=asked_at,
        window_hours=window_hours,
        max_points=max_points,
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
