"""FRED / BLS macro daily ingestion (M5).

Fetches configurable macro series from two public upstreams:

* FRED ``fredgraph.csv`` for the full historical CSV of each configured series.
* BLS ``timeseries/data`` for recent published observations of each configured
  series, using the public unregistered signature so the worker stays usable
  out of the box.

Each row carries ``period_date`` (the date the observation describes) and
``observed_at`` (when we learned). When an upstream revises an old observation,
we write a new row at a later ``observed_at`` and keep the PIT contract intact.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import structlog

from ..clickhouse import get_async_client
from ..settings import get_settings
from ..writers import MACRO_SERIES_COLS, macro_series_row, utcnow

log = structlog.get_logger(__name__)

FRED_CSV_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
BLS_API_BASE = "https://api.bls.gov/publicAPI/v2"


@dataclass(frozen=True)
class FredSeriesConfig:
    series_id: str
    title: str
    units: str = ""


@dataclass(frozen=True)
class BlsSeriesConfig:
    series_id: str
    title: str
    units: str = ""


@dataclass(frozen=True)
class MacroObservation:
    source: str
    series_id: str
    series_title: str
    period_date: datetime
    value: float | None
    units: str


def _load_series_config_entries(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    series = raw.get("series", []) if isinstance(raw, dict) else []
    return [entry for entry in series if isinstance(entry, dict)]


def load_fred_config(path: str | None) -> list[FredSeriesConfig]:
    """Read the configured FRED series list."""

    out: list[FredSeriesConfig] = []
    for entry in _load_series_config_entries(path):
        series_id = str(entry.get("series_id") or "").strip()
        if not series_id:
            continue
        out.append(
            FredSeriesConfig(
                series_id=series_id,
                title=str(entry.get("title") or series_id),
                units=str(entry.get("units") or ""),
            )
        )
    return out


def load_bls_config(path: str | None) -> list[BlsSeriesConfig]:
    """Read the configured BLS series list."""

    out: list[BlsSeriesConfig] = []
    for entry in _load_series_config_entries(path):
        series_id = str(entry.get("series_id") or "").strip().upper()
        if not series_id:
            continue
        out.append(
            BlsSeriesConfig(
                series_id=series_id,
                title=str(entry.get("title") or series_id),
                units=str(entry.get("units") or ""),
            )
        )
    return out


def _parse_value(raw: str) -> float | None:
    """Treat blank or non-numeric values as null."""

    raw = raw.strip()
    if not raw or raw == ".":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if math.isnan(value):
        return None
    return value


def parse_fred_csv(
    csv_text: str,
    *,
    series: FredSeriesConfig,
) -> list[MacroObservation]:
    """Parse a FRED ``fredgraph.csv`` payload into typed observations."""

    reader = csv.DictReader(StringIO(csv_text))
    out: list[MacroObservation] = []
    for row in reader:
        date_str = (
            row.get("observation_date")
            or row.get("DATE")
            or row.get("date")
            or ""
        ).strip()
        if not date_str:
            continue
        try:
            period_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            continue
        raw_value = row.get(series.series_id) or row.get(series.series_id.upper()) or ""
        value = _parse_value(raw_value)
        out.append(
            MacroObservation(
                source="fred",
                series_id=series.series_id,
                series_title=series.title,
                period_date=period_date,
                value=value,
                units=series.units,
            )
        )
    return out


def _bls_period_date(year: str, period: str) -> datetime | None:
    try:
        year_num = int(year)
    except ValueError:
        return None
    if period.startswith("M") and len(period) == 3 and period[1:].isdigit():
        month = int(period[1:])
        if 1 <= month <= 12:
            return datetime(year_num, month, 1, tzinfo=UTC)
        return None
    if period.startswith("Q") and len(period) == 3 and period[1:].isdigit():
        quarter = int(period[1:])
        if 1 <= quarter <= 4:
            return datetime(year_num, 1 + (quarter - 1) * 3, 1, tzinfo=UTC)
        return None
    return None


def _iter_bls_series(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = payload.get("Results")
    if isinstance(results, dict):
        series = results.get("series", [])
        return [entry for entry in series if isinstance(entry, dict)]
    if isinstance(results, list):
        out: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            series = item.get("series", [])
            out.extend(entry for entry in series if isinstance(entry, dict))
        return out
    return []


def parse_bls_json(
    payload: dict[str, Any],
    *,
    series: BlsSeriesConfig,
) -> list[MacroObservation]:
    """Parse a BLS ``timeseries/data`` payload into typed observations."""

    status = str(payload.get("status") or "")
    if status and status != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS request failed for {series.series_id}: {status}")

    out: list[MacroObservation] = []
    for series_payload in _iter_bls_series(payload):
        if str(series_payload.get("seriesID") or "").upper() != series.series_id:
            continue
        data_points = series_payload.get("data", [])
        if not isinstance(data_points, list):
            continue
        for point in data_points:
            if not isinstance(point, dict):
                continue
            period_date = _bls_period_date(
                str(point.get("year") or ""),
                str(point.get("period") or ""),
            )
            if period_date is None:
                continue
            value = _parse_value(str(point.get("value") or ""))
            out.append(
                MacroObservation(
                    source="bls",
                    series_id=series.series_id,
                    series_title=series.title,
                    period_date=period_date,
                    value=value,
                    units=series.units,
                )
            )
    return out


async def fetch_fred_series(
    client: httpx.AsyncClient,
    series: FredSeriesConfig,
) -> list[MacroObservation]:
    response = await client.get(FRED_CSV_BASE, params={"id": series.series_id})
    response.raise_for_status()
    return parse_fred_csv(response.text, series=series)


async def fetch_bls_series(
    client: httpx.AsyncClient,
    series: BlsSeriesConfig,
    *,
    base_url: str = BLS_API_BASE,
) -> list[MacroObservation]:
    response = await client.get(
        f"{base_url.rstrip('/')}/timeseries/data/{series.series_id}"
    )
    response.raise_for_status()
    return parse_bls_json(response.json(), series=series)


async def run_once(
    *,
    http_client: httpx.AsyncClient | None = None,
    series_configs: list[FredSeriesConfig] | None = None,
    bls_series_configs: list[BlsSeriesConfig] | None = None,
) -> int:
    settings = get_settings()
    if series_configs is None:
        series_configs = load_fred_config(settings.macro_series_fred_config_file)
    if bls_series_configs is None:
        bls_series_configs = load_bls_config(settings.macro_series_bls_config_file)
    total_series = len(series_configs) + len(bls_series_configs)
    if total_series == 0:
        log.info("macro_series.no_series_configured")
        return 0

    own_client = http_client is None
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=settings.macro_series_timeout_s)

    ch = await get_async_client()
    observed_at = utcnow()
    total_rows = 0
    try:
        rows: list[tuple[Any, ...]] = []
        for series in series_configs:
            try:
                observations = await fetch_fred_series(http_client, series)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning(
                    "macro_series.fetch_failed",
                    source="fred",
                    series_id=series.series_id,
                    error=repr(exc),
                )
                continue
            for observation in observations:
                if observation.value is None:
                    continue
                rows.append(
                    macro_series_row(
                        source=observation.source,
                        series_id=observation.series_id,
                        series_title=observation.series_title,
                        period_date=observation.period_date,
                        value=observation.value,
                        units=observation.units,
                        observed_at=observed_at,
                    )
                )
        for series in bls_series_configs:
            try:
                observations = await fetch_bls_series(
                    http_client,
                    series,
                    base_url=settings.bls_public_api_base,
                )
            except (httpx.HTTPError, ValueError) as exc:
                log.warning(
                    "macro_series.fetch_failed",
                    source="bls",
                    series_id=series.series_id,
                    error=repr(exc),
                )
                continue
            for observation in observations:
                if observation.value is None:
                    continue
                rows.append(
                    macro_series_row(
                        source=observation.source,
                        series_id=observation.series_id,
                        series_title=observation.series_title,
                        period_date=observation.period_date,
                        value=observation.value,
                        units=observation.units,
                        observed_at=observed_at,
                    )
                )
        if rows:
            await ch.insert("macro_series", rows, column_names=MACRO_SERIES_COLS)
            total_rows = len(rows)
        log.info("macro_series.run_once.done", series=total_series, rows=total_rows)
        return total_rows
    finally:
        if own_client:
            await http_client.aclose()
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    while True:
        await run_once()
        await asyncio.sleep(settings.macro_series_interval_s)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
