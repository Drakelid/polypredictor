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

from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient
from model import (
    BaselineInputs,
    PipelineResult,
    classify,
    probability_for_market,
)

from .classifications import (
    ClassificationRow,
    classification_asof,
    classifications_batch_asof,
)


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
    baseline_source: str
    edge_bps: float | None
    needs_review: bool
    confidence: float
    time_to_resolution_s: float | None


async def _latest_markets(
    ch: AsyncClient, asked_at: datetime, *, limit: int
) -> list[dict[str, object]]:
    """Latest snapshot per ``condition_id`` ordered by volume.

    ``LIMIT 1 BY condition_id`` keeps the one freshest row per market without
    a window-function or subquery — ClickHouse-native and cheap.
    """
    query = """
        SELECT condition_id, question, slug, category, tags, volume_usdc,
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
        if not bool(r[9]) or bool(r[10]) or bool(r[11]):
            continue  # skip inactive/closed/archived — dashboard is live markets
        out.append(
            {
                "condition_id": str(r[0]),
                "question": str(r[1]),
                "slug": str(r[2]),
                "category": str(r[3]) if r[3] else None,
                "tags": list(r[4]) if r[4] else [],
                "volume_usdc": float(r[5]),
                "liquidity_usdc": float(r[6]),
                "end_date": r[7],
                "token_ids": list(r[8]) if r[8] else [],
                "observed_at": r[12],
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


def _pipeline_for_row(
    *,
    snap: dict[str, object],
    mid: float | None,
    classification: ClassificationRow | None,
    asked_at: datetime,
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
    from model import ClassificationResult

    cls_result = ClassificationResult(
        market_type=classification.market_type,
        confidence=classification.confidence,
        features=classification.features,
        reasons=classification.reasons,
    )
    inputs = BaselineInputs(
        features=classification.features,
        asked_at=asked_at.timestamp(),
        market_mid=mid,
        # Spot / IV / consensus / priors all enter in later milestones; for M1
        # they are None and the pipeline gracefully falls back to market_mid.
    )
    return probability_for_market(cls_result, inputs), classification


def _time_to_resolution_s(end_date: datetime | None, asked_at: datetime) -> float | None:
    if end_date is None:
        return None
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=asked_at.tzinfo)
    return (end_date - asked_at).total_seconds()


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

    rows: list[MarketListRow] = []
    for snap in snaps:
        tok_ids = list(snap.get("token_ids") or [])  # type: ignore[arg-type]
        yes_mid: float | None = mids.get(tok_ids[0]) if tok_ids else None
        cid = str(snap["condition_id"])
        result, classification = _pipeline_for_row(
            snap=snap,
            mid=yes_mid,
            classification=classifications.get(cid),
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
                model_prob=result.displayed_probability,
                baseline_source=str(result.displayed_source.value),
                edge_bps=result.edge_bps,
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
    baseline_source: str
    edge_bps: float | None
    uncertainty_multiplier: float
    reasons: list[str]
    classifier_reasons: list[str]


async def model_for_market(
    ch: AsyncClient, *, condition_id: str, asked_at: datetime
) -> MarketModelDetail | None:
    """Model-probability + provenance payload for the detail view."""
    query = """
        SELECT condition_id, question, slug, category, tags, volume_usdc,
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
        "question": str(r[1]),
        "slug": str(r[2]),
        "category": str(r[3]) if r[3] else None,
        "tags": list(r[4]) if r[4] else [],
        "volume_usdc": float(r[5]),
        "liquidity_usdc": float(r[6]),
        "end_date": r[7],
        "token_ids": list(r[8]) if r[8] else [],
    }
    tok_ids = list(snap["token_ids"])  # type: ignore[arg-type]
    mids = await _latest_mids_for_tokens(ch, tok_ids, asked_at)
    yes_mid = mids.get(tok_ids[0]) if tok_ids else None

    classification = await classification_asof(ch, condition_id, asked_at)
    pipeline_result, classification = _pipeline_for_row(
        snap=snap,
        mid=yes_mid,
        classification=classification,
        asked_at=asked_at,
    )
    return MarketModelDetail(
        condition_id=condition_id,
        question=str(snap["question"]),
        market_type=str(classification.market_type.value),
        confidence=classification.confidence,
        needs_review=classification.needs_review,
        classifier=classification.classifier,
        mid=yes_mid,
        model_prob=pipeline_result.displayed_probability,
        baseline_source=str(pipeline_result.displayed_source.value),
        edge_bps=pipeline_result.edge_bps,
        uncertainty_multiplier=pipeline_result.baseline.uncertainty_multiplier,
        reasons=list(pipeline_result.baseline.reasons),
        classifier_reasons=list(classification.reasons),
    )
