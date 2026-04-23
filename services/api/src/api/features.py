"""As-of reads over persisted market feature snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class FeatureSnapshotRow:
    condition_id: str
    token_id: str
    mid: float
    spread: float
    book_imbalance_1pct: float | None
    book_imbalance_5pct: float | None
    momentum_1h: float | None
    momentum_24h: float | None
    momentum_7d: float | None
    realized_vol_24h: float | None
    informed_taker_flow_24h: float | None
    passive_maker_flow_24h: float | None
    decayed_directional_flow_24h: float | None
    event_time: datetime
    observed_at: datetime


async def feature_snapshot_asof(
    ch: AsyncClient,
    condition_id: str,
    asked_at: datetime,
) -> FeatureSnapshotRow | None:
    query = """
        SELECT
            condition_id,
            token_id,
            mid,
            spread,
            book_imbalance_1pct,
            book_imbalance_5pct,
            momentum_1h,
            momentum_24h,
            momentum_7d,
            realized_vol_24h,
            informed_taker_flow_24h,
            passive_maker_flow_24h,
            decayed_directional_flow_24h,
            event_time,
            observed_at
        FROM market_features
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
    return FeatureSnapshotRow(
        condition_id=str(row[0]),
        token_id=str(row[1]),
        mid=float(row[2]),
        spread=float(row[3]),
        book_imbalance_1pct=float(row[4]) if row[4] is not None else None,
        book_imbalance_5pct=float(row[5]) if row[5] is not None else None,
        momentum_1h=float(row[6]) if row[6] is not None else None,
        momentum_24h=float(row[7]) if row[7] is not None else None,
        momentum_7d=float(row[8]) if row[8] is not None else None,
        realized_vol_24h=float(row[9]) if row[9] is not None else None,
        informed_taker_flow_24h=float(row[10]) if row[10] is not None else None,
        passive_maker_flow_24h=float(row[11]) if row[11] is not None else None,
        decayed_directional_flow_24h=float(row[12]) if row[12] is not None else None,
        event_time=row[13],
        observed_at=row[14],
    )


async def feature_snapshots_batch_asof(
    ch: AsyncClient,
    condition_ids: list[str],
    asked_at: datetime,
) -> dict[str, FeatureSnapshotRow]:
    if not condition_ids:
        return {}
    query = """
        SELECT
            condition_id,
            token_id,
            mid,
            spread,
            book_imbalance_1pct,
            book_imbalance_5pct,
            momentum_1h,
            momentum_24h,
            momentum_7d,
            realized_vol_24h,
            informed_taker_flow_24h,
            passive_maker_flow_24h,
            decayed_directional_flow_24h,
            event_time,
            observed_at
        FROM market_features
        WHERE condition_id IN {conds:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
    """
    result = await ch.query(
        query,
        parameters={"conds": list(condition_ids), "asof": asked_at},
    )
    out: dict[str, FeatureSnapshotRow] = {}
    for row in result.result_rows:
        feature_row = FeatureSnapshotRow(
            condition_id=str(row[0]),
            token_id=str(row[1]),
            mid=float(row[2]),
            spread=float(row[3]),
            book_imbalance_1pct=float(row[4]) if row[4] is not None else None,
            book_imbalance_5pct=float(row[5]) if row[5] is not None else None,
            momentum_1h=float(row[6]) if row[6] is not None else None,
            momentum_24h=float(row[7]) if row[7] is not None else None,
            momentum_7d=float(row[8]) if row[8] is not None else None,
            realized_vol_24h=float(row[9]) if row[9] is not None else None,
            informed_taker_flow_24h=float(row[10]) if row[10] is not None else None,
            passive_maker_flow_24h=float(row[11]) if row[11] is not None else None,
            decayed_directional_flow_24h=float(row[12]) if row[12] is not None else None,
            event_time=row[13],
            observed_at=row[14],
        )
        out[feature_row.condition_id] = feature_row
    return out
