"""Smart-money leaderboard + positions refresher.

M3.1 starts by refreshing the public leaderboard across the high-signal slices
called out in the PRD:

* categories: ``CRYPTO`` and ``FINANCE``
* time windows: ``MONTH`` and ``ALL``
* orderings: ``PNL`` and ``VOL``

The worker deduplicates wallets across those slices, then snapshots the
wallets' current positions into ``positions_smart_money`` with the best
available leaderboard context attached to each position row.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import structlog
from polymarket_client import LeaderboardEntry, PolymarketClient

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..health import HealthSink
from ..settings import get_settings
from ..writers import (
    SMART_MONEY_POSITIONS_COLS,
    smart_money_position_row,
    utcnow,
)

log = structlog.get_logger(__name__)

LEADERBOARD_CATEGORIES = ("CRYPTO", "FINANCE")
LEADERBOARD_TIME_PERIODS = ("MONTH", "ALL")
LEADERBOARD_ORDERINGS = ("PNL", "VOL")


@dataclass(frozen=True)
class LeaderboardWallet:
    proxy_wallet: str
    rank: int | None
    pnl: float | None
    volume: float | None
    sources: tuple[str, ...]


def _source_key(*, category: str, time_period: str, order_by: str) -> str:
    return f"{category}:{time_period}:{order_by}"


def merge_wallets(entries: list[tuple[str, LeaderboardEntry]]) -> list[LeaderboardWallet]:
    merged: dict[str, dict[str, object]] = {}
    for source, entry in entries:
        wallet = entry.proxy_wallet
        if not wallet:
            continue
        current = merged.setdefault(
            wallet,
            {
                "rank": None,
                "pnl": None,
                "volume": None,
                "sources": [],
            },
        )
        rank = int(entry.rank) if entry.rank > 0 else None
        best_rank = current["rank"]
        if best_rank is None or (rank is not None and rank < best_rank):
            current["rank"] = rank
        pnl = float(entry.pnl)
        best_pnl = current["pnl"]
        if best_pnl is None or abs(pnl) > abs(best_pnl):
            current["pnl"] = pnl
        volume = float(entry.volume)
        best_volume = current["volume"]
        if best_volume is None or volume > best_volume:
            current["volume"] = volume
        sources = current["sources"]
        if isinstance(sources, list) and source not in sources:
            sources.append(source)
    out = [
        LeaderboardWallet(
            proxy_wallet=wallet,
            rank=data["rank"] if isinstance(data["rank"], int) else None,
            pnl=float(data["pnl"]) if isinstance(data["pnl"], (int, float)) else None,
            volume=float(data["volume"]) if isinstance(data["volume"], (int, float)) else None,
            sources=tuple(sorted(data["sources"])) if isinstance(data["sources"], list) else (),
        )
        for wallet, data in merged.items()
    ]
    out.sort(
        key=lambda item: (
            item.rank is None,
            item.rank if item.rank is not None else 1_000_000,
            -(item.volume or 0.0),
            item.proxy_wallet,
        )
    )
    return out


async def fetch_leaderboard_wallets(
    pm: PolymarketClient,
    *,
    limit: int,
) -> list[LeaderboardWallet]:
    entries: list[tuple[str, LeaderboardEntry]] = []
    for category in LEADERBOARD_CATEGORIES:
        for time_period in LEADERBOARD_TIME_PERIODS:
            for order_by in LEADERBOARD_ORDERINGS:
                source = _source_key(
                    category=category,
                    time_period=time_period,
                    order_by=order_by,
                )
                leaderboard = await pm.data.leaderboard(
                    category=category,
                    time_period=time_period,
                    order_by=order_by,
                    limit=limit,
                )
                entries.extend((source, entry) for entry in leaderboard)
    return merge_wallets(entries)


async def snapshot_wallet_positions(
    pm: PolymarketClient,
    ch: object,
    wallet: LeaderboardWallet,
    *,
    positions_limit: int,
    observed_at,
) -> int:
    positions = await pm.data.positions(wallet.proxy_wallet, limit=positions_limit)
    if not positions:
        return 0
    rows = [
        smart_money_position_row(
            position=position,
            leaderboard_rank=wallet.rank,
            leaderboard_pnl=wallet.pnl,
            leaderboard_vol=wallet.volume,
            observed_at=observed_at,
        )
        for position in positions
    ]
    await ch.insert(
        "positions_smart_money",
        rows,
        column_names=SMART_MONEY_POSITIONS_COLS,
    )
    return len(rows)


async def run_once(
    pm: PolymarketClient,
    *,
    leaderboard_limit: int | None = None,
    positions_limit: int | None = None,
) -> tuple[int, int]:
    settings = get_settings()
    leaderboard_limit = leaderboard_limit or settings.smart_money_leaderboard_limit
    positions_limit = positions_limit or settings.smart_money_positions_limit
    wallets = await fetch_leaderboard_wallets(pm, limit=leaderboard_limit)
    observed_at = utcnow()
    ch = await get_async_client()
    try:
        inserted_rows = 0
        for wallet in wallets:
            inserted_rows += await snapshot_wallet_positions(
                pm,
                ch,
                wallet,
                positions_limit=positions_limit,
                observed_at=observed_at,
            )
        log.info(
            "smart_money.refresh.done",
            wallets=len(wallets),
            inserted_rows=inserted_rows,
        )
        return len(wallets), inserted_rows
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    ch_for_health = await get_async_client()
    health = HealthSink(ch_for_health)
    dlq = DeadLetterQueue(settings.redis_url, stream="smart_money_refresh")
    try:
        async with PolymarketClient(
            gamma_base_url=settings.polymarket_gamma_base,
            clob_base_url=settings.polymarket_clob_base,
            data_base_url=settings.polymarket_data_base,
            health_cb=health,
        ) as pm:
            while True:
                try:
                    await run_once(
                        pm,
                        leaderboard_limit=settings.smart_money_leaderboard_limit,
                        positions_limit=settings.smart_money_positions_limit,
                    )
                except Exception as exc:
                    log.error("smart_money.refresh.failed", error=repr(exc))
                    await dlq.push(
                        source="polymarket.data",
                        reason="smart_money_refresh_failed",
                        payload={"error": repr(exc)},
                    )
                await asyncio.sleep(settings.smart_money_refresh_interval_s)
    finally:
        await dlq.aclose()
        await ch_for_health.close()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
