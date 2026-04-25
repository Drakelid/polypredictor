"""As-of reads over ``signal_events`` (M3.5 Signal Feed).

The table is written by many producers (whale flow today, arb + large-print
in later milestones). This reader exposes a unified feed with simple
filtering so the dashboard can render one strip across all signal types.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class SignalEvent:
    event_id: str
    event_type: str
    condition_id: str
    severity: float
    actor: str
    direction: str
    size_delta_usdc: float | None
    prior_size_usdc: float | None
    current_size_usdc: float | None
    payload: dict[str, Any] = field(default_factory=dict)
    event_time: datetime = field(default_factory=lambda: datetime.fromtimestamp(0))
    observed_at: datetime = field(default_factory=lambda: datetime.fromtimestamp(0))


_SELECT = (
    "event_id, event_type, condition_id, severity, actor, direction, "
    "size_delta_usdc, prior_size_usdc, current_size_usdc, payload, "
    "event_time, observed_at"
)


def _parse_payload(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_event(row: tuple[object, ...]) -> SignalEvent:
    return SignalEvent(
        event_id=str(row[0]),
        event_type=str(row[1]),
        condition_id=str(row[2]),
        severity=float(row[3]),
        actor=str(row[4]),
        direction=str(row[5]),
        size_delta_usdc=float(row[6]) if row[6] is not None else None,
        prior_size_usdc=float(row[7]) if row[7] is not None else None,
        current_size_usdc=float(row[8]) if row[8] is not None else None,
        payload=_parse_payload(row[9]),
        event_time=row[10],  # type: ignore[arg-type]
        observed_at=row[11],  # type: ignore[arg-type]
    )


async def list_signals(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_hours: int = 24,
    limit: int = 100,
    event_types: list[str] | None = None,
    condition_id: str | None = None,
    min_severity: float | None = None,
) -> list[SignalEvent]:
    """Latest events within ``lookback_hours``, optionally filtered.

    Results are ordered newest-first. The PIT contract is honored:
    ``observed_at <= asked_at`` so historical reads reproduce what the
    user would have seen at ``asked_at``.
    """
    start = asked_at - timedelta(hours=lookback_hours)
    where = [
        "observed_at <= {asof:DateTime64(3)}",
        "observed_at >= {start:DateTime64(3)}",
    ]
    parameters: dict[str, Any] = {"asof": asked_at, "start": start, "limit": limit}
    if event_types:
        where.append("event_type IN {types:Array(String)}")
        parameters["types"] = list(event_types)
    if condition_id:
        where.append("condition_id = {cond:String}")
        parameters["cond"] = condition_id
    if min_severity is not None:
        where.append("severity >= {min_severity:Float64}")
        parameters["min_severity"] = float(min_severity)
    query = f"""
        SELECT {_SELECT}
        FROM signal_events
        WHERE {' AND '.join(where)}
        ORDER BY observed_at DESC
        LIMIT {{limit:UInt32}}
    """
    result = await ch.query(query, parameters=parameters)
    return [_row_to_event(row) for row in result.result_rows]
