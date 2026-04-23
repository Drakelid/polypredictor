"""Backfill ``prices_history`` for newly discovered markets.

PRD M0 calls for a one-time history fetch via CLOB ``/prices-history`` when a
market first appears. This worker periodically scans the latest active market
catalog, finds token/interval pairs with no history yet, fetches the bucketed
series, and writes append-only rows into ClickHouse.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import structlog
from clickhouse_connect.driver.asyncclient import AsyncClient
from polymarket_client import PolymarketClient

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..health import HealthSink
from ..settings import get_settings
from ..writers import PRICES_HISTORY_COLS, price_history_rows, utcnow

log = structlog.get_logger(__name__)

DEFAULT_INTERVALS: tuple[str, ...] = ("1h", "6h", "1d")


@dataclass(frozen=True)
class HistoryBackfillTarget:
    condition_id: str
    token_id: str
    interval_bucket: str


async def latest_active_markets(ch: AsyncClient) -> list[dict[str, object]]:
    """Latest active market snapshot per condition id, ordered by volume."""
    query = """
        SELECT condition_id, token_ids, volume_usdc, active, closed, archived
        FROM markets_snapshots
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
        ORDER BY volume_usdc DESC
    """
    result = await ch.query(query)
    out: list[dict[str, object]] = []
    for condition_id, token_ids, volume_usdc, active, closed, archived in result.result_rows:
        if not bool(active) or bool(closed) or bool(archived):
            continue
        out.append(
            {
                "condition_id": str(condition_id),
                "token_ids": list(token_ids) if token_ids else [],
                "volume_usdc": float(volume_usdc),
            }
        )
    return out


async def existing_history_pairs(
    ch: AsyncClient, token_ids: list[str]
) -> set[tuple[str, str]]:
    if not token_ids:
        return set()
    query = """
        SELECT token_id, toString(interval_bucket)
        FROM prices_history
        WHERE token_id IN {token_ids:Array(String)}
        GROUP BY token_id, interval_bucket
    """
    result = await ch.query(query, parameters={"token_ids": token_ids})
    return {(str(token_id), str(interval_bucket)) for token_id, interval_bucket in result.result_rows}


async def select_missing_history_targets(
    ch: AsyncClient,
    *,
    intervals: tuple[str, ...] = DEFAULT_INTERVALS,
    limit: int,
) -> list[HistoryBackfillTarget]:
    markets = await latest_active_markets(ch)
    token_ids = [str(token_id) for market in markets for token_id in list(market["token_ids"])]
    existing = await existing_history_pairs(ch, token_ids)

    targets: list[HistoryBackfillTarget] = []
    for market in markets:
        condition_id = str(market["condition_id"])
        for token_id in list(market["token_ids"]):
            token = str(token_id)
            for interval_bucket in intervals:
                if (token, interval_bucket) in existing:
                    continue
                targets.append(
                    HistoryBackfillTarget(
                        condition_id=condition_id,
                        token_id=token,
                        interval_bucket=interval_bucket,
                    )
                )
                if len(targets) >= limit:
                    return targets
    return targets


async def backfill_target(
    pm: PolymarketClient,
    ch: AsyncClient,
    target: HistoryBackfillTarget,
    *,
    fidelity: int,
) -> int:
    history = await pm.clob.prices_history(
        target.token_id,
        interval=target.interval_bucket,
        fidelity=fidelity,
    )
    if not history:
        return 0
    observed_at = utcnow()
    rows = price_history_rows(
        token_id=target.token_id,
        condition_id=target.condition_id,
        interval_bucket=target.interval_bucket,
        history=history,
        observed_at=observed_at,
    )
    await ch.insert("prices_history", rows, column_names=PRICES_HISTORY_COLS)
    return len(rows)


async def run_once(
    pm: PolymarketClient,
    *,
    ch: AsyncClient,
    dlq: DeadLetterQueue,
    limit: int,
    fidelity: int,
    intervals: tuple[str, ...] = DEFAULT_INTERVALS,
) -> int:
    targets = await select_missing_history_targets(ch, intervals=intervals, limit=limit)
    inserted_rows = 0
    for target in targets:
        try:
            inserted_rows += await backfill_target(
                pm,
                ch,
                target,
                fidelity=fidelity,
            )
        except Exception as exc:
            log.warning(
                "prices_history.backfill_failed",
                condition_id=target.condition_id,
                token_id=target.token_id,
                interval_bucket=target.interval_bucket,
                error=repr(exc),
            )
            await dlq.push(
                source="polymarket.clob",
                reason="prices_history_backfill_failed",
                payload={
                    "condition_id": target.condition_id,
                    "token_id": target.token_id,
                    "interval_bucket": target.interval_bucket,
                    "error": repr(exc),
                },
            )
    if targets:
        log.info(
            "prices_history.backfill_done",
            targets=len(targets),
            rows=inserted_rows,
        )
    return inserted_rows


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    s = get_settings()
    ch = await get_async_client()
    health_ch = await get_async_client()
    health = HealthSink(health_ch)
    dlq = DeadLetterQueue(s.redis_url, stream="prices_history_backfill")
    try:
        async with PolymarketClient(
            gamma_base_url=s.polymarket_gamma_base,
            clob_base_url=s.polymarket_clob_base,
            data_base_url=s.polymarket_data_base,
            health_cb=health,
        ) as pm:
            while True:
                await run_once(
                    pm,
                    ch=ch,
                    dlq=dlq,
                    limit=s.prices_history_backfill_batch_size,
                    fidelity=s.prices_history_backfill_fidelity,
                )
                await asyncio.sleep(s.prices_history_backfill_interval_s)
    finally:
        await dlq.aclose()
        await ch.close()
        await health_ch.close()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
