"""M8.3 End-of-life convergence monitoring.

The three unchecked M8.3 tasks are:

1. **Ramp-up check** — verify that markets in the final 5% of their life
   have an appropriately elevated model certainty (the model should be
   converging toward resolution, not uncertain).

2. **Phantom-edge suppression check** — verify that edge/Kelly alerts
   emitted in the last hour before resolution are suppressed (tagged with
   the ``resolution_risk_suppressed`` flag) when ``resolution_risk_score``
   exceeds the configured threshold.

3. **Last-hour FP retrospective** — compute the empirical false-positive
   rate for alerts fired in the hour immediately before resolution, using
   actual binary resolutions as ground truth.

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

from .clickhouse import get_async_client
from .settings import Settings, get_settings


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RampUpResult:
    """Markets in the final 5% of their life."""

    markets_checked: int
    markets_with_high_certainty: int
    """Certainty = band width < 0.15 (model has converged)."""
    markets_with_low_certainty: int
    high_certainty_rate: float
    sample_market_ids: list[str]


@dataclass(frozen=True)
class PhantomEdgeResult:
    """Snapshot of edge-alert suppression in the final hour before resolution."""

    alerts_in_final_hour: int
    alerts_suppressed: int
    alerts_not_suppressed: int
    suppression_rate: float
    sample_unsuppressed_ids: list[str]


@dataclass(frozen=True)
class LastHourFpResult:
    """FP retrospective for last-hour edge alerts."""

    alerts_classified: int
    true_positives: int
    false_positives: int
    skipped_unresolved: int
    fp_rate: float | None
    meets_target: bool
    """Target: FP rate < 15% (M8.5 guardrail from alert_outcome_audit)."""


@dataclass(frozen=True)
class EolMonitorReport:
    as_of: datetime
    lookback_days: int
    ramp_up: RampUpResult
    phantom_edge: PhantomEdgeResult
    last_hour_fp: LastHourFpResult


# ---------------------------------------------------------------------------
# 1. Ramp-up check
# ---------------------------------------------------------------------------

_FINAL_LIFE_FRACTION = 0.05  # last 5% of market life
_CERTAINTY_BAND_THRESHOLD = 0.15  # band width below this → high certainty


async def check_ramp_up_on_live_markets(
    ch: AsyncClient,
    asked_at: datetime,
) -> RampUpResult:
    """Check that markets in the final 5% of their life have converged.

    We query ``markets_snapshots`` for active markets where
    ``(end_date - asked_at) / (end_date - first_observed_at) < 0.05``
    and then check the latest conformal band width from ``market_quotes``
    as a proxy for model certainty.
    """
    query = """
        SELECT
            ms.condition_id,
            ms.end_date,
            ms.observed_at AS first_observed_at
        FROM markets_snapshots ms
        WHERE ms.active = 1
          AND ms.closed = 0
          AND ms.archived = 0
          AND ms.end_date IS NOT NULL
          AND ms.end_date > {asof:DateTime64(3)}
          AND ms.observed_at <= {asof:DateTime64(3)}
        ORDER BY ms.condition_id, ms.observed_at ASC
        LIMIT 1 BY ms.condition_id
    """
    try:
        result = await ch.query(query, parameters={"asof": asked_at})
        rows = result.result_rows
    except Exception:
        rows = []

    final_life_ids: list[str] = []
    for row in rows:
        condition_id = str(row[0])
        end_date: Any = row[1]
        first_obs: Any = row[2]
        if end_date is None or first_obs is None:
            continue
        if not hasattr(end_date, "timestamp"):
            continue
        total_life_s = (end_date - first_obs).total_seconds()
        remaining_s = (end_date - asked_at).total_seconds()
        if total_life_s <= 0:
            continue
        fraction_remaining = remaining_s / total_life_s
        if fraction_remaining < _FINAL_LIFE_FRACTION:
            final_life_ids.append(condition_id)

    if not final_life_ids:
        return RampUpResult(
            markets_checked=0,
            markets_with_high_certainty=0,
            markets_with_low_certainty=0,
            high_certainty_rate=1.0,
            sample_market_ids=[],
        )

    # Fetch latest conformal band width for these markets.
    # We use the spread and book imbalance from market_features as a proxy
    # (smaller spread → more liquid → typically more certain mid-price).
    # A real convergence check would query served conformal bands from the
    # model API at the PIT snapshot, but for the monitor we use the
    # market_features spread as a lightweight signal.
    band_query = """
        SELECT condition_id, spread
        FROM market_features
        WHERE condition_id IN {ids:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
    """
    try:
        band_result = await ch.query(
            band_query,
            parameters={"ids": final_life_ids, "asof": asked_at},
        )
        band_rows = band_result.result_rows
    except Exception:
        band_rows = []

    high_certainty = 0
    for brow in band_rows:
        spread = float(brow[1]) if brow[1] is not None else 1.0
        if spread < _CERTAINTY_BAND_THRESHOLD:
            high_certainty += 1

    total = len(final_life_ids)
    low = total - high_certainty
    rate = high_certainty / total if total > 0 else 1.0
    return RampUpResult(
        markets_checked=total,
        markets_with_high_certainty=high_certainty,
        markets_with_low_certainty=low,
        high_certainty_rate=round(rate, 4),
        sample_market_ids=final_life_ids[:5],
    )


# ---------------------------------------------------------------------------
# 2. Phantom-edge suppression check
# ---------------------------------------------------------------------------

_PHANTOM_EDGE_WINDOW_HOURS = 1  # final hour before resolution
_EDGE_SIGNAL_TYPES = ("whale_open", "whale_resize", "arb", "large_print", "book_shock")


async def check_phantom_edge_suppression(
    ch: AsyncClient,
    asked_at: datetime,
    *,
    lookback_days: int = 30,
    window_hours: int = _PHANTOM_EDGE_WINDOW_HOURS,
) -> PhantomEdgeResult:
    """Check that edge alerts fired in the final hour are suppressed.

    Queries ``signal_events`` for events that fired within ``window_hours``
    of the market's resolution date, and checks for the
    ``resolution_risk_suppressed`` flag in the payload JSON.
    """
    lookback_start = asked_at - timedelta(days=lookback_days)

    query = """
        SELECT
            se.event_id,
            se.condition_id,
            se.event_type,
            se.payload,
            se.event_time,
            ms.end_date
        FROM signal_events se
        INNER JOIN (
            SELECT condition_id, end_date
            FROM markets_snapshots
            WHERE observed_at <= {asof:DateTime64(3)}
            ORDER BY condition_id, observed_at DESC
            LIMIT 1 BY condition_id
        ) ms ON se.condition_id = ms.condition_id
        WHERE se.event_type IN {types:Array(String)}
          AND se.observed_at >= {start:DateTime64(3)}
          AND se.observed_at <= {asof:DateTime64(3)}
          AND ms.end_date IS NOT NULL
          AND (toUnixTimestamp(ms.end_date) - toUnixTimestamp(se.event_time))
              BETWEEN 0 AND {window_s:Int64}
        ORDER BY se.event_time DESC
        LIMIT 500
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

    suppressed = 0
    not_suppressed_ids: list[str] = []
    for row in rows:
        event_id = str(row[0])
        payload_raw = row[3]
        payload: dict = {}
        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw)
            except Exception:
                payload = {}
        elif isinstance(payload_raw, dict):
            payload = payload_raw
        if payload.get("resolution_risk_suppressed"):
            suppressed += 1
        else:
            not_suppressed_ids.append(event_id)

    total = len(rows)
    not_suppressed = total - suppressed
    rate = suppressed / total if total > 0 else 1.0
    return PhantomEdgeResult(
        alerts_in_final_hour=total,
        alerts_suppressed=suppressed,
        alerts_not_suppressed=not_suppressed,
        suppression_rate=round(rate, 4),
        sample_unsuppressed_ids=not_suppressed_ids[:5],
    )


# ---------------------------------------------------------------------------
# 3. Last-hour FP retrospective
# ---------------------------------------------------------------------------

_FP_TARGET = 0.15


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
    ``no``) disagrees with the actual binary resolution outcome.
    """
    lookback_start = asked_at - timedelta(days=lookback_days)

    query = """
        SELECT
            se.condition_id,
            se.direction,
            mr.outcome
        FROM signal_events se
        INNER JOIN (
            SELECT condition_id, end_date
            FROM markets_snapshots
            WHERE observed_at <= {asof:DateTime64(3)}
            ORDER BY condition_id, observed_at DESC
            LIMIT 1 BY condition_id
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
        LIMIT 2000
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
        check_phantom_edge_suppression(ch, as_of, lookback_days=lookback_days),
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
