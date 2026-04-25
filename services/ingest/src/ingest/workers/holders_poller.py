"""Holder-concentration poller (M3.2, PRD §6 / §9).

Every ``holders_poll_interval_s`` (default 300s per PRD §7 TTL for holders):

1. List active markets from ``markets_snapshots`` (as-of now).
2. Batch condition_ids through the Data API ``/holders`` endpoint.
3. Split the returned holders per outcome (YES / NO), sort descending by
   size, compute rank + pct-of-outcome per holder, and persist to
   ``holders_snapshots``.
4. Derive per-outcome Gini, top-1, top-5 concentration in
   :mod:`.concentration` and persist a summary row to
   ``market_concentration``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from polymarket_client import HolderEntry, PolymarketClient

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..health import HealthSink
from ..settings import get_settings
from ..writers import (
    HOLDERS_COLS,
    MARKET_CONCENTRATION_COLS,
    holder_row,
    market_concentration_row,
    utcnow,
)
from .concentration import concentration_for_outcome

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MarketTokens:
    condition_id: str
    yes_token: str | None
    no_token: str | None


@dataclass(frozen=True)
class PollStats:
    markets: int
    holder_rows: int
    concentration_rows: int


async def _active_markets(
    ch: Any,
    *,
    observed_at: datetime,
    limit: int,
) -> list[MarketTokens]:
    query = """
        SELECT condition_id, token_ids, active, closed, archived
        FROM markets_snapshots
        WHERE observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
        ORDER BY volume_usdc DESC
        LIMIT {limit:UInt32}
    """
    result = await ch.query(query, parameters={"asof": observed_at, "limit": limit})
    out: list[MarketTokens] = []
    for condition_id, token_ids, active, closed, archived in result.result_rows:
        if not bool(active) or bool(closed) or bool(archived):
            continue
        tokens = list(token_ids) if token_ids else []
        yes_token = str(tokens[0]) if len(tokens) >= 1 else None
        no_token = str(tokens[1]) if len(tokens) >= 2 else None
        out.append(
            MarketTokens(
                condition_id=str(condition_id),
                yes_token=yes_token,
                no_token=no_token,
            )
        )
    return out


def _outcome_label(holder: HolderEntry) -> str:
    if holder.outcome is None:
        return "UNKNOWN"
    label = holder.outcome.strip().upper()
    return label if label in {"YES", "NO"} else "UNKNOWN"


def _split_holders_by_outcome(
    holders: list[HolderEntry],
) -> dict[str, list[HolderEntry]]:
    grouped: dict[str, list[HolderEntry]] = defaultdict(list)
    for holder in holders:
        if holder.size <= 0.0:
            continue
        grouped[_outcome_label(holder)].append(holder)
    return {
        outcome: sorted(entries, key=lambda entry: entry.size, reverse=True)
        for outcome, entries in grouped.items()
    }


def _holder_rows_for_market(
    tokens: MarketTokens,
    outcome_holders: dict[str, list[HolderEntry]],
    observed_at: datetime,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for outcome, holders in outcome_holders.items():
        if not holders:
            continue
        total = sum(entry.size for entry in holders)
        if total <= 0.0:
            continue
        if outcome == "YES":
            token_id = tokens.yes_token or ""
        elif outcome == "NO":
            token_id = tokens.no_token or ""
        else:
            token_id = ""
        for rank, holder in enumerate(holders, start=1):
            rows.append(
                holder_row(
                    condition_id=tokens.condition_id,
                    token_id=token_id,
                    rank=rank,
                    holder=holder,
                    pct_of_outcome=holder.size / total,
                    observed_at=observed_at,
                )
            )
    return rows


def _concentration_row_for_market(
    tokens: MarketTokens,
    outcome_holders: dict[str, list[HolderEntry]],
    *,
    whale_threshold: float,
    observed_at: datetime,
) -> tuple[Any, ...]:
    yes_sizes = [h.size for h in outcome_holders.get("YES", [])]
    no_sizes = [h.size for h in outcome_holders.get("NO", [])]
    yes = concentration_for_outcome(yes_sizes, whale_threshold=whale_threshold)
    no = concentration_for_outcome(no_sizes, whale_threshold=whale_threshold)
    return market_concentration_row(
        condition_id=tokens.condition_id,
        yes_gini=yes.gini,
        no_gini=no.gini,
        yes_top1_pct=yes.top1_pct,
        no_top1_pct=no.top1_pct,
        yes_top5_pct=yes.top5_pct,
        no_top5_pct=no.top5_pct,
        yes_whale_flag=yes.whale_flag,
        no_whale_flag=no.whale_flag,
        yes_holders_count=yes.holders_count,
        no_holders_count=no.holders_count,
        observed_at=observed_at,
    )


async def run_once(
    pm: PolymarketClient,
    *,
    batch_size: int | None = None,
    max_markets: int | None = None,
    whale_threshold: float | None = None,
) -> PollStats:
    settings = get_settings()
    batch_size = batch_size or settings.holders_poll_batch_size
    max_markets = max_markets or settings.holders_poll_max_markets
    whale_threshold = (
        whale_threshold
        if whale_threshold is not None
        else settings.holder_concentration_whale_threshold
    )

    ch = await get_async_client()
    try:
        observed_at = utcnow()
        markets = await _active_markets(ch, observed_at=observed_at, limit=max_markets)
        if not markets:
            return PollStats(markets=0, holder_rows=0, concentration_rows=0)
        tokens_by_cid = {m.condition_id: m for m in markets}
        condition_ids = list(tokens_by_cid.keys())

        all_holder_rows: list[tuple[Any, ...]] = []
        all_concentration_rows: list[tuple[Any, ...]] = []

        for offset in range(0, len(condition_ids), batch_size):
            batch = condition_ids[offset : offset + batch_size]
            response = await pm.data.holders(batch)
            for cid in batch:
                tokens = tokens_by_cid[cid]
                holders = response.get(cid, [])
                grouped = _split_holders_by_outcome(holders)
                all_holder_rows.extend(
                    _holder_rows_for_market(tokens, grouped, observed_at)
                )
                all_concentration_rows.append(
                    _concentration_row_for_market(
                        tokens,
                        grouped,
                        whale_threshold=whale_threshold,
                        observed_at=observed_at,
                    )
                )

        if all_holder_rows:
            await ch.insert(
                "holders_snapshots",
                all_holder_rows,
                column_names=HOLDERS_COLS,
            )
        if all_concentration_rows:
            await ch.insert(
                "market_concentration",
                all_concentration_rows,
                column_names=MARKET_CONCENTRATION_COLS,
            )
        log.info(
            "holders.poll.done",
            markets=len(markets),
            holder_rows=len(all_holder_rows),
            concentration_rows=len(all_concentration_rows),
        )
        return PollStats(
            markets=len(markets),
            holder_rows=len(all_holder_rows),
            concentration_rows=len(all_concentration_rows),
        )
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    ch_for_health = await get_async_client()
    health = HealthSink(ch_for_health)
    dlq = DeadLetterQueue(settings.redis_url, stream="holders_poller")
    try:
        async with PolymarketClient(
            gamma_base_url=settings.polymarket_gamma_base,
            clob_base_url=settings.polymarket_clob_base,
            data_base_url=settings.polymarket_data_base,
            health_cb=health,
        ) as pm:
            while True:
                try:
                    await run_once(pm)
                except Exception as exc:
                    log.error("holders.poll.failed", error=repr(exc))
                    await dlq.push(
                        source="polymarket.data",
                        reason="holders_poll_failed",
                        payload={"error": repr(exc)},
                    )
                await asyncio.sleep(settings.holders_poll_interval_s)
    finally:
        await dlq.aclose()
        await ch_for_health.close()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
