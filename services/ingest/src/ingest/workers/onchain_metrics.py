"""Glassnode + Dune on-chain ingestion (M5).

Pulls a configurable list of on-chain metrics from two upstreams whose free
tiers cover the families called out in PRD §5.2 (exchange in/outflows,
stablecoin supply, whale-wallet activity):

* **Glassnode** ``/v1/metrics/{category}/{metric}`` — REST endpoint that
  returns ``[{"t": <unix_seconds>, "v": <float>}, ...]`` for a configured
  asset/interval. Requires an ``api_key`` query param; the free tier exposes
  a curated subset of metrics (e.g. ``transactions/transfers_volume_to_exchanges_sum``).
* **Dune** ``/api/v1/query/{query_id}/results`` — REST endpoint that returns
  the latest execution of an arbitrary SQL query as ``{result: {rows: [...]}}``.
  We map per-query column names to ``period_date`` / ``value`` / ``asset``
  via a config file so a single worker can ingest any saved Dune query.

Each row carries ``period_date`` (the date the observation describes) and
``observed_at`` (when we learned). Restatements arrive as new rows at later
``observed_at`` so the PIT contract holds end-to-end.

If neither Glassnode nor Dune is configured (no API key, or no entries in
either config file), the worker no-ops cleanly so the ingest service stays
green out-of-the-box.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

from ..clickhouse import get_async_client
from ..settings import get_settings
from ..writers import ONCHAIN_METRICS_COLS, onchain_metric_row, utcnow

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GlassnodeMetricConfig:
    metric: str  # e.g. "transactions/transfers_volume_to_exchanges_sum"
    asset: str  # e.g. "BTC"
    title: str
    units: str = ""
    interval: str = "24h"
    chain: str = ""
    category: str = "misc"  # 'flow' | 'supply' | 'whale' | 'misc'

    @property
    def metric_id(self) -> str:
        return self.metric


@dataclass(frozen=True)
class DuneQueryConfig:
    query_id: int
    metric_id: str
    title: str
    asset: str = ""
    chain: str = ""
    category: str = "misc"
    units: str = ""
    date_field: str = "day"
    value_field: str = "value"
    asset_field: str | None = None  # if set, overrides the static asset


@dataclass(frozen=True)
class OnchainObservation:
    source: str
    metric_id: str
    metric_title: str
    asset: str
    chain: str
    category: str
    period_date: datetime
    value: float | None
    units: str


def _load_entries(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    series = raw.get("metrics", []) if isinstance(raw, dict) else []
    return [entry for entry in series if isinstance(entry, dict)]


def load_glassnode_config(path: str | None) -> list[GlassnodeMetricConfig]:
    out: list[GlassnodeMetricConfig] = []
    for entry in _load_entries(path):
        metric = str(entry.get("metric") or "").strip().strip("/")
        asset = str(entry.get("asset") or "").strip().upper()
        if not metric or not asset:
            continue
        out.append(
            GlassnodeMetricConfig(
                metric=metric,
                asset=asset,
                title=str(entry.get("title") or metric),
                units=str(entry.get("units") or ""),
                interval=str(entry.get("interval") or "24h"),
                chain=str(entry.get("chain") or "").lower(),
                category=str(entry.get("category") or "misc").lower(),
            )
        )
    return out


def load_dune_config(path: str | None) -> list[DuneQueryConfig]:
    out: list[DuneQueryConfig] = []
    for entry in _load_entries(path):
        try:
            query_id = int(entry.get("query_id"))
        except (TypeError, ValueError):
            continue
        metric_id = str(entry.get("metric_id") or "").strip()
        if not metric_id:
            continue
        out.append(
            DuneQueryConfig(
                query_id=query_id,
                metric_id=metric_id,
                title=str(entry.get("title") or metric_id),
                asset=str(entry.get("asset") or "").upper(),
                chain=str(entry.get("chain") or "").lower(),
                category=str(entry.get("category") or "misc").lower(),
                units=str(entry.get("units") or ""),
                date_field=str(entry.get("date_field") or "day"),
                value_field=str(entry.get("value_field") or "value"),
                asset_field=(
                    str(entry["asset_field"]) if entry.get("asset_field") else None
                ),
            )
        )
    return out


def _coerce_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def _coerce_period_date(raw: Any) -> datetime | None:
    """Accept ISO date / datetime strings, unix seconds, or unix milliseconds."""

    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # Heuristic: anything past ~year 5000 in seconds is actually ms.
        seconds = float(raw)
        if seconds > 1e12:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=UTC)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        # Try ISO-8601 first; tolerate the trailing 'Z'.
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
        try:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def parse_glassnode_payload(
    payload: list[dict[str, Any]] | dict[str, Any],
    *,
    config: GlassnodeMetricConfig,
) -> list[OnchainObservation]:
    """Parse a Glassnode ``/metrics`` response into typed observations.

    The standard response is a list of ``{"t": <unix>, "v": <float>}`` items
    (or ``{"t": ..., "o": {...}}`` for OHLC-style endpoints, in which case we
    pull the close ``c`` if present). Anything else returns no rows.
    """

    items: list[dict[str, Any]]
    if isinstance(payload, list):
        items = [p for p in payload if isinstance(p, dict)]
    else:
        return []

    out: list[OnchainObservation] = []
    for item in items:
        period = _coerce_period_date(item.get("t"))
        if period is None:
            continue
        raw_value: Any
        if "v" in item:
            raw_value = item.get("v")
        elif isinstance(item.get("o"), dict):
            o = item["o"]
            raw_value = o.get("c") or o.get("close") or o.get("v")
        else:
            raw_value = None
        value = _coerce_float(raw_value)
        out.append(
            OnchainObservation(
                source="glassnode",
                metric_id=config.metric_id,
                metric_title=config.title,
                asset=config.asset,
                chain=config.chain,
                category=config.category,
                period_date=period,
                value=value,
                units=config.units,
            )
        )
    return out


def parse_dune_payload(
    payload: dict[str, Any],
    *,
    config: DuneQueryConfig,
) -> list[OnchainObservation]:
    """Parse a Dune ``/query/{id}/results`` response into typed observations.

    The Dune v1 schema is ``{"result": {"rows": [{...}, ...]}}``; we map the
    configured ``date_field`` and ``value_field`` to our schema and optionally
    pull a per-row asset out of ``asset_field``.
    """

    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    rows = result.get("rows")
    if not isinstance(rows, list):
        return []

    out: list[OnchainObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        period = _coerce_period_date(row.get(config.date_field))
        if period is None:
            continue
        value = _coerce_float(row.get(config.value_field))
        asset = config.asset
        if config.asset_field:
            override = row.get(config.asset_field)
            if isinstance(override, str) and override.strip():
                asset = override.strip().upper()
        out.append(
            OnchainObservation(
                source="dune",
                metric_id=config.metric_id,
                metric_title=config.title,
                asset=asset,
                chain=config.chain,
                category=config.category,
                period_date=period,
                value=value,
                units=config.units,
            )
        )
    return out


async def fetch_glassnode_metric(
    client: httpx.AsyncClient,
    config: GlassnodeMetricConfig,
    *,
    base_url: str,
    api_key: str,
) -> list[OnchainObservation]:
    url = f"{base_url.rstrip('/')}/metrics/{config.metric}"
    response = await client.get(
        url,
        params={
            "a": config.asset,
            "i": config.interval,
            "api_key": api_key,
            "f": "JSON",
        },
    )
    response.raise_for_status()
    return parse_glassnode_payload(response.json(), config=config)


async def fetch_dune_query(
    client: httpx.AsyncClient,
    config: DuneQueryConfig,
    *,
    base_url: str,
    api_key: str,
) -> list[OnchainObservation]:
    url = f"{base_url.rstrip('/')}/query/{config.query_id}/results"
    response = await client.get(url, headers={"X-Dune-API-Key": api_key})
    response.raise_for_status()
    return parse_dune_payload(response.json(), config=config)


async def run_once(
    *,
    http_client: httpx.AsyncClient | None = None,
    glassnode_configs: list[GlassnodeMetricConfig] | None = None,
    dune_configs: list[DuneQueryConfig] | None = None,
) -> int:
    settings = get_settings()
    if glassnode_configs is None:
        glassnode_configs = (
            load_glassnode_config(settings.onchain_metrics_glassnode_config_file)
            if settings.glassnode_api_key
            else []
        )
    if dune_configs is None:
        dune_configs = (
            load_dune_config(settings.onchain_metrics_dune_config_file)
            if settings.dune_api_key
            else []
        )
    total_metrics = len(glassnode_configs) + len(dune_configs)
    if total_metrics == 0:
        log.info("onchain_metrics.no_metrics_configured")
        return 0

    own_client = http_client is None
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=settings.onchain_metrics_timeout_s)

    ch = await get_async_client()
    observed_at = utcnow()
    rows: list[tuple[Any, ...]] = []
    try:
        for config in glassnode_configs:
            try:
                observations = await fetch_glassnode_metric(
                    http_client,
                    config,
                    base_url=settings.glassnode_base,
                    api_key=settings.glassnode_api_key or "",
                )
            except (httpx.HTTPError, ValueError) as exc:
                log.warning(
                    "onchain_metrics.fetch_failed",
                    source="glassnode",
                    metric=config.metric_id,
                    asset=config.asset,
                    error=repr(exc),
                )
                continue
            for observation in observations:
                if observation.value is None:
                    continue
                rows.append(
                    onchain_metric_row(
                        source=observation.source,
                        metric_id=observation.metric_id,
                        metric_title=observation.metric_title,
                        asset=observation.asset,
                        chain=observation.chain,
                        category=observation.category,
                        period_date=observation.period_date,
                        value=observation.value,
                        units=observation.units,
                        observed_at=observed_at,
                    )
                )
        for config in dune_configs:
            try:
                observations = await fetch_dune_query(
                    http_client,
                    config,
                    base_url=settings.dune_base,
                    api_key=settings.dune_api_key or "",
                )
            except (httpx.HTTPError, ValueError) as exc:
                log.warning(
                    "onchain_metrics.fetch_failed",
                    source="dune",
                    metric=config.metric_id,
                    query_id=config.query_id,
                    error=repr(exc),
                )
                continue
            for observation in observations:
                if observation.value is None:
                    continue
                rows.append(
                    onchain_metric_row(
                        source=observation.source,
                        metric_id=observation.metric_id,
                        metric_title=observation.metric_title,
                        asset=observation.asset,
                        chain=observation.chain,
                        category=observation.category,
                        period_date=observation.period_date,
                        value=observation.value,
                        units=observation.units,
                        observed_at=observed_at,
                    )
                )
        if rows:
            await ch.insert(
                "onchain_metrics", rows, column_names=ONCHAIN_METRICS_COLS
            )
        log.info(
            "onchain_metrics.run_once.done",
            metrics=total_metrics,
            rows=len(rows),
        )
        return len(rows)
    finally:
        if own_client:
            await http_client.aclose()
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    while True:
        await run_once()
        await asyncio.sleep(settings.onchain_metrics_interval_s)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
