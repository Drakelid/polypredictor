"""Daily regime-label writer (M6.2).

Pipeline each run:

1. Fetch ~30 daily BTC closes from Binance (no API key).
2. Derive ``RegimeFeatures`` (24h + 7d realized vol, 7d momentum) via the
   pure helper in :mod:`model.regime`.
3. Optionally enrich with the latest FRED ``M2SL`` weekly delta from the
   ``macro_series`` table as a stablecoin-supply proxy. This is intentionally
   coarse — the PRD's true ask is a stablecoin-supply series, but no public
   FRED series tracks that directly. Treating M2 as a same-direction proxy
   lets the liquidity-crisis branch fire earlier than waiting on a paid
   on-chain feed.
4. Run :func:`tag_regime` and write a ``regime_labels`` row.

The walk-forward backtest already groups by ``regime`` (StratumReport.by_regime),
so once this worker has tagged a few weeks the M6 backtests start producing
regime-stratified Brier/ECE/coverage automatically.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from model import (
    BinanceKlinesClient,
    RegimeFeatures,
    RegimeResult,
    regime_features_from_btc_closes,
    tag_regime,
)

from ..clickhouse import get_async_client
from ..settings import get_settings
from ..writers import REGIME_LABELS_COLS, regime_label_row, utcnow

log = structlog.get_logger(__name__)

CLASSIFIER_VERSION = "rule_v1"


@dataclass(frozen=True)
class RegimeRunStats:
    label: str
    confidence: float
    closes_seen: int
    stablecoin_proxy_used: bool


async def _fetch_btc_closes(
    binance: BinanceKlinesClient,
    *,
    symbol: str,
    days: int,
) -> list[float]:
    klines = await binance.daily_klines(symbol=symbol, days=days)
    return [kline.close for kline in klines if kline.close > 0]


async def _stablecoin_supply_delta_proxy(ch: Any) -> float | None:
    """Latest weekly fractional change in FRED M2SL, used as a coarse
    stablecoin-supply direction proxy until a true stablecoin series is in.

    Returns ``None`` when fewer than two M2SL observations exist.
    """
    query = """
        SELECT period_date, value
        FROM macro_series
        WHERE source = 'fred' AND series_id = 'M2SL' AND value IS NOT NULL
        ORDER BY period_date DESC
        LIMIT 8
    """
    result = await ch.query(query)
    rows = result.result_rows
    if len(rows) < 2:
        return None
    latest = float(rows[0][1])
    prior = float(rows[-1][1])
    if prior <= 0:
        return None
    return (latest - prior) / prior


async def run_once(
    *,
    binance: BinanceKlinesClient | None = None,
    symbol: str | None = None,
    days: int | None = None,
) -> RegimeRunStats:
    settings = get_settings()
    symbol = symbol or settings.regime_btc_symbol
    days = days or settings.regime_lookback_days

    own_client = binance is None
    if binance is None:
        binance = BinanceKlinesClient(base_url=settings.binance_spot_base)

    ch = await get_async_client()
    observed_at = utcnow()
    try:
        closes = await _fetch_btc_closes(binance, symbol=symbol, days=days)
        stablecoin_delta: float | None = None
        try:
            stablecoin_delta = await _stablecoin_supply_delta_proxy(ch)
        except Exception as exc:
            # Optional enrichment — never block the daily tag on macro_series
            # being absent or schema-out-of-date during early deploys.
            log.warning("regime.stablecoin_proxy_failed", error=repr(exc))

        features: RegimeFeatures = regime_features_from_btc_closes(
            closes,
            stablecoin_supply_delta_7d=stablecoin_delta,
        )
        result: RegimeResult = tag_regime(features)

        regime_date = datetime(
            observed_at.year, observed_at.month, observed_at.day, tzinfo=UTC
        )
        row = regime_label_row(
            regime_date=regime_date,
            label=result.label.value,
            confidence=result.confidence,
            btc_realized_vol_24h=features.btc_realized_vol_24h,
            btc_realized_vol_7d=features.btc_realized_vol_7d,
            btc_momentum_7d=features.btc_momentum_7d,
            btc_ndx_correlation_30d=features.btc_ndx_correlation_30d,
            stablecoin_supply_delta_7d=features.stablecoin_supply_delta_7d,
            reasons=result.reasons,
            classifier=CLASSIFIER_VERSION,
            observed_at=observed_at,
        )
        await ch.insert("regime_labels", [row], column_names=REGIME_LABELS_COLS)

        log.info(
            "regime.tagged",
            label=result.label.value,
            confidence=round(result.confidence, 3),
            closes=len(closes),
            stablecoin_proxy_used=stablecoin_delta is not None,
        )
        return RegimeRunStats(
            label=result.label.value,
            confidence=result.confidence,
            closes_seen=len(closes),
            stablecoin_proxy_used=stablecoin_delta is not None,
        )
    finally:
        if own_client:
            await binance.aclose()
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    while True:
        await run_once()
        await asyncio.sleep(settings.regime_tagger_interval_s)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
