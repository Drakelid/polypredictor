"""Per-asset on-chain feature derivation for the ensemble (PRD §6.3).

Reads the trailing 30 days of ``onchain_metrics`` rows in one PIT-correct
batch and derives the small handful of dimensionless signals the ensemble
booster consumes:

* ``onchain_exchange_inflow_z`` — z-score of the latest 24h BTC/ETH inflow
  vs the trailing 30d window for the same asset.
* ``onchain_exchange_outflow_z`` — same shape for outflow.
* ``onchain_whale_count_delta_pct`` — 7d percent change of whale-wallet count
  for the market's asset.
* ``onchain_stablecoin_supply_delta_pct`` — 7d percent change of summed
  stablecoin supply (USDT + USDC across whatever rows the worker landed).
  This one is asset-agnostic: the same value goes to every market.

Z-scoring (rather than passing raw inflow in BTC) keeps the feature
distribution comparable across assets so the booster can learn a single
threshold instead of asset-specific ones. When fewer than 5 historical
observations are available for an asset/metric, the corresponding feature
returns ``None`` so an unprimed worker doesn't synthesise garbage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient

_MIN_SAMPLES_FOR_ZSCORE = 5
_FLOW_INFLOW_METRIC = "transactions/transfers_volume_to_exchanges_sum"
_FLOW_OUTFLOW_METRIC = "transactions/transfers_volume_from_exchanges_sum"
_WHALE_METRIC_PREFIX = "addresses/min_"  # e.g. addresses/min_1k_count, min_10k_count
_SUPPLY_METRIC = "supply/current"
_STABLECOIN_ASSETS = ("USDT", "USDC")


@dataclass(frozen=True)
class OnchainAssetFeatures:
    exchange_inflow_z: float | None = None
    exchange_outflow_z: float | None = None
    whale_count_delta_pct: float | None = None


@dataclass(frozen=True)
class OnchainFeatureContext:
    by_asset: dict[str, OnchainAssetFeatures] = field(default_factory=dict)
    stablecoin_supply_delta_pct: float | None = None

    def for_asset(self, asset: str | None) -> OnchainAssetFeatures:
        """Per-asset features (empty defaults if the asset isn't tracked).

        Always returns a populated dataclass — callers shouldn't have to
        special-case "no row" vs "no asset". Missing values surface as None
        on the dataclass fields.
        """
        if not asset:
            return OnchainAssetFeatures()
        return self.by_asset.get(asset.upper(), OnchainAssetFeatures())


@dataclass
class _Series:
    """Mutable accumulator: rows keyed by (asset, period_date) with revision wins."""

    rows: dict[tuple[str, datetime], tuple[datetime, float]] = field(
        default_factory=dict
    )

    def add(
        self,
        *,
        asset: str,
        period_date: datetime,
        value: float | None,
        observed_at: datetime,
    ) -> None:
        if value is None:
            return
        key = (asset.upper(), period_date)
        prev = self.rows.get(key)
        if prev is None or observed_at >= prev[0]:
            self.rows[key] = (observed_at, float(value))

    def by_asset(self) -> dict[str, list[tuple[datetime, float]]]:
        out: dict[str, list[tuple[datetime, float]]] = {}
        for (asset, period), (_observed, value) in self.rows.items():
            out.setdefault(asset, []).append((period, value))
        for asset in out:
            out[asset].sort(key=lambda pair: pair[0])
        return out


def _zscore(values: list[float]) -> float | None:
    if len(values) < _MIN_SAMPLES_FOR_ZSCORE:
        return None
    *history, latest = values
    if len(history) < _MIN_SAMPLES_FOR_ZSCORE - 1:
        return None
    mean = sum(history) / len(history)
    variance = sum((v - mean) ** 2 for v in history) / len(history)
    if variance <= 1e-12:
        return None
    return float((latest - mean) / math.sqrt(variance))


def _delta_pct_at_horizon(
    series: list[tuple[datetime, float]],
    *,
    horizon: timedelta,
    tolerance: timedelta,
) -> float | None:
    """Latest value vs the value ``horizon`` ago, returned as a percent change.

    ``tolerance`` lets us pick the closest historical observation when the
    series doesn't have a row exactly ``horizon`` back. Daily ingestion will
    usually land within a few hours of the target.
    """
    if len(series) < 2:
        return None
    latest_period, latest_value = series[-1]
    target = latest_period - horizon
    best: tuple[float, float] | None = None  # (abs_distance_seconds, value)
    for period, value in series[:-1]:
        distance = abs((period - target).total_seconds())
        if distance > tolerance.total_seconds():
            continue
        if best is None or distance < best[0]:
            best = (distance, value)
    if best is None:
        return None
    prior_value = best[1]
    if abs(prior_value) <= 1e-12:
        return None
    return float((latest_value - prior_value) / prior_value)


async def load_onchain_feature_context(
    ch: AsyncClient,
    asked_at: datetime,
    *,
    lookback_days: int = 30,
    delta_horizon_days: int = 7,
) -> OnchainFeatureContext:
    """Batch-load PIT-correct onchain features for every asset we track.

    Issues a single ClickHouse query for the trailing ``lookback_days`` of
    rows, dedupes revisions (latest ``observed_at`` per natural key), and
    returns a context keyed by asset. Empty result → empty context (every
    feature surfaces as ``None`` downstream).
    """
    cutoff = asked_at - timedelta(days=lookback_days)
    query = """
        SELECT source, metric_id, asset, category,
               period_date, value, observed_at
        FROM onchain_metrics
        WHERE observed_at <= {asof:DateTime64(3)}
          AND period_date >= toDate({cutoff:DateTime64(3)})
          AND value IS NOT NULL
        ORDER BY source, metric_id, asset, period_date, observed_at DESC
        LIMIT 1 BY source, metric_id, asset, period_date
    """
    result = await ch.query(
        query,
        parameters={"asof": asked_at, "cutoff": cutoff},
    )

    inflow = _Series()
    outflow = _Series()
    supply: dict[str, _Series] = {asset: _Series() for asset in _STABLECOIN_ASSETS}
    whale: _Series = _Series()

    for row in result.result_rows:
        _source, metric_id, asset, _category, period_raw, value, observed_at = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            row[4],
            row[5],
            row[6],
        )
        if isinstance(period_raw, datetime):
            period = period_raw
        else:
            period = datetime(period_raw.year, period_raw.month, period_raw.day)
        if metric_id == _FLOW_INFLOW_METRIC:
            inflow.add(asset=asset, period_date=period, value=value, observed_at=observed_at)
        elif metric_id == _FLOW_OUTFLOW_METRIC:
            outflow.add(asset=asset, period_date=period, value=value, observed_at=observed_at)
        elif metric_id.startswith(_WHALE_METRIC_PREFIX):
            whale.add(asset=asset, period_date=period, value=value, observed_at=observed_at)
        elif metric_id == _SUPPLY_METRIC and asset.upper() in supply:
            supply[asset.upper()].add(
                asset=asset, period_date=period, value=value, observed_at=observed_at
            )

    inflow_by_asset = inflow.by_asset()
    outflow_by_asset = outflow.by_asset()
    whale_by_asset = whale.by_asset()

    horizon = timedelta(days=delta_horizon_days)
    # Allow ±36h slack so a missed daily observation doesn't blank the feature.
    tolerance = timedelta(hours=36)

    by_asset: dict[str, OnchainAssetFeatures] = {}
    assets = (
        set(inflow_by_asset)
        | set(outflow_by_asset)
        | set(whale_by_asset)
    )
    for asset in assets:
        inflow_values = [v for _p, v in inflow_by_asset.get(asset, [])]
        outflow_values = [v for _p, v in outflow_by_asset.get(asset, [])]
        whale_series = whale_by_asset.get(asset, [])
        by_asset[asset] = OnchainAssetFeatures(
            exchange_inflow_z=_zscore(inflow_values) if inflow_values else None,
            exchange_outflow_z=_zscore(outflow_values) if outflow_values else None,
            whale_count_delta_pct=_delta_pct_at_horizon(
                whale_series, horizon=horizon, tolerance=tolerance
            )
            if whale_series
            else None,
        )

    stablecoin_delta = _stablecoin_supply_delta_pct(
        {asset: series.by_asset().get(asset, []) for asset, series in supply.items()},
        horizon=horizon,
        tolerance=tolerance,
    )

    return OnchainFeatureContext(
        by_asset=by_asset,
        stablecoin_supply_delta_pct=stablecoin_delta,
    )


def _stablecoin_supply_delta_pct(
    series_by_asset: dict[str, list[tuple[datetime, float]]],
    *,
    horizon: timedelta,
    tolerance: timedelta,
) -> float | None:
    """Sum supply across stablecoin assets at the latest period and the
    closest period ~``horizon`` ago, then return the percent delta.

    Done at the *summed* level so adding USDC doesn't dilute USDT's signal —
    the model sees one stablecoin-supply growth scalar rather than two.
    """
    latest_period: datetime | None = None
    latest_total = 0.0
    prior_total = 0.0
    valid = False
    for series in series_by_asset.values():
        if not series:
            continue
        period, value = series[-1]
        latest_total += value
        if latest_period is None or period > latest_period:
            latest_period = period
    if latest_period is None:
        return None
    target = latest_period - horizon
    for series in series_by_asset.values():
        best: tuple[float, float] | None = None
        for period, value in series[:-1] if series else []:
            distance = abs((period - target).total_seconds())
            if distance > tolerance.total_seconds():
                continue
            if best is None or distance < best[0]:
                best = (distance, value)
        if best is not None:
            prior_total += best[1]
            valid = True
    if not valid or abs(prior_total) <= 1e-12:
        return None
    return float((latest_total - prior_total) / prior_total)
