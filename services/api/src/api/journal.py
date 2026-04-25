"""Manual journal capture, scoring, and dashboard summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from asyncpg import Pool
from clickhouse_connect.driver.asyncclient import AsyncClient

from . import asof as asof_q
from .markets import MarketModelDetail, model_for_market
from .settings import Settings
from .users import ensure_demo_user

_CENT = Decimal("0.000001")


@dataclass(frozen=True)
class CreateJournalCallInput:
    condition_id: str
    outcome: str
    size_usdc: float


@dataclass(frozen=True)
class AutoJournalFillInput:
    condition_id: str
    token_id: str
    side: str
    price: float
    size: float
    source_event_id: str


@dataclass(frozen=True)
class JournalCall:
    id: str
    condition_id: str
    outcome: str
    side: str
    size_usdc: float
    entry_price: float
    model_prob_at_call: float
    model_band_lo_at_call: float
    model_band_hi_at_call: float
    market_mid_at_call: float
    created_at: datetime
    resolved_outcome: str | None
    resolved_at: datetime | None
    pnl_usdc: float | None
    brier_contribution: float | None
    predicted_edge_bps: float
    realized_edge_bps: float | None
    call_confidence: float


@dataclass(frozen=True)
class JournalSummary:
    total_calls: int
    resolved_calls: int
    unresolved_calls: int
    avg_brier: float | None
    total_pnl_usdc: float
    confidence_buckets: list[dict[str, Any]]
    best_calls: list[JournalCall]
    worst_calls: list[JournalCall]
    edge_scatter: list[dict[str, float]]
    calibration_points: list[dict[str, float]]
    resolution_sync: JournalResolutionSync | None = None


@dataclass(frozen=True)
class JournalResolutionSync:
    proxy_wallet: str
    verified_at: datetime | None
    open_positions: int
    redeemable_positions: int
    total_position_value_usdc: float
    total_earnings_usdc: float


async def create_manual_call(
    *,
    pool: Pool,
    ch: AsyncClient,
    settings: Settings,
    payload: CreateJournalCallInput,
    asked_at: datetime,
) -> JournalCall:
    user_id = await ensure_demo_user(pool, settings)
    from . import tuning as tuning_q

    tuning_profile = await tuning_q.get_active_profile(pool=pool, settings=settings)
    detail = await model_for_market(
        ch,
        condition_id=payload.condition_id,
        asked_at=asked_at,
        tuning_profile=tuning_profile,
    )
    if detail is None:
        raise ValueError("market not known")
    snapshot = await asof_q.latest_market_snapshot_asof(ch, payload.condition_id, asked_at)
    if snapshot is None or not snapshot.token_ids:
        raise ValueError("market tokens unavailable")
    entry_price = _entry_price_for_outcome(detail, payload.outcome)
    if entry_price is None:
        raise ValueError("market mid unavailable")
    model_prob = detail.model_prob
    if model_prob is None:
        raise ValueError("model probability unavailable")
    band_lo = detail.band_lo if detail.band_lo is not None else model_prob
    band_hi = detail.band_hi if detail.band_hi is not None else model_prob
    return await _insert_journal_call(
        pool=pool,
        user_id=user_id,
        condition_id=payload.condition_id,
        token_id=_token_id_for_outcome(snapshot.token_ids, payload.outcome),
        outcome=payload.outcome,
        size_usdc=payload.size_usdc,
        entry_price=entry_price,
        model_prob=model_prob,
        band_lo=band_lo,
        band_hi=band_hi,
        market_mid=detail.mid,
        source="manual",
        created_at=asked_at,
    )


async def create_auto_fill_call(
    *,
    pool: Pool,
    ch: AsyncClient,
    settings: Settings,
    payload: AutoJournalFillInput,
    asked_at: datetime,
) -> JournalCall | None:
    user_id = await ensure_demo_user(pool, settings)
    from . import tuning as tuning_q

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT id, condition_id, outcome, side, size_usdc, entry_price,
                   model_prob_at_call, model_band_lo_at_call, model_band_hi_at_call,
                   market_mid_at_call, created_at, resolved_outcome, resolved_at,
                   pnl_usdc, brier_contribution
            FROM journal_calls
            WHERE user_id = $1
              AND source = 'auto_wss'
              AND source_event_id = $2
            LIMIT 1
            """,
            user_id,
            payload.source_event_id,
        )
    if existing is not None:
        return _journal_call_from_row(existing)

    tuning_profile = await tuning_q.get_active_profile(pool=pool, settings=settings)
    detail = await model_for_market(
        ch,
        condition_id=payload.condition_id,
        asked_at=asked_at,
        tuning_profile=tuning_profile,
    )
    if detail is None:
        return None
    snapshot = await asof_q.latest_market_snapshot_asof(ch, payload.condition_id, asked_at)
    if snapshot is None or not snapshot.token_ids:
        return None
    directional = _directional_fill_call(
        token_ids=snapshot.token_ids,
        token_id=payload.token_id,
        side=payload.side,
        price=payload.price,
        size=payload.size,
    )
    if directional is None:
        return None
    model_prob = detail.model_prob
    if model_prob is None:
        return None
    band_lo = detail.band_lo if detail.band_lo is not None else model_prob
    band_hi = detail.band_hi if detail.band_hi is not None else model_prob
    return await _insert_journal_call(
        pool=pool,
        user_id=user_id,
        condition_id=payload.condition_id,
        token_id=directional["token_id"],
        outcome=directional["outcome"],
        size_usdc=directional["size_usdc"],
        entry_price=directional["entry_price"],
        model_prob=model_prob,
        band_lo=band_lo,
        band_hi=band_hi,
        market_mid=detail.mid,
        source="auto_wss",
        created_at=asked_at,
        source_event_id=payload.source_event_id,
    )


async def list_calls(
    *,
    pool: Pool,
    ch: AsyncClient,
    settings: Settings,
) -> list[JournalCall]:
    user_id = await ensure_demo_user(pool, settings)
    await sync_resolutions(pool=pool, ch=ch, user_id=user_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, condition_id, outcome, side, size_usdc, entry_price,
                   model_prob_at_call, model_band_lo_at_call, model_band_hi_at_call,
                   market_mid_at_call, created_at, resolved_outcome, resolved_at,
                   pnl_usdc, brier_contribution
            FROM journal_calls
            WHERE user_id = $1
            ORDER BY created_at DESC
            """,
            user_id,
        )
    return [_journal_call_from_row(row) for row in rows]


async def summary(
    *,
    pool: Pool,
    ch: AsyncClient,
    settings: Settings,
) -> JournalSummary:
    calls = await list_calls(pool=pool, ch=ch, settings=settings)
    resolved = [call for call in calls if call.resolved_outcome in {"YES", "NO"}]
    avg_brier = (
        sum(call.brier_contribution or 0.0 for call in resolved) / len(resolved)
        if resolved
        else None
    )
    total_pnl = sum(call.pnl_usdc or 0.0 for call in resolved)
    best_calls = sorted(resolved, key=lambda call: call.pnl_usdc or 0.0, reverse=True)[:5]
    worst_calls = sorted(resolved, key=lambda call: call.pnl_usdc or 0.0)[:5]
    edge_scatter = [
        {
            "predicted_edge_bps": call.predicted_edge_bps,
            "realized_edge_bps": call.realized_edge_bps or 0.0,
        }
        for call in resolved
        if call.realized_edge_bps is not None
    ]
    buckets = _confidence_buckets(resolved)
    calibration = [
        {
            "bucket_mid": bucket["bucket_mid"],
            "avg_predicted": bucket["avg_confidence"],
            "hit_rate": bucket["hit_rate"],
            "count": bucket["count"],
        }
        for bucket in buckets
        if bucket["count"] > 0
    ]
    resolution_sync = await _journal_resolution_sync(pool=pool, settings=settings)
    return JournalSummary(
        total_calls=len(calls),
        resolved_calls=len(resolved),
        unresolved_calls=len(calls) - len(resolved),
        avg_brier=avg_brier,
        total_pnl_usdc=total_pnl,
        confidence_buckets=buckets,
        best_calls=best_calls,
        worst_calls=worst_calls,
        edge_scatter=edge_scatter,
        calibration_points=calibration,
        resolution_sync=resolution_sync,
    )


async def _journal_resolution_sync(
    *,
    pool: Pool,
    settings: Settings,
) -> JournalResolutionSync | None:
    # Local import avoids an otherwise unnecessary module dependency at import time.
    from . import polymarket_account as polymarket_account_q

    link = await polymarket_account_q.get_linked_address(pool=pool, settings=settings)
    if link.summary is None:
        return None
    return JournalResolutionSync(
        proxy_wallet=link.summary.proxy_wallet,
        verified_at=link.summary.verified_at,
        open_positions=link.summary.open_positions,
        redeemable_positions=link.summary.redeemable_positions,
        total_position_value_usdc=link.summary.total_position_value_usdc,
        total_earnings_usdc=link.summary.total_earnings_usdc,
    )


async def sync_resolutions(*, pool: Pool, ch: AsyncClient, user_id: UUID) -> None:
    async with pool.acquire() as conn:
        unresolved_rows = await conn.fetch(
            """
            SELECT id, condition_id, outcome, side, size_usdc, entry_price, model_prob_at_call
            FROM journal_calls
            WHERE user_id = $1
              AND resolved_outcome IS NULL
            """,
            user_id,
        )
    if not unresolved_rows:
        return
    condition_ids = sorted({str(row["condition_id"]) for row in unresolved_rows})
    resolutions = await _latest_resolutions(ch, condition_ids)
    updates: list[tuple[str, datetime, float, float, str]] = []
    for row in unresolved_rows:
        resolution = resolutions.get(str(row["condition_id"]))
        if resolution is None:
            continue
        resolved_outcome, resolved_at = resolution
        if resolved_outcome not in {"YES", "NO"}:
            continue
        pnl = _pnl_usdc(
            side=str(row["side"]),
            outcome=str(row["outcome"]),
            entry_price=float(row["entry_price"]),
            size_usdc=float(row["size_usdc"]),
            resolved_outcome=resolved_outcome,
        )
        brier = _brier_contribution(float(row["model_prob_at_call"]), resolved_outcome)
        updates.append((resolved_outcome, resolved_at, pnl, brier, str(row["id"])))
    if not updates:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            UPDATE journal_calls
            SET resolved_outcome = $1,
                resolved_at = $2,
                pnl_usdc = $3,
                brier_contribution = $4
            WHERE id = $5::uuid
            """,
            updates,
        )


async def _latest_resolutions(
    ch: AsyncClient,
    condition_ids: list[str],
) -> dict[str, tuple[str, datetime]]:
    if not condition_ids:
        return {}
    result = await ch.query(
        """
        SELECT condition_id, resolved_outcome, event_time
        FROM market_resolutions
        WHERE condition_id IN {conds:Array(String)}
          AND resolved_outcome IN ('YES', 'NO', 'INVALID')
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
        """,
        parameters={"conds": condition_ids},
    )
    return {
        str(row[0]): (str(row[1]), row[2])
        for row in result.result_rows
    }


def _journal_call_from_row(row: Any) -> JournalCall:
    outcome = str(row["outcome"])
    model_prob = float(row["model_prob_at_call"])
    entry_price = float(row["entry_price"])
    predicted_edge = _predicted_edge_bps(model_prob, outcome, entry_price)
    pnl = float(row["pnl_usdc"]) if row["pnl_usdc"] is not None else None
    size_usdc = float(row["size_usdc"])
    return JournalCall(
        id=str(row["id"]),
        condition_id=str(row["condition_id"]),
        outcome=outcome,
        side=str(row["side"]),
        size_usdc=size_usdc,
        entry_price=entry_price,
        model_prob_at_call=model_prob,
        model_band_lo_at_call=float(row["model_band_lo_at_call"]),
        model_band_hi_at_call=float(row["model_band_hi_at_call"]),
        market_mid_at_call=float(row["market_mid_at_call"]),
        created_at=_coerce_utc(row["created_at"]),
        resolved_outcome=str(row["resolved_outcome"]) if row["resolved_outcome"] is not None else None,
        resolved_at=_coerce_utc(row["resolved_at"]) if row["resolved_at"] is not None else None,
        pnl_usdc=pnl,
        brier_contribution=float(row["brier_contribution"]) if row["brier_contribution"] is not None else None,
        predicted_edge_bps=predicted_edge,
        realized_edge_bps=((pnl / size_usdc) * 10_000.0) if pnl is not None and size_usdc > 0 else None,
        call_confidence=_call_confidence(model_prob, outcome),
    )


async def _insert_journal_call(
    *,
    pool: Pool,
    user_id: UUID,
    condition_id: str,
    token_id: str,
    outcome: str,
    size_usdc: float,
    entry_price: float | None,
    model_prob: float,
    band_lo: float,
    band_hi: float,
    market_mid: float | None,
    source: str,
    created_at: datetime,
    source_event_id: str | None = None,
) -> JournalCall:
    if entry_price is None or market_mid is None:
        raise ValueError("market mid unavailable")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO journal_calls (
                user_id,
                condition_id,
                token_id,
                outcome,
                side,
                size_usdc,
                entry_price,
                model_prob_at_call,
                model_band_lo_at_call,
                model_band_hi_at_call,
                market_mid_at_call,
                source,
                source_event_id,
                created_at
            )
            VALUES ($1, $2, $3, $4, 'BUY', $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING id, condition_id, outcome, side, size_usdc, entry_price,
                      model_prob_at_call, model_band_lo_at_call, model_band_hi_at_call,
                      market_mid_at_call, created_at, resolved_outcome, resolved_at,
                      pnl_usdc, brier_contribution
            """,
            user_id,
            condition_id,
            token_id,
            outcome,
            _decimal(size_usdc),
            _decimal(entry_price),
            _decimal(model_prob),
            _decimal(band_lo),
            _decimal(band_hi),
            _decimal(market_mid),
            source,
            source_event_id,
            created_at,
        )
    assert row is not None
    return _journal_call_from_row(row)


def _entry_price_for_outcome(detail: MarketModelDetail, outcome: str) -> float | None:
    if detail.mid is None:
        return None
    return detail.mid if outcome == "YES" else 1.0 - detail.mid


def _token_id_for_outcome(token_ids: list[str], outcome: str) -> str:
    if outcome == "YES":
        return str(token_ids[0])
    if len(token_ids) > 1:
        return str(token_ids[1])
    return str(token_ids[0])


def _directional_fill_call(
    *,
    token_ids: list[str],
    token_id: str,
    side: str,
    price: float,
    size: float,
) -> dict[str, Any] | None:
    token_id = str(token_id)
    yes_token = str(token_ids[0])
    no_token = str(token_ids[1]) if len(token_ids) > 1 else yes_token
    if token_id == yes_token:
        token_outcome = "YES"
    elif token_id == no_token:
        token_outcome = "NO"
    else:
        return None
    side = side.upper()
    if side == "BUY":
        outcome = token_outcome
        entry_price = price
    elif side == "SELL":
        outcome = "NO" if token_outcome == "YES" else "YES"
        entry_price = 1.0 - price
    else:
        return None
    if entry_price <= 0 or entry_price >= 1:
        return None
    return {
        "outcome": outcome,
        "token_id": _token_id_for_outcome(token_ids, outcome),
        "entry_price": entry_price,
        "size_usdc": price * size,
    }


def _predicted_edge_bps(model_prob_yes: float, outcome: str, entry_price: float) -> float:
    model_side_prob = model_prob_yes if outcome == "YES" else 1.0 - model_prob_yes
    return (model_side_prob - entry_price) * 10_000.0


def _call_confidence(model_prob_yes: float, outcome: str) -> float:
    return model_prob_yes if outcome == "YES" else 1.0 - model_prob_yes


def _brier_contribution(model_prob_yes: float, resolved_outcome: str) -> float:
    realized = 1.0 if resolved_outcome == "YES" else 0.0
    return (model_prob_yes - realized) ** 2


def _pnl_usdc(
    *,
    side: str,
    outcome: str,
    entry_price: float,
    size_usdc: float,
    resolved_outcome: str,
) -> float:
    if side != "BUY" or entry_price <= 0:
        return 0.0
    won = outcome == resolved_outcome
    if not won:
        return -size_usdc
    shares = size_usdc / entry_price
    return shares - size_usdc


def _confidence_bucket_label(confidence: float) -> str:
    lo = int(confidence * 10) / 10
    hi = lo + 0.1
    return f"{int(lo * 100)}-{int(min(hi, 1.0) * 100)}%"


def _confidence_buckets(calls: list[JournalCall]) -> list[dict[str, Any]]:
    buckets: dict[int, list[JournalCall]] = {idx: [] for idx in range(5, 10)}
    for call in calls:
        bucket = min(9, max(5, int(call.call_confidence * 10)))
        buckets[bucket].append(call)
    out: list[dict[str, Any]] = []
    for idx in sorted(buckets):
        group = buckets[idx]
        wins = [
            1.0
            for call in group
            if (call.outcome == "YES" and call.resolved_outcome == "YES")
            or (call.outcome == "NO" and call.resolved_outcome == "NO")
        ]
        out.append(
            {
                "label": _confidence_bucket_label(idx / 10),
                "bucket_mid": (idx / 10) + 0.05,
                "count": len(group),
                "avg_confidence": (
                    sum(call.call_confidence for call in group) / len(group)
                    if group
                    else 0.0
                ),
                "hit_rate": (sum(wins) / len(group)) if group else 0.0,
            }
        )
    return out


def _decimal(value: float | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _coerce_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
