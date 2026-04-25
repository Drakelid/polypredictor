"""Derived adversarial-flow context for M4.5.

The PRD calls for detecting directional market moves that are not corroborated
by external venues or linked social/news evidence. We bootstrap that from
already-persisted data:

* directional flow features from ``market_features``
* recent ``external_divergence`` signal events (perps/options disagree)
* recent linked ``external_events`` (news/social/macro evidence)
* top-of-book quote depth for a ``thin_book`` tag
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient

from .features import FeatureSnapshotRow
from .settings import Settings, get_settings


@dataclass(frozen=True)
class QuoteDepthRow:
    token_id: str
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float
    event_time: datetime
    observed_at: datetime

    @property
    def top_book_depth_usdc(self) -> float:
        return max(self.best_bid, 0.0) * max(self.bid_size, 0.0) + max(
            self.best_ask, 0.0
        ) * max(self.ask_size, 0.0)


@dataclass(frozen=True)
class AdversarialFlowContext:
    condition_id: str
    score: float
    is_flagged: bool
    thin_book: bool
    top_book_depth_usdc: float | None
    flow_strength: float
    external_divergence_count: int
    external_event_count: int
    reasons: list[str]


def _clip_score(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _flow_strength(feature_snapshot: FeatureSnapshotRow | None) -> float:
    if feature_snapshot is None:
        return 0.0
    candidates = [
        abs(feature_snapshot.informed_taker_flow_24h or 0.0),
        abs(feature_snapshot.decayed_directional_flow_24h or 0.0),
    ]
    return max(candidates, default=0.0)


def build_adversarial_flow_context(
    *,
    condition_id: str,
    feature_snapshot: FeatureSnapshotRow | None,
    quote_depth: QuoteDepthRow | None,
    external_divergence_count: int,
    external_event_count: int,
    settings: Settings | None = None,
) -> AdversarialFlowContext:
    cfg = settings or get_settings()
    flow_strength = _flow_strength(feature_snapshot)
    min_flow = max(1e-6, cfg.adversarial_flow_min_flow_strength)
    flow_component = _clip_score((flow_strength - min_flow) / max(1e-6, 1.0 - min_flow))
    top_book_depth = quote_depth.top_book_depth_usdc if quote_depth is not None else None
    thin_book = (
        top_book_depth is not None
        and top_book_depth < cfg.thin_book_top_level_depth_threshold_usdc
    )

    score = 0.6 * flow_component
    reasons: list[str] = []
    if flow_component > 0:
        reasons.append("large directional flow")
    if external_divergence_count > 0:
        score += 0.25
        reasons.append("perps/options divergence not confirming the move")
    if external_event_count == 0:
        score += 0.15
        reasons.append("no recent linked social/news evidence")
    else:
        score -= 0.10
    if thin_book:
        score += 0.15
        reasons.append("top-of-book depth below thin-book threshold")

    score = _clip_score(score)
    return AdversarialFlowContext(
        condition_id=condition_id,
        score=score,
        is_flagged=score >= cfg.adversarial_flow_flag_threshold,
        thin_book=thin_book,
        top_book_depth_usdc=top_book_depth,
        flow_strength=flow_strength,
        external_divergence_count=external_divergence_count,
        external_event_count=external_event_count,
        reasons=reasons,
    )


async def quote_depths_batch_asof(
    ch: AsyncClient,
    token_ids: list[str],
    asked_at: datetime,
) -> dict[str, QuoteDepthRow]:
    if not token_ids:
        return {}
    query = """
        SELECT token_id, best_bid, best_ask, bid_size, ask_size, event_time, observed_at
        FROM market_quotes
        WHERE token_id IN {toks:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY token_id, observed_at DESC
        LIMIT 1 BY token_id
    """
    result = await ch.query(
        query,
        parameters={"toks": list(token_ids), "asof": asked_at},
    )
    return {
        str(row[0]): QuoteDepthRow(
            token_id=str(row[0]),
            best_bid=float(row[1]),
            best_ask=float(row[2]),
            bid_size=float(row[3]),
            ask_size=float(row[4]),
            event_time=row[5],  # type: ignore[arg-type]
            observed_at=row[6],  # type: ignore[arg-type]
        )
        for row in result.result_rows
    }


async def external_divergence_counts_batch(
    ch: AsyncClient,
    condition_ids: list[str],
    *,
    asked_at: datetime,
    lookback_hours: int,
) -> dict[str, int]:
    if not condition_ids:
        return {}
    start = asked_at - timedelta(hours=lookback_hours)
    query = """
        SELECT condition_id, count()
        FROM signal_events
        WHERE condition_id IN {conds:Array(String)}
          AND event_type = 'external_divergence'
          AND observed_at >= {start:DateTime64(3)}
          AND observed_at <= {asof:DateTime64(3)}
        GROUP BY condition_id
    """
    result = await ch.query(
        query,
        parameters={"conds": list(condition_ids), "start": start, "asof": asked_at},
    )
    return {str(row[0]): int(row[1]) for row in result.result_rows}


async def external_event_counts_batch(
    ch: AsyncClient,
    condition_ids: list[str],
    *,
    asked_at: datetime,
    lookback_hours: int,
) -> dict[str, int]:
    if not condition_ids:
        return {}
    start = asked_at - timedelta(hours=lookback_hours)
    query = """
        SELECT condition_id, count()
        FROM (
            SELECT arrayJoin(related_markets) AS condition_id
            FROM external_events
            WHERE observed_at >= {start:DateTime64(3)}
              AND observed_at <= {asof:DateTime64(3)}
        )
        WHERE condition_id IN {conds:Array(String)}
        GROUP BY condition_id
    """
    result = await ch.query(
        query,
        parameters={"conds": list(condition_ids), "start": start, "asof": asked_at},
    )
    return {str(row[0]): int(row[1]) for row in result.result_rows}


async def adversarial_flow_batch_contexts(
    ch: AsyncClient,
    *,
    condition_ids: list[str],
    token_ids_by_condition: dict[str, str],
    feature_snapshots: dict[str, FeatureSnapshotRow],
    asked_at: datetime,
    settings: Settings | None = None,
) -> dict[str, AdversarialFlowContext]:
    cfg = settings or get_settings()
    quote_depths = await quote_depths_batch_asof(
        ch,
        [token_id for token_id in token_ids_by_condition.values() if token_id],
        asked_at,
    )
    divergence_counts = await external_divergence_counts_batch(
        ch,
        condition_ids,
        asked_at=asked_at,
        lookback_hours=cfg.adversarial_flow_signal_window_hours,
    )
    event_counts = await external_event_counts_batch(
        ch,
        condition_ids,
        asked_at=asked_at,
        lookback_hours=cfg.adversarial_flow_external_event_window_hours,
    )
    out: dict[str, AdversarialFlowContext] = {}
    for condition_id in condition_ids:
        token_id = token_ids_by_condition.get(condition_id)
        out[condition_id] = build_adversarial_flow_context(
            condition_id=condition_id,
            feature_snapshot=feature_snapshots.get(condition_id),
            quote_depth=quote_depths.get(token_id) if token_id else None,
            external_divergence_count=divergence_counts.get(condition_id, 0),
            external_event_count=event_counts.get(condition_id, 0),
            settings=cfg,
        )
    return out


async def adversarial_flow_asof(
    ch: AsyncClient,
    *,
    condition_id: str,
    token_id: str | None,
    feature_snapshot: FeatureSnapshotRow | None,
    asked_at: datetime,
    settings: Settings | None = None,
) -> AdversarialFlowContext:
    rows = await adversarial_flow_batch_contexts(
        ch,
        condition_ids=[condition_id],
        token_ids_by_condition={condition_id: token_id or ""},
        feature_snapshots=(
            {condition_id: feature_snapshot} if feature_snapshot is not None else {}
        ),
        asked_at=asked_at,
        settings=settings,
    )
    return rows[condition_id]
