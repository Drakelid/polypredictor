"""M8.3 End-of-life convergence monitoring (PRD §6.8).

Three operational checks, each backed by a separate ClickHouse query:

1. **Ramp-up check** — for every market currently in the final 5% of its
   life, compute the expected ``final_life_market_mid_weight`` (the same
   value the markets serve path applies). The aggregate report shows how
   many live markets are inside the convergence ramp and how many are
   inside the final-hour phantom window so operators can spot when the
   gate is engaging.

2. **Phantom-edge density** — count signal-feed events whose ``event_time``
   landed within the final hour before resolution over the lookback. The
   markets API suppresses ``edge_bps`` in that window
   (:func:`api.eol_convergence.should_suppress_phantom_edge`); independent
   signal-emitting workers (whale_flow, arb_checker, microstructure) do
   not, so this number tracks the residual exposure.

3. **Last-hour FP retrospective** — for the same final-hour alerts, join
   against the first ``market_resolutions`` row per condition_id and
   compute the false-positive rate. Target is 15% per the M8.5 guardrail.

All three results are packaged into an :class:`EolMonitorReport` and
exposed at ``GET /v1/eol-monitor``.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient

from . import eol_convergence as eol_q
from .clickhouse import get_async_client


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RampUpResult:
    """Markets currently inside the EOL convergence ramp."""

    markets_checked: int
    """Total active markets evaluated (all live markets with ``end_date``)."""
    markets_in_ramp: int
    """Markets inside the final 5% of life (weight > 0)."""
    markets_in_phantom_window: int
    """Markets inside the final hour (edge gate is engaged)."""
    mean_convergence_weight: float
    """Mean weight across markets currently inside the ramp; 0.0 if none."""
    max_convergence_weight: float
    """Max weight observed across markets currently inside the ramp."""
    sample_market_ids: list[str]
    """Up to 5 condition_ids inside the ramp, ordered by descending weight."""


@dataclass(frozen=True)
class PhantomEdgeResult:
    """Density of signal-feed alerts firing in the final hour before resolution."""

    alerts_in_final_hour: int
    """Alerts whose ``event_time`` landed within ``window_hours`` of ``end_date``."""
    alerts_per_day: float
    """``alerts_in_final_hour / lookback_days`` — operational density signal."""
    sample_condition_ids: list[str]
    """Up to 5 distinct condition_ids that fired alerts in the window."""


@dataclass(frozen=True)
class LastHourFpResult:
    """FP retrospective for last-hour edge alerts."""

    alerts_classified: int
    true_positives: int
    false_positives: int
    skipped_unresolved: int
    fp_rate: float | None
    meets_target: bool
    """Target: FP rate < 15% (PRD §8 guardrail; mirrors :mod:`api.alert_outcome_audit`)."""


@dataclass(frozen=True)
class EolMonitorReport:
    as_of: datetime
    lookback_days: int
    ramp_up: RampUpResult
    phantom_edge: PhantomEdgeResult
    last_hour_fp: LastHourFpResult


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PHANTOM_EDGE_WINDOW_HOURS = 1
_EDGE_SIGNAL_TYPES = ("whale_open", "whale_resize", "arb", "large_print", "book_shock")
_FP_TARGET = 0.15


# ---------------------------------------------------------------------------
# 1. Ramp-up check
# ---------------------------------------------------------------------------


async def check_ramp_up_on_live_markets(
    ch: AsyncClient,
    asked_at: datetime,
) -> RampUpResult:
    """Aggregate convergence-weight stats over currently live markets.

    For each active market with an ``end_date`` strictly in the future, we
    derive ``time_to_resolution_s`` (now → end_date) and ``total_life_s``
    (``MIN(observed_at)`` → end_date) directly in SQL, then run the same
    pure :func:`eol_convergence.final_life_market_mid_weight` the markets
    serve path applies. The resulting report is read-only — it never
    mutates any model state.
    """
    query = """
        SELECT
            condition_id,
            anyLast(end_date)        AS end_date,
            min(observed_at)         AS first_observed_at,
            anyLast(active)          AS active,
            anyLast(closed)          AS closed,
            anyLast(archived)        AS archived
        FROM markets_snapshots
        WHERE observed_at <= {asof:DateTime64(3)}
        GROUP BY condition_id
    """
    try:
        result = await ch.query(query, parameters={"asof": asked_at})
        rows = result.result_rows
    except Exception:
        rows = []

    weights: list[tuple[str, float]] = []
    in_phantom = 0
    markets_checked = 0
    for row in rows:
        condition_id = str(row[0])
        end_date: Any = row[1]
        first_obs: Any = row[2]
        active = bool(row[3]) if row[3] is not None else False
        closed = bool(row[4]) if row[4] is not None else False
        archived = bool(row[5]) if row[5] is not None else False
        if not active or closed or archived:
            continue
        if end_date is None or first_obs is None:
            continue
        if not hasattr(end_date, "timestamp") or not hasattr(first_obs, "timestamp"):
            continue
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=UTC)
        if first_obs.tzinfo is None:
            first_obs = first_obs.replace(tzinfo=UTC)
        if end_date <= asked_at:
            continue  # already past end_date — out of scope for live monitoring
        markets_checked += 1
        ttr_s = (end_date - asked_at).total_seconds()
        total_life_s = (end_date - first_obs).total_seconds()
        if total_life_s <= 0:
            continue
        weight = eol_q.final_life_market_mid_weight(ttr_s, total_life_s)
        if weight is None or weight <= 0.0:
            continue
        weights.append((condition_id, weight))
        if eol_q.should_suppress_phantom_edge(ttr_s):
            in_phantom += 1

    if not weights:
        return RampUpResult(
            markets_checked=markets_checked,
            markets_in_ramp=0,
            markets_in_phantom_window=0,
            mean_convergence_weight=0.0,
            max_convergence_weight=0.0,
            sample_market_ids=[],
        )

    weights.sort(key=lambda w: w[1], reverse=True)
    avg = sum(w for _, w in weights) / len(weights)
    return RampUpResult(
        markets_checked=markets_checked,
        markets_in_ramp=len(weights),
        markets_in_phantom_window=in_phantom,
        mean_convergence_weight=round(avg, 4),
        max_convergence_weight=round(weights[0][1], 4),
        sample_market_ids=[cid for cid, _ in weights[:5]],
    )


# ---------------------------------------------------------------------------
# 2. Phantom-edge density (signals that fired in the final hour)
# ---------------------------------------------------------------------------


async def check_phantom_edge_density(
    ch: AsyncClient,
    asked_at: datetime,
    *,
    lookback_days: int = 30,
    window_hours: int = _PHANTOM_EDGE_WINDOW_HOURS,
) -> PhantomEdgeResult:
    """Count signal-feed alerts firing in the final ``window_hours`` of life.

    Joins ``signal_events`` against the latest ``markets_snapshots`` row per
    condition_id (PIT-correct via ``observed_at <= asked_at``). Only edge
    signal types are counted (whale, arb, large prints, book shocks); event
    notifications and pure macro events are excluded.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    lookback_start = asked_at - timedelta(days=lookback_days)

    query = """
        SELECT se.condition_id
        FROM signal_events se
        INNER JOIN (
            SELECT condition_id, anyLast(end_date) AS end_date
            FROM markets_snapshots
            WHERE observed_at <= {asof:DateTime64(3)}
            GROUP BY condition_id
        ) ms ON se.condition_id = ms.condition_id
        WHERE se.event_type IN {types:Array(String)}
          AND se.observed_at >= {start:DateTime64(3)}
          AND se.observed_at <= {asof:DateTime64(3)}
          AND ms.end_date IS NOT NULL
          AND (toUnixTimestamp(ms.end_date) - toUnixTimestamp(se.event_time))
              BETWEEN 0 AND {window_s:Int64}
        ORDER BY se.event_time DESC
        LIMIT 5000
    """
    try:
        result = await ch.query(
            query,
            parameters={
                "asof": asked_at,
                "start": lookback_start,
                "types": list(_EDGE_SIGNAL_TYPES),
                "window_s": int(window_hours * 3600),
            },
        )
        rows = result.result_rows
    except Exception:
        rows = []

    total = len(rows)
    seen: list[str] = []
    seen_set: set[str] = set()
    for row in rows:
        cid = str(row[0])
        if cid not in seen_set:
            seen_set.add(cid)
            seen.append(cid)
            if len(seen) >= 5:
                break

    per_day = total / lookback_days
    return PhantomEdgeResult(
        alerts_in_final_hour=total,
        alerts_per_day=round(per_day, 4),
        sample_condition_ids=seen,
    )


# ---------------------------------------------------------------------------
# 3. Last-hour FP retrospective
# ---------------------------------------------------------------------------


async def run_eol_fp_retrospective(
    ch: AsyncClient,
    asked_at: datetime,
    *,
    lookback_days: int = 30,
    window_hours: int = _PHANTOM_EDGE_WINDOW_HOURS,
    min_samples: int = 10,
) -> LastHourFpResult:
    """Compute the empirical FP rate for alerts fired in the final hour.

    An alert is a *false positive* when the directional signal (``yes`` or
    ``no``) disagrees with the actual binary resolution outcome. The first
    ``market_resolutions`` row per condition_id is the ground truth — UMA
    re-resolutions arrive as new rows, but we use the first to keep the
    audit aligned with the M6.1 PIT contract.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    lookback_start = asked_at - timedelta(days=lookback_days)

    query = """
        SELECT
            se.condition_id,
            se.direction,
            mr.outcome
        FROM signal_events se
        INNER JOIN (
            SELECT condition_id, anyLast(end_date) AS end_date
            FROM markets_snapshots
            WHERE observed_at <= {asof:DateTime64(3)}
            GROUP BY condition_id
        ) ms ON se.condition_id = ms.condition_id
        INNER JOIN (
            SELECT condition_id, outcome
            FROM market_resolutions
            WHERE observed_at <= {asof:DateTime64(3)}
            ORDER BY condition_id, observed_at ASC
            LIMIT 1 BY condition_id
        ) mr ON se.condition_id = mr.condition_id
        WHERE se.event_type IN {types:Array(String)}
          AND se.observed_at >= {start:DateTime64(3)}
          AND se.observed_at <= {asof:DateTime64(3)}
          AND ms.end_date IS NOT NULL
          AND (toUnixTimestamp(ms.end_date) - toUnixTimestamp(se.event_time))
              BETWEEN 0 AND {window_s:Int64}
          AND lower(mr.outcome) IN ('yes', 'no')
          AND lower(se.direction) IN ('yes', 'no')
        LIMIT 5000
    """
    try:
        result = await ch.query(
            query,
            parameters={
                "asof": asked_at,
                "start": lookback_start,
                "types": list(_EDGE_SIGNAL_TYPES),
                "window_s": int(window_hours * 3600),
            },
        )
        rows = result.result_rows
    except Exception:
        rows = []

    tp = 0
    fp = 0
    for row in rows:
        direction = str(row[1] or "").lower()
        outcome = str(row[2] or "").lower()
        if direction == outcome:
            tp += 1
        else:
            fp += 1

    classified = tp + fp
    fp_rate: float | None = fp / classified if classified >= min_samples else None
    meets = (fp_rate is not None) and (fp_rate < _FP_TARGET)

    return LastHourFpResult(
        alerts_classified=classified,
        true_positives=tp,
        false_positives=fp,
        skipped_unresolved=len(rows) - classified,
        fp_rate=round(fp_rate, 4) if fp_rate is not None else None,
        meets_target=meets,
    )


# ---------------------------------------------------------------------------
# Composite runner
# ---------------------------------------------------------------------------


async def run_eol_monitor(
    ch: AsyncClient,
    *,
    asked_at: datetime | None = None,
    lookback_days: int = 30,
) -> EolMonitorReport:
    as_of = asked_at or datetime.now(tz=UTC)
    ramp_up, phantom, fp_retro = await asyncio.gather(
        check_ramp_up_on_live_markets(ch, as_of),
        check_phantom_edge_density(ch, as_of, lookback_days=lookback_days),
        run_eol_fp_retrospective(ch, as_of, lookback_days=lookback_days),
    )
    return EolMonitorReport(
        as_of=as_of,
        lookback_days=lookback_days,
        ramp_up=ramp_up,
        phantom_edge=phantom,
        last_hour_fp=fp_retro,
    )


async def _main() -> None:
    ch = await get_async_client()
    try:
        report = await run_eol_monitor(ch)

        def _serial(obj: object) -> object:
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, float) and math.isnan(obj):
                return None
            raise TypeError(f"not serialisable: {type(obj)}")

        print(json.dumps(asdict(report), indent=2, default=_serial))
    finally:
        await ch.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
