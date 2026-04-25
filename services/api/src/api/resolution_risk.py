"""As-of reads over ``market_resolution_risk`` (M4.4).

Written by :mod:`ingest.workers.gamma_discovery`. Consumed by:

* Dashboard list + detail endpoints for the warning badge
* Ensemble serving as ``resolution_risk_score``
* Post-hoc uncertainty widening / edge suppression rules
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class ResolutionRiskRow:
    condition_id: str
    risk_score: float
    risk_level: str
    is_flagged: bool
    risk_multiplier: float
    classifier: str
    reasons: list[str]
    event_time: datetime
    observed_at: datetime


_SELECT = (
    "condition_id, risk_score, risk_level, is_flagged, risk_multiplier, "
    "classifier, reasons, event_time, observed_at"
)


def _split_reasons(raw: object) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    return [part for part in text.split("|") if part]


def _row_to_resolution_risk(row: tuple[object, ...]) -> ResolutionRiskRow:
    return ResolutionRiskRow(
        condition_id=str(row[0]),
        risk_score=float(row[1]),
        risk_level=str(row[2]),
        is_flagged=bool(row[3]),
        risk_multiplier=float(row[4]),
        classifier=str(row[5]),
        reasons=_split_reasons(row[6]),
        event_time=row[7],  # type: ignore[arg-type]
        observed_at=row[8],  # type: ignore[arg-type]
    )


async def resolution_risk_asof(
    ch: AsyncClient,
    condition_id: str,
    asked_at: datetime,
) -> ResolutionRiskRow | None:
    query = f"""
        SELECT {_SELECT}
        FROM market_resolution_risk
        WHERE condition_id = {{cond:String}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(
        query,
        parameters={"cond": condition_id, "asof": asked_at},
    )
    rows = result.result_rows
    if not rows:
        return None
    return _row_to_resolution_risk(rows[0])


async def resolution_risk_batch_asof(
    ch: AsyncClient,
    condition_ids: list[str],
    asked_at: datetime,
) -> dict[str, ResolutionRiskRow]:
    if not condition_ids:
        return {}
    query = f"""
        SELECT {_SELECT}
        FROM market_resolution_risk
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
        str(row[0]): _row_to_resolution_risk(row)
        for row in result.result_rows
    }
