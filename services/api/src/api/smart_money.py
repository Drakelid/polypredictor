"""As-of reads over the M3.1 smart-money per-market rollup.

Writes come from :mod:`ingest.workers.smart_money_refresh`. Consumers:

* API ``/v1/markets/{condition_id}/smart-money`` for the detail view badge
* Ensemble feature pipeline in M3.1.4 (``smart_money_consensus``)

The 24h directional delta is derived at read time: one as-of read at
``asked_at`` and a second at ``asked_at - 24h``. Keeping it derived avoids
extra write-amplification and means backfilled data "just works" once it
exists in the append-only store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from clickhouse_connect.driver.asyncclient import AsyncClient


@dataclass(frozen=True)
class SmartMoneyPerMarket:
    condition_id: str
    yes_wallets: int
    no_wallets: int
    yes_size: float
    no_size: float
    yes_size_usdc: float
    no_size_usdc: float
    yes_avg_entry: float | None
    no_avg_entry: float | None
    net_size_usdc: float
    consensus_score: float
    sample_wallets: int
    dominant_outcome: str
    event_time: datetime
    observed_at: datetime


@dataclass(frozen=True)
class SmartMoneyView:
    latest: SmartMoneyPerMarket
    prior_24h: SmartMoneyPerMarket | None
    directional_delta_usdc: float | None
    consensus_delta_24h: float | None


_SELECT_COLUMNS = (
    "condition_id, yes_wallets, no_wallets, yes_size, no_size, "
    "yes_size_usdc, no_size_usdc, yes_avg_entry, no_avg_entry, "
    "net_size_usdc, consensus_score, sample_wallets, dominant_outcome, "
    "event_time, observed_at"
)


def _row_to_per_market(row: tuple[object, ...]) -> SmartMoneyPerMarket:
    return SmartMoneyPerMarket(
        condition_id=str(row[0]),
        yes_wallets=int(row[1]),
        no_wallets=int(row[2]),
        yes_size=float(row[3]),
        no_size=float(row[4]),
        yes_size_usdc=float(row[5]),
        no_size_usdc=float(row[6]),
        yes_avg_entry=float(row[7]) if row[7] is not None else None,
        no_avg_entry=float(row[8]) if row[8] is not None else None,
        net_size_usdc=float(row[9]),
        consensus_score=float(row[10]),
        sample_wallets=int(row[11]),
        dominant_outcome=str(row[12]),
        event_time=row[13],  # type: ignore[arg-type]
        observed_at=row[14],  # type: ignore[arg-type]
    )


async def smart_money_asof(
    ch: AsyncClient,
    condition_id: str,
    asked_at: datetime,
) -> SmartMoneyPerMarket | None:
    query = f"""
        SELECT {_SELECT_COLUMNS}
        FROM smart_money_per_market
        WHERE condition_id = {{cond:String}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY observed_at DESC
        LIMIT 1
    """
    result = await ch.query(query, parameters={"cond": condition_id, "asof": asked_at})
    rows = result.result_rows
    if not rows:
        return None
    return _row_to_per_market(rows[0])


async def smart_money_batch_asof(
    ch: AsyncClient,
    condition_ids: list[str],
    asked_at: datetime,
) -> dict[str, SmartMoneyPerMarket]:
    if not condition_ids:
        return {}
    query = f"""
        SELECT {_SELECT_COLUMNS}
        FROM smart_money_per_market
        WHERE condition_id IN {{conds:Array(String)}}
          AND observed_at <= {{asof:DateTime64(3)}}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
    """
    result = await ch.query(
        query,
        parameters={"conds": list(condition_ids), "asof": asked_at},
    )
    return {
        str(row[0]): _row_to_per_market(row)
        for row in result.result_rows
    }


async def smart_money_view(
    ch: AsyncClient,
    condition_id: str,
    asked_at: datetime,
    *,
    delta_window_hours: int = 24,
) -> SmartMoneyView | None:
    latest = await smart_money_asof(ch, condition_id, asked_at)
    if latest is None:
        return None
    prior = await smart_money_asof(
        ch,
        condition_id,
        asked_at - timedelta(hours=delta_window_hours),
    )
    directional_delta: float | None = None
    consensus_delta: float | None = None
    if prior is not None:
        directional_delta = latest.net_size_usdc - prior.net_size_usdc
        consensus_delta = latest.consensus_score - prior.consensus_score
    return SmartMoneyView(
        latest=latest,
        prior_24h=prior,
        directional_delta_usdc=directional_delta,
        consensus_delta_24h=consensus_delta,
    )
