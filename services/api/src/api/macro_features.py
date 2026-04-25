"""Macro-series feature derivation for the ensemble (PRD §5.2 / §6.3).

Reads the trailing 400 days of ``macro_series`` rows in one PIT-correct
batch and surfaces a small set of dimensionless scalars covering the macro
regimes that move crypto/finance prediction markets:

* ``macro_fed_funds_30d_delta`` — 30d change in the Fed Funds Effective Rate
  (FEDFUNDS). Captures the actual rate path between FOMC meetings.
* ``macro_treasury_10y_30d_delta`` — 30d change in the 10y Treasury yield
  (DGS10). Long-rate moves are correlated with risk-asset repricing.
* ``macro_yield_curve_2s10s`` — latest level of the 10y-2y spread (T10Y2Y).
  Inversion is a well-known recession indicator.
* ``macro_cpi_yoy_pct`` — 12-month percent change in headline CPI
  (CPIAUCSL). Inflation regime is a key Fed-policy driver.

All four are asset-agnostic — the same scalar applies to every market in a
given request. Missing data → ``None`` so an unprimed FRED worker doesn't
synthesise garbage. The lookback window covers ~13 months so CPI YoY can be
computed even when the most recent CPI release is several days old.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient

# Series IDs we read from FRED via macro_series. Keep this list short — every
# entry triples the per-request macro query selectivity.
_FED_FUNDS_SERIES = "FEDFUNDS"
_TREASURY_10Y_SERIES = "DGS10"
_YIELD_CURVE_SERIES = "T10Y2Y"
_CPI_SERIES = "CPIAUCSL"

_MACRO_SERIES_IDS = (
    _FED_FUNDS_SERIES,
    _TREASURY_10Y_SERIES,
    _YIELD_CURVE_SERIES,
    _CPI_SERIES,
)

# Slack windows: macro releases land at predictable cadences but with the
# occasional hiccup. The 30d delta needs ±5 days of slack so a missed
# observation doesn't blank the feature; CPI YoY needs ±15 days because the
# release schedule is monthly with occasional staggered revisions.
_DELTA_30D_HORIZON = timedelta(days=30)
_DELTA_30D_TOLERANCE = timedelta(days=5)
_CPI_YOY_HORIZON = timedelta(days=365)
_CPI_YOY_TOLERANCE = timedelta(days=15)


@dataclass(frozen=True)
class MacroFeatureContext:
    fed_funds_30d_delta: float | None = None
    treasury_10y_30d_delta: float | None = None
    yield_curve_2s10s: float | None = None
    cpi_yoy_pct: float | None = None


def _value_at_horizon(
    series: list[tuple[datetime, float]],
    *,
    horizon: timedelta,
    tolerance: timedelta,
) -> float | None:
    """Closest historical observation ``horizon`` ago, within ``tolerance``."""

    if len(series) < 2:
        return None
    latest_period, _latest_value = series[-1]
    target = latest_period - horizon
    best: tuple[float, float] | None = None  # (abs_distance_seconds, value)
    for period, value in series[:-1]:
        distance = abs((period - target).total_seconds())
        if distance > tolerance.total_seconds():
            continue
        if best is None or distance < best[0]:
            best = (distance, value)
    return best[1] if best is not None else None


def _absolute_delta(
    series: list[tuple[datetime, float]],
    *,
    horizon: timedelta,
    tolerance: timedelta,
) -> float | None:
    """Latest minus value ``horizon`` ago. Used for rate deltas (already in
    percentage-point units, so a percent change is misleading)."""

    if not series:
        return None
    prior = _value_at_horizon(series, horizon=horizon, tolerance=tolerance)
    if prior is None:
        return None
    _latest_period, latest = series[-1]
    return float(latest - prior)


def _percent_change(
    series: list[tuple[datetime, float]],
    *,
    horizon: timedelta,
    tolerance: timedelta,
) -> float | None:
    """(latest / value_at_horizon) − 1. Used for CPI YoY where percent change
    is the standard reading."""

    if not series:
        return None
    prior = _value_at_horizon(series, horizon=horizon, tolerance=tolerance)
    if prior is None or abs(prior) <= 1e-12:
        return None
    _latest_period, latest = series[-1]
    return float((latest - prior) / prior)


async def load_macro_feature_context(
    ch: AsyncClient,
    asked_at: datetime,
    *,
    lookback_days: int = 400,
) -> MacroFeatureContext:
    """Batch-load PIT-correct macro features for the request.

    Single ClickHouse query for the trailing ``lookback_days`` of FED FUNDS,
    DGS10, T10Y2Y, and CPI rows. Revisions are deduped by keeping the latest
    ``observed_at`` per ``(series_id, period_date)`` natural key; missing
    series surface as ``None`` on the returned context.
    """
    cutoff = asked_at - timedelta(days=lookback_days)
    query = """
        SELECT series_id, period_date, value, observed_at
        FROM macro_series
        WHERE observed_at <= {asof:DateTime64(3)}
          AND period_date >= toDate({cutoff:DateTime64(3)})
          AND value IS NOT NULL
          AND series_id IN {series_ids:Array(String)}
        ORDER BY series_id, period_date, observed_at DESC
        LIMIT 1 BY series_id, period_date
    """
    result = await ch.query(
        query,
        parameters={
            "asof": asked_at,
            "cutoff": cutoff,
            "series_ids": list(_MACRO_SERIES_IDS),
        },
    )

    series_by_id: dict[str, list[tuple[datetime, float]]] = {
        sid: [] for sid in _MACRO_SERIES_IDS
    }
    for row in result.result_rows:
        series_id = str(row[0])
        period_raw = row[1]
        value = row[2]
        if value is None or series_id not in series_by_id:
            continue
        if isinstance(period_raw, datetime):
            period = period_raw
        else:
            period = datetime(period_raw.year, period_raw.month, period_raw.day)
        series_by_id[series_id].append((period, float(value)))
    for series_id in series_by_id:
        series_by_id[series_id].sort(key=lambda pair: pair[0])

    fed = series_by_id[_FED_FUNDS_SERIES]
    ten_y = series_by_id[_TREASURY_10Y_SERIES]
    curve = series_by_id[_YIELD_CURVE_SERIES]
    cpi = series_by_id[_CPI_SERIES]

    return MacroFeatureContext(
        fed_funds_30d_delta=_absolute_delta(
            fed, horizon=_DELTA_30D_HORIZON, tolerance=_DELTA_30D_TOLERANCE
        ),
        treasury_10y_30d_delta=_absolute_delta(
            ten_y, horizon=_DELTA_30D_HORIZON, tolerance=_DELTA_30D_TOLERANCE
        ),
        yield_curve_2s10s=float(curve[-1][1]) if curve else None,
        cpi_yoy_pct=_percent_change(
            cpi, horizon=_CPI_YOY_HORIZON, tolerance=_CPI_YOY_TOLERANCE
        ),
    )
