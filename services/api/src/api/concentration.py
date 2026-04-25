"""As-of reads over ``market_concentration`` (M3.2).

Written by :mod:`ingest.workers.holders_poller`. Consumed by:

* Dashboard list + detail endpoints (concentration badge)
* Ensemble feature pipeline (``concentration_score`` — max of YES/NO Gini
  gates the PRD "down-weight when concentration > 0.6" rule)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class MarketConcentration:
    condition_id: str
    yes_gini: float | None
    no_gini: float | None
    yes_top1_pct: float | None
    no_top1_pct: float | None
    yes_top5_pct: float | None
    no_top5_pct: float | None
    yes_whale_flag: bool
    no_whale_flag: bool
    any_whale_flag: bool
    yes_holders_count: int
    no_holders_count: int
    max_gini: float | None
    event_time: datetime
    observed_at: datetime


_SELECT = (
    "condition_id, yes_gini, no_gini, yes_top1_pct, no_top1_pct, "
    "yes_top5_pct, no_top5_pct, yes_whale_flag, no_whale_flag, "
    "any_whale_flag, yes_holders_count, no_holders_count, max_gini, "
    "event_time, observed_at"
)


def _row_to_concentration(row: tuple[object, ...]) -> MarketConcentration:
    return MarketConcentration(
        condition_id=str(row[0]),
        yes_gini=float(row[1]) if row[1] is not None else None,
        no_gini=float(row[2]) if row[2] is not None else None,
        yes_top1_pct=float(row[3]) if row[3] is not None else None,
        no_top1_pct=float(row[4]) if row[4] is not None else None,
        yes_top5_pct=float(row[5]) if row[5] is not None else None,
        no_top5_pct=float(row[6]) if row[6] is not None else None,
        yes_whale_flag=bool(row[7]),
        no_whale_flag=bool(row[8]),
        any_whale_flag=bool(row[9]),
        yes_holders_count=int(row[10]),
        no_holders_count=int(row[11]),
        max_gini=float(row[12]) if row[12] is not None else None,
        event_time=row[13],  # type: ignore[arg-type]
        observed_at=row[14],  # type: ignore[arg-type]
    )


async def concentration_asof(
    ch: AsyncClient,
    condition_id: str,
    asked_at: datetime,
) -> MarketConcentration | None:
    query = f"""
        SELECT {_SELECT}
        FROM market_concentration
        WHERE condition_id = {{cond:String}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(
        query, parameters={"cond": condition_id, "asof": asked_at}
    )
    rows = result.result_rows
    if not rows:
        return None
    return _row_to_concentration(rows[0])


async def concentration_batch_asof(
    ch: AsyncClient,
    condition_ids: list[str],
    asked_at: datetime,
) -> dict[str, MarketConcentration]:
    if not condition_ids:
        return {}
    query = f"""
        SELECT {_SELECT}
        FROM market_concentration
        WHERE condition_id IN {{conds:Array(String)}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
    """
    result = await ch.query(
        query,
        parameters={"conds": list(condition_ids), "asof": asked_at},
    )
    return {
        str(row[0]): _row_to_concentration(row)
        for row in result.result_rows
    }
