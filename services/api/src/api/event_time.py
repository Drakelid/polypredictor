"""Separate event-time reads over scheduled catalyst events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient

from . import asof as asof_q
from .classifications import classification_asof


@dataclass(frozen=True)
class ScheduledEventRow:
    event_kind: str
    source: str
    source_id: str
    title: str
    event_time: datetime
    observed_at: datetime
    release_time: datetime | None
    consensus_value: float | None
    actual_value: float | None
    surprise_value: float | None
    value_unit: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class EventTimeContext:
    condition_id: str
    next_event: ScheduledEventRow | None
    previous_event: ScheduledEventRow | None
    hours_to_next_event: float | None
    hours_since_previous_event: float | None
    in_event_window_24h: bool
    event_asset: str | None
    pre_event_atm_iv_now: float | None
    pre_event_atm_iv_24h_ago: float | None
    pre_event_iv_runup_24h: float | None
    post_event_atm_iv_pre_1h: float | None
    post_event_atm_iv_24h: float | None
    post_event_iv_crush_pre_1h_to_24h: float | None
    post_event_mid_1h: float | None
    post_event_mid_24h: float | None
    post_event_drift_1h_to_24h: float | None


_SCHEDULED_EVENT_KINDS = {"macro", "onchain"}
_SUPPORTED_IV_ASSETS = {"BTC", "ETH", "SOL"}


@dataclass(frozen=True)
class DeribitIvSurfaceRow:
    currency: str
    expiry_date: datetime
    atm_iv: float
    observed_at: datetime


def _parse_metadata(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_scheduled_event(event_kind: str, metadata: dict[str, Any]) -> bool:
    return event_kind.lower() in _SCHEDULED_EVENT_KINDS and bool(metadata.get("scheduled"))


def _float_or_none(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _datetime_from_metadata(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


async def scheduled_events_asof(
    ch: AsyncClient,
    condition_id: str,
    asked_at: datetime,
    *,
    lookback_days: int = 30,
    lookahead_days: int = 60,
) -> list[ScheduledEventRow]:
    start = asked_at - timedelta(days=lookback_days)
    end = asked_at + timedelta(days=lookahead_days)
    query = """
        SELECT
            event_kind,
            source,
            source_id,
            title,
            metadata,
            event_time,
            observed_at
        FROM (
            SELECT
                event_kind,
                source,
                source_id,
                title,
                metadata,
                event_time,
                observed_at
            FROM external_events
            WHERE event_kind IN ('macro', 'onchain')
              AND has(related_markets, {cond:String})
              AND event_time >= {start:DateTime64(3)}
              AND event_time <= {end:DateTime64(3)}
              AND observed_at <= {asof:DateTime64(3)}
            ORDER BY source, source_id, observed_at DESC
            LIMIT 1 BY source, source_id
        )
        ORDER BY event_time ASC
    """
    result = await ch.query(
        query,
        parameters={"cond": condition_id, "start": start, "end": end, "asof": asked_at},
    )
    rows = [_row_to_scheduled_event(row) for row in result.result_rows]
    return [row for row in rows if _is_scheduled_event(row.event_kind, row.metadata)]


async def event_time_context_asof(
    ch: AsyncClient,
    condition_id: str,
    asked_at: datetime,
) -> EventTimeContext:
    events = await scheduled_events_asof(ch, condition_id, asked_at)
    classification = await classification_asof(ch, condition_id, asked_at)
    event_asset = None
    if classification is not None and classification.features.asset in _SUPPORTED_IV_ASSETS:
        event_asset = classification.features.asset
    next_event = next((event for event in events if event.event_time >= asked_at), None)
    previous_events = [event for event in events if event.event_time <= asked_at]
    previous_event = previous_events[-1] if previous_events else None
    hours_to_next_event = None
    if next_event is not None:
        hours_to_next_event = (next_event.event_time - asked_at).total_seconds() / 3600.0
    hours_since_previous_event = None
    if previous_event is not None:
        hours_since_previous_event = (asked_at - previous_event.event_time).total_seconds() / 3600.0
    in_event_window = False
    if hours_to_next_event is not None and abs(hours_to_next_event) <= 24.0:
        in_event_window = True
    if hours_since_previous_event is not None and hours_since_previous_event <= 24.0:
        in_event_window = True
    pre_event_atm_iv_now = None
    pre_event_atm_iv_24h_ago = None
    pre_event_iv_runup = None
    if next_event is not None and event_asset is not None:
        (
            pre_event_atm_iv_now,
            pre_event_atm_iv_24h_ago,
            pre_event_iv_runup,
        ) = await _pre_event_iv_runup(
            ch,
            currency=event_asset,
            asked_at=asked_at,
            event_time=next_event.event_time,
        )
    post_event_atm_iv_pre_1h = None
    post_event_atm_iv_24h = None
    post_event_iv_crush = None
    if previous_event is not None and event_asset is not None and asked_at >= previous_event.event_time + timedelta(hours=24):
        (
            post_event_atm_iv_pre_1h,
            post_event_atm_iv_24h,
            post_event_iv_crush,
        ) = await _post_event_iv_crush(
            ch,
            currency=event_asset,
            asked_at=asked_at,
            event_time=previous_event.event_time,
        )
    post_event_mid_1h = None
    post_event_mid_24h = None
    post_event_drift = None
    if previous_event is not None and asked_at >= previous_event.event_time + timedelta(hours=24):
        post_event_mid_1h, post_event_mid_24h, post_event_drift = await _post_event_drift(
            ch,
            condition_id=condition_id,
            asked_at=asked_at,
            event_time=previous_event.event_time,
        )
    return EventTimeContext(
        condition_id=condition_id,
        next_event=next_event,
        previous_event=previous_event,
        hours_to_next_event=hours_to_next_event,
        hours_since_previous_event=hours_since_previous_event,
        in_event_window_24h=in_event_window,
        event_asset=event_asset,
        pre_event_atm_iv_now=pre_event_atm_iv_now,
        pre_event_atm_iv_24h_ago=pre_event_atm_iv_24h_ago,
        pre_event_iv_runup_24h=pre_event_iv_runup,
        post_event_atm_iv_pre_1h=post_event_atm_iv_pre_1h,
        post_event_atm_iv_24h=post_event_atm_iv_24h,
        post_event_iv_crush_pre_1h_to_24h=post_event_iv_crush,
        post_event_mid_1h=post_event_mid_1h,
        post_event_mid_24h=post_event_mid_24h,
        post_event_drift_1h_to_24h=post_event_drift,
    )


async def _nearest_deribit_iv_surface_asof(
    ch: AsyncClient,
    *,
    currency: str,
    target_event_time: datetime,
    asked_at: datetime,
) -> DeribitIvSurfaceRow | None:
    result = await ch.query(
        """
        SELECT currency, toDateTime(expiry_date), atm_iv, observed_at
        FROM deribit_iv_surface
        WHERE currency = {currency:String}
          AND expiry_date >= toDate({target_event_time:DateTime64(3)})
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY expiry_date ASC, observed_at DESC
        LIMIT 1
        """,
        parameters={
            "currency": currency,
            "target_event_time": target_event_time,
            "asof": asked_at,
        },
    )
    rows = result.result_rows
    if not rows:
        return None
    row = rows[0]
    return DeribitIvSurfaceRow(
        currency=str(row[0]),
        expiry_date=row[1],  # type: ignore[arg-type]
        atm_iv=float(row[2]),
        observed_at=row[3],  # type: ignore[arg-type]
    )


async def _pre_event_iv_runup(
    ch: AsyncClient,
    *,
    currency: str,
    asked_at: datetime,
    event_time: datetime,
) -> tuple[float | None, float | None, float | None]:
    current = await _nearest_deribit_iv_surface_asof(
        ch,
        currency=currency,
        target_event_time=event_time,
        asked_at=asked_at,
    )
    prior = await _nearest_deribit_iv_surface_asof(
        ch,
        currency=currency,
        target_event_time=event_time,
        asked_at=asked_at - timedelta(hours=24),
    )
    current_iv = current.atm_iv if current is not None else None
    prior_iv = prior.atm_iv if prior is not None else None
    if current_iv is None or prior_iv is None:
        return current_iv, prior_iv, None
    return current_iv, prior_iv, current_iv - prior_iv


async def _post_event_iv_crush(
    ch: AsyncClient,
    *,
    currency: str,
    asked_at: datetime,
    event_time: datetime,
) -> tuple[float | None, float | None, float | None]:
    pre = await _nearest_deribit_iv_surface_asof(
        ch,
        currency=currency,
        target_event_time=event_time,
        asked_at=event_time - timedelta(hours=1),
    )
    post = await _nearest_deribit_iv_surface_asof(
        ch,
        currency=currency,
        target_event_time=event_time,
        asked_at=min(asked_at, event_time + timedelta(hours=24)),
    )
    pre_iv = pre.atm_iv if pre is not None else None
    post_iv = post.atm_iv if post is not None else None
    if pre_iv is None or post_iv is None:
        return pre_iv, post_iv, None
    return pre_iv, post_iv, post_iv - pre_iv


async def _post_event_drift(
    ch: AsyncClient,
    *,
    condition_id: str,
    asked_at: datetime,
    event_time: datetime,
) -> tuple[float | None, float | None, float | None]:
    snapshot = await asof_q.latest_market_snapshot_asof(ch, condition_id, asked_at)
    if snapshot is None or not snapshot.token_ids:
        return (None, None, None)
    token_id = str(snapshot.token_ids[0])
    quote_1h = await asof_q.latest_quote_asof(ch, token_id, event_time + timedelta(hours=1))
    quote_24h = await asof_q.latest_quote_asof(ch, token_id, event_time + timedelta(hours=24))
    mid_1h = quote_1h.mid if quote_1h is not None else None
    mid_24h = quote_24h.mid if quote_24h is not None else None
    if mid_1h is None or mid_24h is None:
        return (mid_1h, mid_24h, None)
    return (mid_1h, mid_24h, mid_24h - mid_1h)


def _row_to_scheduled_event(row: tuple[object, ...]) -> ScheduledEventRow:
    metadata = _parse_metadata(row[4])
    event_time = row[5]  # type: ignore[assignment]
    release_time = _datetime_from_metadata(metadata.get("release_time")) or event_time
    consensus_value = _float_or_none(metadata.get("consensus_value"))
    actual_value = _float_or_none(metadata.get("actual_value"))
    surprise_value = None
    if consensus_value is not None and actual_value is not None:
        surprise_value = actual_value - consensus_value
    return ScheduledEventRow(
        event_kind=str(row[0]),
        source=str(row[1]),
        source_id=str(row[2]),
        title=str(row[3]),
        release_time=release_time,
        consensus_value=consensus_value,
        actual_value=actual_value,
        surprise_value=surprise_value,
        value_unit=str(metadata.get("value_unit") or "").strip() or None,
        metadata=metadata,
        event_time=event_time,
        observed_at=row[6],  # type: ignore[arg-type]
    )
