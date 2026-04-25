"""Smart-money leaderboard + positions refresher.

M3.1 refreshes the public leaderboard across the high-signal slices called
out in the PRD, applies the wash-trading guards from §9, and persists both
the qualification history and a per-market rollup that the model and signal
feed consume.

Slices queried every run:

* categories: ``CRYPTO`` and ``FINANCE``
* time windows: ``MONTH`` and ``ALL``
* orderings: ``PNL`` and ``VOL``

Pipeline each run:

1. Fetch every slice, merge to unique wallets (keeping best leaderboard
   context + source provenance).
2. Pull each wallet's current positions.
3. Evaluate the wash-trading guards in
   :mod:`ingest.workers.smart_money_qualify` (PnL+VOL cross-check, multi-
   market requirement, minimum volume).
4. Write qualification rows for every merged wallet (including
   disqualified ones — the history supports backtests).
5. Snapshot positions for every wallet into ``positions_smart_money``
   (raw audit store).
6. Roll up qualified wallets' positions into
   ``smart_money_per_market`` for downstream consumption.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import structlog
from polymarket_client import LeaderboardEntry, PolymarketClient, Position

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..health import HealthSink
from ..settings import get_settings
from ..writers import (
    SIGNAL_EVENTS_COLS,
    SMART_MONEY_PER_MARKET_COLS,
    SMART_MONEY_POSITIONS_COLS,
    SMART_MONEY_QUALIFICATIONS_COLS,
    signal_event_row,
    smart_money_per_market_row,
    smart_money_position_row,
    smart_money_qualification_row,
    utcnow,
)
from .smart_money_qualify import (
    QualificationDecision,
    QualificationThresholds,
    aggregate_per_market,
    qualify_wallet,
)
from .whale_flow import (
    PositionSnapshot,
    WhaleFlowEvent,
    WhaleFlowThresholds,
    detect_whale_flow,
    positions_from_polymarket,
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


@dataclass(frozen=True)
class RefreshStats:
    wallets: int
    qualified_wallets: int
    position_rows: int
    per_market_rows: int
    whale_events: int


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


async def fetch_positions_for_wallets(
    pm: PolymarketClient,
    wallets: list[LeaderboardWallet],
    *,
    positions_limit: int,
) -> dict[str, list[Position]]:
    positions: dict[str, list[Position]] = {}
    for wallet in wallets:
        positions[wallet.proxy_wallet] = await pm.data.positions(
            wallet.proxy_wallet, limit=positions_limit
        )
    return positions


def qualify_all(
    wallets: list[LeaderboardWallet],
    positions_by_wallet: dict[str, list[Position]],
    thresholds: QualificationThresholds,
) -> list[QualificationDecision]:
    return [
        qualify_wallet(
            proxy_wallet=wallet.proxy_wallet,
            sources=wallet.sources,
            leaderboard_rank=wallet.rank,
            leaderboard_pnl=wallet.pnl,
            leaderboard_vol=wallet.volume,
            positions=positions_by_wallet.get(wallet.proxy_wallet, []),
            thresholds=thresholds,
        )
        for wallet in wallets
    ]


async def _write_positions(
    ch: object,
    wallets: list[LeaderboardWallet],
    positions_by_wallet: dict[str, list[Position]],
    *,
    observed_at,
) -> int:
    rows = []
    for wallet in wallets:
        for position in positions_by_wallet.get(wallet.proxy_wallet, []):
            rows.append(
                smart_money_position_row(
                    position=position,
                    leaderboard_rank=wallet.rank,
                    leaderboard_pnl=wallet.pnl,
                    leaderboard_vol=wallet.volume,
                    observed_at=observed_at,
                )
            )
    if not rows:
        return 0
    await ch.insert("positions_smart_money", rows, column_names=SMART_MONEY_POSITIONS_COLS)
    return len(rows)


async def _write_qualifications(
    ch: object,
    decisions: list[QualificationDecision],
    *,
    observed_at,
) -> int:
    if not decisions:
        return 0
    rows = [
        smart_money_qualification_row(
            proxy_wallet=decision.proxy_wallet,
            qualified=decision.qualified,
            reasons=list(decision.reasons),
            distinct_markets=decision.distinct_markets,
            leaderboard_rank=decision.leaderboard_rank,
            leaderboard_pnl=decision.leaderboard_pnl,
            leaderboard_vol=decision.leaderboard_vol,
            sources=list(decision.sources),
            appeared_in_pnl=decision.appeared_in_pnl,
            appeared_in_vol=decision.appeared_in_vol,
            min_volume_ok=decision.min_volume_ok,
            observed_at=observed_at,
        )
        for decision in decisions
    ]
    await ch.insert(
        "smart_money_qualifications", rows, column_names=SMART_MONEY_QUALIFICATIONS_COLS
    )
    return len(rows)


async def _load_prior_positions(
    ch: object,
    *,
    qualified_wallets: set[str],
    strictly_before: object,
) -> list[PositionSnapshot]:
    """Latest positions_smart_money row per (wallet, condition_id, outcome) strictly
    before ``strictly_before``, restricted to qualified wallets.
    """
    if not qualified_wallets:
        return []
    query = """
        SELECT proxy_wallet, condition_id, token_id, outcome, size,
               avg_entry_price, current_value_usdc
        FROM positions_smart_money
        WHERE proxy_wallet IN {wallets:Array(String)}
          AND observed_at < {asof:DateTime64(3)}
        ORDER BY proxy_wallet, condition_id, outcome, observed_at DESC
        LIMIT 1 BY proxy_wallet, condition_id, outcome
    """
    result = await ch.query(  # type: ignore[attr-defined]
        query,
        parameters={"wallets": list(qualified_wallets), "asof": strictly_before},
    )
    out: list[PositionSnapshot] = []
    for row in result.result_rows:
        size = float(row[4])
        if size <= 0.0:
            continue
        out.append(
            PositionSnapshot(
                proxy_wallet=str(row[0]),
                condition_id=str(row[1]),
                token_id=str(row[2]),
                outcome=str(row[3]).upper(),
                size=size,
                size_usdc=float(row[6]),
                avg_entry_price=float(row[5]),
            )
        )
    return out


async def _write_signal_events(
    ch: object,
    events: list[WhaleFlowEvent],
    *,
    observed_at,
) -> int:
    if not events:
        return 0
    rows = [
        signal_event_row(
            event_id=event.event_id,
            event_type=event.event_type,
            condition_id=event.condition_id,
            severity=event.severity,
            actor=event.proxy_wallet,
            direction=event.direction,
            size_delta_usdc=event.size_delta_usdc,
            prior_size_usdc=event.prior_size_usdc,
            current_size_usdc=event.current_size_usdc,
            payload={
                "token_id": event.token_id,
                "outcome": event.outcome,
            },
            event_time=event.event_time,
            observed_at=observed_at,
        )
        for event in events
    ]
    await ch.insert("signal_events", rows, column_names=SIGNAL_EVENTS_COLS)  # type: ignore[attr-defined]
    return len(rows)


async def _write_per_market(
    ch: object,
    positions_by_wallet: dict[str, list[Position]],
    qualified: set[str],
    *,
    observed_at,
) -> int:
    aggregates = aggregate_per_market(positions_by_wallet, qualified)
    if not aggregates:
        return 0
    rows = [
        smart_money_per_market_row(
            condition_id=aggregate.condition_id,
            yes_wallets=aggregate.yes_wallets,
            no_wallets=aggregate.no_wallets,
            yes_size=aggregate.yes_size,
            no_size=aggregate.no_size,
            yes_size_usdc=aggregate.yes_size_usdc,
            no_size_usdc=aggregate.no_size_usdc,
            yes_avg_entry=aggregate.yes_avg_entry,
            no_avg_entry=aggregate.no_avg_entry,
            sample_wallets=aggregate.sample_wallets,
            observed_at=observed_at,
        )
        for aggregate in aggregates
    ]
    await ch.insert(
        "smart_money_per_market", rows, column_names=SMART_MONEY_PER_MARKET_COLS
    )
    return len(rows)


async def run_once(
    pm: PolymarketClient,
    *,
    leaderboard_limit: int | None = None,
    positions_limit: int | None = None,
    thresholds: QualificationThresholds | None = None,
    whale_thresholds: WhaleFlowThresholds | None = None,
) -> RefreshStats:
    settings = get_settings()
    leaderboard_limit = leaderboard_limit or settings.smart_money_leaderboard_limit
    positions_limit = positions_limit or settings.smart_money_positions_limit
    thresholds = thresholds or QualificationThresholds(
        min_volume=settings.smart_money_min_volume,
        min_distinct_markets=settings.smart_money_min_distinct_markets,
    )
    whale_thresholds = whale_thresholds or WhaleFlowThresholds(
        open_threshold_usdc=settings.whale_flow_open_threshold_usdc,
        resize_pct=settings.whale_flow_resize_pct,
    )

    wallets = await fetch_leaderboard_wallets(pm, limit=leaderboard_limit)
    positions_by_wallet = await fetch_positions_for_wallets(
        pm, wallets, positions_limit=positions_limit
    )
    decisions = qualify_all(wallets, positions_by_wallet, thresholds)
    qualified_wallets = {
        decision.proxy_wallet for decision in decisions if decision.qualified
    }
    observed_at = utcnow()

    ch = await get_async_client()
    try:
        # Load the PREVIOUS pass's snapshots BEFORE writing the current pass,
        # so the diff sees the real predecessor.
        prior_positions = await _load_prior_positions(
            ch,
            qualified_wallets=qualified_wallets,
            strictly_before=observed_at,
        )

        position_rows = await _write_positions(
            ch, wallets, positions_by_wallet, observed_at=observed_at
        )
        await _write_qualifications(ch, decisions, observed_at=observed_at)
        per_market_rows = await _write_per_market(
            ch, positions_by_wallet, qualified_wallets, observed_at=observed_at
        )

        current_positions: list[PositionSnapshot] = []
        for wallet, positions in positions_by_wallet.items():
            if wallet not in qualified_wallets:
                continue
            current_positions.extend(positions_from_polymarket(positions))
        whale_events = detect_whale_flow(
            current=current_positions,
            prior=prior_positions,
            qualified_wallets=qualified_wallets,
            observed_at=observed_at,
            thresholds=whale_thresholds,
        )
        whale_rows_written = await _write_signal_events(
            ch, whale_events, observed_at=observed_at
        )

        log.info(
            "smart_money.refresh.done",
            wallets=len(wallets),
            qualified=len(qualified_wallets),
            position_rows=position_rows,
            per_market_rows=per_market_rows,
            whale_events=whale_rows_written,
        )
        return RefreshStats(
            wallets=len(wallets),
            qualified_wallets=len(qualified_wallets),
            position_rows=position_rows,
            per_market_rows=per_market_rows,
            whale_events=whale_rows_written,
        )
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
