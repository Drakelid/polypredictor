"""Public-trades sweeper for markets not covered by the WSS hot path.

The market WebSocket is the preferred source for real-time trades on the most
important markets. This worker periodically backfills the rest via the public
``/trades`` endpoint, maintaining a per-market cursor so repeated sweeps don't
re-insert the same trade ids each interval.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from clickhouse_connect.driver.asyncclient import AsyncClient
from polymarket_client import PolymarketClient, Trade

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..health import HealthSink
from ..markets_registry import MarketRef, MarketsRegistry
from ..settings import get_settings
from ..writers import TRADES_COLS, trade_row, utcnow

log = structlog.get_logger(__name__)


@dataclass
class TradeCursor:
    last_event_time: datetime | None = None
    trade_ids_at_last_time: set[str] = field(default_factory=set)


def _event_time(trade: Trade, observed_at: datetime) -> datetime:
    ts = trade.timestamp or observed_at
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _sort_key(trade: Trade, observed_at: datetime) -> tuple[datetime, str]:
    return (_event_time(trade, observed_at), str(trade.trade_id or ""))


def _new_trades_since(
    trades: list[Trade],
    *,
    cursor: TradeCursor,
    observed_at: datetime,
) -> list[Trade]:
    ordered = sorted(trades, key=lambda trade: _sort_key(trade, observed_at))
    out: list[Trade] = []
    seen_ids: set[str] = set()
    for trade in ordered:
        trade_id = str(trade.trade_id or "")
        if not trade_id or trade_id in seen_ids:
            continue
        seen_ids.add(trade_id)
        event_time = _event_time(trade, observed_at)
        if cursor.last_event_time is None or event_time > cursor.last_event_time:
            out.append(trade)
            continue
        if event_time == cursor.last_event_time and trade_id not in cursor.trade_ids_at_last_time:
            out.append(trade)
    return out


def _advance_cursor(
    cursor: TradeCursor,
    *,
    trades: list[Trade],
    observed_at: datetime,
) -> None:
    valid_trades = [trade for trade in trades if trade.trade_id]
    if not valid_trades:
        return
    latest_time = max(_event_time(trade, observed_at) for trade in valid_trades)
    latest_ids = {
        str(trade.trade_id)
        for trade in valid_trades
        if _event_time(trade, observed_at) == latest_time
    }
    if cursor.last_event_time == latest_time:
        cursor.trade_ids_at_last_time |= latest_ids
    else:
        cursor.last_event_time = latest_time
        cursor.trade_ids_at_last_time = latest_ids


async def load_cursor(ch: AsyncClient, condition_id: str) -> TradeCursor:
    """Bootstrap cursor from the latest persisted trades for a market."""
    query = """
        SELECT trade_id, event_time
        FROM trades_raw
        WHERE condition_id = {condition_id:String}
        ORDER BY event_time DESC, trade_id DESC
        LIMIT 50
    """
    result = await ch.query(query, parameters={"condition_id": condition_id})
    if not result.result_rows:
        return TradeCursor()
    latest_time = result.result_rows[0][1]
    ids = {
        str(trade_id)
        for trade_id, event_time in result.result_rows
        if event_time == latest_time and trade_id
    }
    return TradeCursor(last_event_time=latest_time, trade_ids_at_last_time=ids)


def _rows_for_trades(
    ref: MarketRef,
    trades: list[Trade],
    *,
    observed_at: datetime,
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for trade in trades:
        trade_id = str(trade.trade_id or "")
        if not trade_id:
            continue
        event_time = _event_time(trade, observed_at)
        rows.append(
            trade_row(
                trade_id=trade_id,
                token_id=trade.token_id,
                condition_id=str(trade.market or ref.condition_id),
                price=float(trade.price),
                size=float(trade.size),
                side=str(trade.side).upper(),
                aggressor_side=None,
                crossed_spread=None,
                event_time=event_time,
                observed_at=observed_at,
            )
        )
    return rows


async def sweep_one_market(
    pm: PolymarketClient,
    ch: AsyncClient,
    ref: MarketRef,
    *,
    cursor: TradeCursor,
    limit: int,
) -> int:
    observed_at = utcnow()
    trades = await pm.clob.trades(market=ref.condition_id, limit=limit)
    fresh = _new_trades_since(trades, cursor=cursor, observed_at=observed_at)
    if not fresh:
        _advance_cursor(cursor, trades=trades, observed_at=observed_at)
        return 0
    rows = _rows_for_trades(ref, fresh, observed_at=observed_at)
    if rows:
        await ch.insert("trades_raw", rows, column_names=TRADES_COLS)
    _advance_cursor(cursor, trades=trades, observed_at=observed_at)
    return len(rows)


async def run_once(
    pm: PolymarketClient,
    registry: MarketsRegistry,
    *,
    ch: AsyncClient,
    dlq: DeadLetterQueue,
    cursors: dict[str, TradeCursor],
    limit: int,
    skip_top_n: int,
) -> int:
    refs = await registry.tail_by_volume(skip_top_n)
    inserted = 0
    for ref in refs:
        try:
            cursor = cursors.get(ref.condition_id)
            if cursor is None:
                cursor = await load_cursor(ch, ref.condition_id)
                cursors[ref.condition_id] = cursor
            inserted += await sweep_one_market(
                pm,
                ch,
                ref,
                cursor=cursor,
                limit=limit,
            )
        except Exception as exc:
            log.warning(
                "trades.sweeper_failed",
                condition_id=ref.condition_id,
                error=repr(exc),
            )
            await dlq.push(
                source="polymarket.clob",
                reason="public_trades_sweep_failed",
                payload={"condition_id": ref.condition_id, "error": repr(exc)},
            )
    if refs:
        log.info("trades.sweeper_done", markets=len(refs), rows=inserted)
    return inserted


async def run_forever(registry: MarketsRegistry | None = None) -> None:
    logging.basicConfig(level=get_settings().log_level)
    s = get_settings()
    registry = registry or MarketsRegistry()
    ch = await get_async_client()
    health_ch = await get_async_client()
    health = HealthSink(health_ch)
    dlq = DeadLetterQueue(s.redis_url, stream="trades_sweeper")
    cursors: dict[str, TradeCursor] = {}
    try:
        async with PolymarketClient(
            gamma_base_url=s.polymarket_gamma_base,
            clob_base_url=s.polymarket_clob_base,
            data_base_url=s.polymarket_data_base,
            health_cb=health,
        ) as pm:
            if registry.size() == 0:
                from .gamma_discovery import run_once as gamma_once

                await gamma_once(pm, registry)

            while True:
                await run_once(
                    pm,
                    registry,
                    ch=ch,
                    dlq=dlq,
                    cursors=cursors,
                    limit=s.trades_sweeper_limit,
                    skip_top_n=s.trades_sweeper_skip_top_n,
                )
                await asyncio.sleep(s.trades_sweeper_interval_s)
    finally:
        await dlq.aclose()
        await ch.close()
        await health_ch.close()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
