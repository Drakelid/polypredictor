"""As-of reads over ``onchain_metrics`` (Glassnode/Dune ingestion landings).

The ingestion path lives in :mod:`ingest.workers.onchain_metrics` and lands
rows into ClickHouse with PIT-correct ``period_date`` and ``observed_at``.
This module is the only sanctioned read path for downstream consumers — it
filters by ``observed_at <= asked_at`` so callers cannot read a value that
hadn't been published yet.

Two access shapes are supported:

* :func:`latest_onchain_metric_asof` — single most-recent observation for one
  ``(source, metric_id, asset)`` tuple at the given as-of time. Useful when a
  feature pipeline needs the freshest known value at prediction time.
* :func:`list_onchain_metrics` — recent feed across one or more
  source/asset/category filters, ordered by event_time descending. Powers the
  ``/v1/onchain-metrics`` endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class OnchainMetricRow:
    source: str
    metric_id: str
    metric_title: str
    asset: str
    chain: str
    category: str
    period_date: datetime
    value: float | None
    units: str
    event_time: datetime
    observed_at: datetime


_SELECT = (
    "source, metric_id, metric_title, asset, chain, category, "
    "period_date, value, units, event_time, observed_at"
)


def _row_to_metric(r: tuple[object, ...]) -> OnchainMetricRow:
    period_raw = r[6]
    if isinstance(period_raw, datetime):
        period_date = period_raw
    else:
        # ClickHouse Date columns return ``datetime.date``; widen to UTC midnight.
        period_date = datetime(period_raw.year, period_raw.month, period_raw.day)
    return OnchainMetricRow(
        source=str(r[0]),
        metric_id=str(r[1]),
        metric_title=str(r[2]),
        asset=str(r[3]),
        chain=str(r[4]),
        category=str(r[5]),
        period_date=period_date,
        value=None if r[7] is None else float(r[7]),
        units=str(r[8]),
        event_time=r[9],  # type: ignore[arg-type]
        observed_at=r[10],  # type: ignore[arg-type]
    )


async def latest_onchain_metric_asof(
    ch: AsyncClient,
    *,
    source: str,
    metric_id: str,
    asset: str,
    asked_at: datetime,
) -> OnchainMetricRow | None:
    """Latest observation known at ``asked_at`` for a single metric.

    Picks the row with the largest ``period_date`` whose ``observed_at`` is
    on-or-before ``asked_at``; ties on ``period_date`` resolve to the latest
    ``observed_at`` so revisions win.
    """
    query = f"""
        SELECT {_SELECT}
        FROM onchain_metrics
        WHERE source = {{src:String}}
          AND metric_id = {{mid:String}}
          AND asset = {{ast:String}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY period_date DESC, observed_at DESC
        LIMIT 1
    """
    result = await ch.query(
        query,
        parameters={
            "src": source,
            "mid": metric_id,
            "ast": asset,
            "asof": asked_at,
        },
    )
    rows = result.result_rows
    if not rows:
        return None
    return _row_to_metric(rows[0])


async def list_onchain_metrics(
    ch: AsyncClient,
    *,
    asked_at: datetime,
    lookback_hours: int = 7 * 24,
    limit: int = 200,
    sources: list[str] | None = None,
    assets: list[str] | None = None,
    categories: list[str] | None = None,
    metric_ids: list[str] | None = None,
) -> list[OnchainMetricRow]:
    """Recent on-chain observations known at ``asked_at``.

    For each ``(source, metric_id, asset, period_date)`` natural key we keep
    the latest ``observed_at`` so revisions surface in place of stale rows.
    Within the trailing ``lookback_hours`` window, results are ordered by
    ``period_date`` descending.
    """
    cutoff = asked_at - timedelta(hours=lookback_hours)
    parts = [
        f"SELECT {_SELECT}",
        "FROM onchain_metrics",
        "WHERE observed_at <= {asof:DateTime64(3)}",
        "  AND period_date >= toDate({cutoff:DateTime64(3)})",
    ]
    params: dict[str, object] = {
        "asof": asked_at,
        "cutoff": cutoff,
        "lim": int(limit),
    }
    if sources:
        parts.append("  AND source IN {sources:Array(String)}")
        params["sources"] = sources
    if assets:
        parts.append("  AND asset IN {assets:Array(String)}")
        params["assets"] = [a.upper() for a in assets]
    if categories:
        parts.append("  AND category IN {categories:Array(String)}")
        params["categories"] = [c.lower() for c in categories]
    if metric_ids:
        parts.append("  AND metric_id IN {metric_ids:Array(String)}")
        params["metric_ids"] = metric_ids
    parts.append(
        "ORDER BY source, metric_id, asset, period_date DESC, observed_at DESC"
    )
    parts.append(
        "LIMIT 1 BY source, metric_id, asset, period_date"
    )
    parts.append("LIMIT {lim:UInt32}")
    query = "\n".join(parts)
    result = await ch.query(query, parameters=params)
    rows = [_row_to_metric(r) for r in result.result_rows]
    rows.sort(key=lambda r: r.period_date, reverse=True)
    return rows
