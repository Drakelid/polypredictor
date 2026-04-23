"""FastAPI app entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import Depends, FastAPI, HTTPException, Query

from . import asof as asof_q
from . import markets as markets_q
from .clickhouse import get_async_client
from .settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.ch = await get_async_client()
    try:
        yield
    finally:
        await app.state.ch.close()


app = FastAPI(title="PolyPredictor API", version="0.0.1", lifespan=lifespan)


def get_ch() -> Any:
    return app.state.ch


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
        "baseline_source": detail.baseline_source,
        "edge_bps": detail.edge_bps,
        "uncertainty_multiplier": detail.uncertainty_multiplier,
        "reasons": detail.reasons,
        "classifier_reasons": detail.classifier_reasons,
    }
