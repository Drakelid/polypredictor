"""As-of reads over ``regime_labels`` (M6.2).

Written by :mod:`ingest.workers.regime_tagger`. Consumers:

* the API serve path passes the regime label into the conformal registry as
  the third Mondrian axis (PRD §6.2 last bullet)
* the dashboard renders a regime badge so users can read the band width in
  context

PIT contract: ``observed_at <= asked_at`` means a backtest at time T sees
exactly the regime label that was live at T, not whatever the latest
classifier output is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class RegimeLabelRow:
    regime_date: datetime
    label: str
    confidence: float
    btc_realized_vol_24h: float | None
    btc_realized_vol_7d: float | None
    btc_momentum_7d: float | None
    btc_ndx_correlation_30d: float | None
    stablecoin_supply_delta_7d: float | None
    reasons: list[str]
    classifier: str
    event_time: datetime
    observed_at: datetime


_SELECT = (
    "regime_date, label, confidence, btc_realized_vol_24h, btc_realized_vol_7d, "
    "btc_momentum_7d, btc_ndx_correlation_30d, stablecoin_supply_delta_7d, "
    "reasons, classifier, event_time, observed_at"
)


def _row_to_regime(row: tuple[object, ...]) -> RegimeLabelRow:
    raw_reasons = str(row[8] or "")
    reasons = [piece for piece in raw_reasons.split("|") if piece]
    return RegimeLabelRow(
        regime_date=row[0],  # type: ignore[arg-type]
        label=str(row[1]),
        confidence=float(row[2]),
        btc_realized_vol_24h=float(row[3]) if row[3] is not None else None,
        btc_realized_vol_7d=float(row[4]) if row[4] is not None else None,
        btc_momentum_7d=float(row[5]) if row[5] is not None else None,
        btc_ndx_correlation_30d=float(row[6]) if row[6] is not None else None,
        stablecoin_supply_delta_7d=float(row[7]) if row[7] is not None else None,
        reasons=reasons,
        classifier=str(row[9]),
        event_time=row[10],  # type: ignore[arg-type]
        observed_at=row[11],  # type: ignore[arg-type]
    )


async def regime_label_asof(
    ch: AsyncClient,
    asked_at: datetime,
) -> RegimeLabelRow | None:
    """Latest regime row known at ``asked_at`` (PIT-correct)."""
    query = f"""
        SELECT {_SELECT}
        FROM regime_labels
        WHERE observed_at <= {{asof:DateTime64(3)}}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(query, parameters={"asof": asked_at})
    rows = result.result_rows
    if not rows:
        return None
    return _row_to_regime(rows[0])
