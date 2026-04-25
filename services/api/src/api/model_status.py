"""Per-market-type disable status for the auto-disable gate (M6.3 / §8).

The drift driver writes append-only ``disabled`` / ``re_enabled`` rows into
``model_disable_log``. The current state of a market type is the action of
its most recent row (PIT-correct: ``observed_at <= asked_at``). Serving
treats ``disabled`` as a hard short-circuit — the ensemble refinement is
skipped and the displayed probability falls back to the baseline path that
already degrades to ``market_mid`` when no other source applies.

Backtests automatically reproduce historical disable windows because the
asof query honors the same ``observed_at`` ordering as every other PIT
table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class ModelDisableStatus:
    market_type: str
    is_disabled: bool
    action: str
    consecutive_days: int
    threshold_days: int
    reason: str
    event_time: datetime
    observed_at: datetime


_SELECT = (
    "market_type, action, consecutive_days, threshold_days, reason, "
    "event_time, observed_at"
)


def _row_to_status(row: tuple[object, ...]) -> ModelDisableStatus:
    action = str(row[1])
    return ModelDisableStatus(
        market_type=str(row[0]),
        is_disabled=action == "disabled",
        action=action,
        consecutive_days=int(row[2]),
        threshold_days=int(row[3]),
        reason=str(row[4]),
        event_time=row[5],  # type: ignore[arg-type]
        observed_at=row[6],  # type: ignore[arg-type]
    )


async def model_disable_status_asof(
    ch: AsyncClient,
    *,
    market_type: str,
    asked_at: datetime,
) -> ModelDisableStatus | None:
    """Latest disable/re-enable transition for ``market_type``.

    Returns ``None`` if the market type has never been touched by the
    auto-disable rule — callers should treat that as "enabled" (the default).
    """
    query = f"""
        SELECT {_SELECT}
        FROM model_disable_log
        WHERE market_type = {{market_type:String}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(
        query,
        parameters={"market_type": market_type, "asof": asked_at},
    )
    rows = result.result_rows
    if not rows:
        return None
    return _row_to_status(rows[0])


async def model_disable_status_batch_asof(
    ch: AsyncClient,
    *,
    market_types: list[str],
    asked_at: datetime,
) -> dict[str, ModelDisableStatus]:
    """Latest status per market_type, batched. Missing types map to absent
    keys (callers treat as enabled)."""
    if not market_types:
        return {}
    query = f"""
        SELECT {_SELECT}
        FROM model_disable_log
        WHERE market_type IN {{market_types:Array(String)}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY market_type, observed_at DESC
        LIMIT 1 BY market_type
    """
    result = await ch.query(
        query,
        parameters={
            "market_types": list(market_types),
            "asof": asked_at,
        },
    )
    return {
        str(row[0]): _row_to_status(row)
        for row in result.result_rows
    }
