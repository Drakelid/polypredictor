"""Microstructure signal producer for M3.5.

Emits two signal classes into ``signal_events``:

* ``large_print``: recent public trades whose notional clears a configured floor
* ``book_shock``: sharp moves in 1% orderbook imbalance over a short lookback

Both producers are append-only and use deterministic event ids so the worker
can safely re-scan a small recent window without duplicating feed events.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..settings import get_settings
from ..writers import SIGNAL_EVENTS_COLS, signal_event_row, utcnow
from .feature_snapshots import OrderbookLevel, _book_imbalance

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RecentTrade:
    trade_id: str
    token_id: str
    condition_id: str
    price: float
    size: float
    aggressor_side: str | None
    event_time: datetime
    observed_at: datetime

    @property
    def notional_usdc(self) -> float:
        return self.price * self.size


@dataclass(frozen=True)
class QuoteSnapshot:
    token_id: str
    condition_id: str
    best_bid: float
    best_ask: float
    observed_at: datetime


async def _latest_active_markets(
    ch: Any,
    *,
    asked_at: datetime,
    limit: int,
) -> list[tuple[str, str]]:
    query = """
        SELECT condition_id, token_ids, active, closed, archived
        FROM markets_snapshots
        WHERE observed_at <= {asof:DateTime64(3)}
        ORDER BY condition_id, observed_at DESC
        LIMIT 1 BY condition_id
        ORDER BY volume_usdc DESC
        LIMIT {limit:UInt32}
    """
    result = await ch.query(query, parameters={"asof": asked_at, "limit": limit})
    rows: list[tuple[str, str]] = []
    for condition_id, token_ids, active, closed, archived in result.result_rows:
        if not bool(active) or bool(closed) or bool(archived):
            continue
        yes_token = str(token_ids[0]) if token_ids else ""
        if yes_token:
            rows.append((str(condition_id), yes_token))
    return rows


async def _recent_trades(
    ch: Any,
    *,
    token_ids: list[str],
    start: datetime,
    asked_at: datetime,
) -> list[RecentTrade]:
    if not token_ids:
        return []
    query = """
        SELECT trade_id, token_id, condition_id, price, size, aggressor_side, event_time, observed_at
        FROM trades_raw
        WHERE token_id IN {token_ids:Array(String)}
          AND observed_at >= {start:DateTime64(3)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY observed_at DESC
    """
    result = await ch.query(
        query,
        parameters={"token_ids": token_ids, "start": start, "asof": asked_at},
    )
    return [
        RecentTrade(
            trade_id=str(trade_id),
            token_id=str(token_id),
            condition_id=str(condition_id),
            price=float(price),
            size=float(size),
            aggressor_side=(str(aggressor_side) if aggressor_side is not None else None),
            event_time=event_time,
            observed_at=observed_at,
        )
        for trade_id, token_id, condition_id, price, size, aggressor_side, event_time, observed_at in result.result_rows
    ]


async def _quotes_asof(
    ch: Any,
    *,
    token_ids: list[str],
    asked_at: datetime,
) -> dict[str, QuoteSnapshot]:
    if not token_ids:
        return {}
    query = """
        SELECT token_id, condition_id, best_bid, best_ask, observed_at
        FROM market_quotes
        WHERE token_id IN {token_ids:Array(String)}
          AND observed_at <= {asof:DateTime64(3)}
        ORDER BY token_id, observed_at DESC
        LIMIT 1 BY token_id
    """
    result = await ch.query(
        query,
        parameters={"token_ids": token_ids, "asof": asked_at},
    )
    return {
        str(token_id): QuoteSnapshot(
            token_id=str(token_id),
            condition_id=str(condition_id),
            best_bid=float(best_bid),
            best_ask=float(best_ask),
            observed_at=observed_at,
        )
        for token_id, condition_id, best_bid, best_ask, observed_at in result.result_rows
    }


async def _orderbooks_asof(
    ch: Any,
    *,
    token_ids: list[str],
    asked_at: datetime,
) -> dict[str, list[OrderbookLevel]]:
    if not token_ids:
        return {}
    query = """
        WITH latest_books AS (
            SELECT token_id, max(observed_at) AS latest_observed_at
            FROM orderbook_snapshots
            WHERE token_id IN {token_ids:Array(String)}
              AND observed_at <= {asof:DateTime64(3)}
              AND source = 'rest'
            GROUP BY token_id
        )
        SELECT ob.token_id, ob.side, ob.price, ob.size
        FROM orderbook_snapshots AS ob
        INNER JOIN latest_books AS lb
            ON ob.token_id = lb.token_id
           AND ob.observed_at = lb.latest_observed_at
        WHERE ob.source = 'rest'
        ORDER BY ob.token_id, ob.side, ob.price DESC
    """
    result = await ch.query(
        query,
        parameters={"token_ids": token_ids, "asof": asked_at},
    )
    out: dict[str, list[OrderbookLevel]] = defaultdict(list)
    for token_id, side, price, size in result.result_rows:
        out[str(token_id)].append(
            OrderbookLevel(side=str(side), price=float(price), size=float(size))
        )
    return dict(out)


async def _existing_event_ids(
    ch: Any,
    *,
    event_types: list[str],
    start: datetime,
    asked_at: datetime,
) -> set[str]:
    query = """
        SELECT event_id
        FROM signal_events
        WHERE event_type IN {types:Array(String)}
          AND observed_at >= {start:DateTime64(3)}
          AND observed_at <= {asof:DateTime64(3)}
    """
    result = await ch.query(
        query,
        parameters={"types": event_types, "start": start, "asof": asked_at},
    )
    return {str(row[0]) for row in result.result_rows}


def _large_print_event_id(trade_id: str) -> str:
    return hashlib.sha256(f"large_print|{trade_id}".encode()).hexdigest()[:32]


def _book_shock_event_id(
    *,
    condition_id: str,
    token_id: str,
    observed_at: datetime,
) -> str:
    seed = f"book_shock|{condition_id}|{token_id}|{observed_at.isoformat()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _signal_direction_from_aggressor(aggressor_side: str | None) -> str:
    if aggressor_side == "buy":
        return "yes"
    if aggressor_side == "sell":
        return "no"
    return "neutral"


async def build_signal_rows(
    ch: Any,
    *,
    observed_at: datetime,
    batch_size: int,
    large_print_min_notional_usdc: float,
    book_shock_lookback: timedelta,
    book_shock_min_delta: float,
) -> list[tuple[object, ...]]:
    markets = await _latest_active_markets(ch, asked_at=observed_at, limit=batch_size)
    token_ids = [token_id for _, token_id in markets]
    if not token_ids:
        return []

    trade_window = max(timedelta(minutes=5), book_shock_lookback)
    recent_trades = await _recent_trades(
        ch,
        token_ids=token_ids,
        start=observed_at - trade_window,
        asked_at=observed_at,
    )
    current_quotes = await _quotes_asof(ch, token_ids=token_ids, asked_at=observed_at)
    prior_cutoff = observed_at - book_shock_lookback
    prior_quotes = await _quotes_asof(ch, token_ids=token_ids, asked_at=prior_cutoff)
    current_books = await _orderbooks_asof(ch, token_ids=token_ids, asked_at=observed_at)
    prior_books = await _orderbooks_asof(ch, token_ids=token_ids, asked_at=prior_cutoff)
    existing_ids = await _existing_event_ids(
        ch,
        event_types=["large_print", "book_shock"],
        start=observed_at - timedelta(days=7),
        asked_at=observed_at,
    )

    rows: list[tuple[object, ...]] = []

    for trade in recent_trades:
        if trade.notional_usdc < large_print_min_notional_usdc:
            continue
        event_id = _large_print_event_id(trade.trade_id)
        if event_id in existing_ids:
            continue
        rows.append(
            signal_event_row(
                event_id=event_id,
                event_type="large_print",
                condition_id=trade.condition_id,
                severity=(
                    trade.notional_usdc / large_print_min_notional_usdc
                    if large_print_min_notional_usdc > 0
                    else trade.notional_usdc
                ),
                actor="market_tape",
                direction=_signal_direction_from_aggressor(trade.aggressor_side),
                size_delta_usdc=trade.notional_usdc,
                prior_size_usdc=0.0,
                current_size_usdc=trade.notional_usdc,
                payload={
                    "token_id": trade.token_id,
                    "trade_id": trade.trade_id,
                    "price": trade.price,
                    "size": trade.size,
                    "notional_usdc": trade.notional_usdc,
                    "aggressor_side": trade.aggressor_side,
                    "trade_observed_at": trade.observed_at.isoformat(),
                },
                event_time=trade.event_time,
                observed_at=observed_at,
            )
        )
        existing_ids.add(event_id)

    for token_id in token_ids:
        current_quote = current_quotes.get(token_id)
        prior_quote = prior_quotes.get(token_id)
        if current_quote is None or prior_quote is None:
            continue
        current_levels = current_books.get(token_id, [])
        prior_levels = prior_books.get(token_id, [])
        current_imbalance = _book_imbalance(
            current_levels,
            best_bid=current_quote.best_bid,
            best_ask=current_quote.best_ask,
            depth_pct=0.01,
        )
        prior_imbalance = _book_imbalance(
            prior_levels,
            best_bid=prior_quote.best_bid,
            best_ask=prior_quote.best_ask,
            depth_pct=0.01,
        )
        if current_imbalance is None or prior_imbalance is None:
            continue
        delta = current_imbalance - prior_imbalance
        if abs(delta) < book_shock_min_delta:
            continue
        event_id = _book_shock_event_id(
            condition_id=current_quote.condition_id,
            token_id=token_id,
            observed_at=current_quote.observed_at,
        )
        if event_id in existing_ids:
            continue
        rows.append(
            signal_event_row(
                event_id=event_id,
                event_type="book_shock",
                condition_id=current_quote.condition_id,
                severity=(
                    abs(delta) / book_shock_min_delta
                    if book_shock_min_delta > 0
                    else abs(delta)
                ),
                actor="orderbook",
                direction="yes" if delta > 0 else "no",
                size_delta_usdc=None,
                prior_size_usdc=None,
                current_size_usdc=None,
                payload={
                    "token_id": token_id,
                    "prior_imbalance_1pct": prior_imbalance,
                    "current_imbalance_1pct": current_imbalance,
                    "delta_imbalance_1pct": delta,
                    "prior_observed_at": prior_quote.observed_at.isoformat(),
                    "current_observed_at": current_quote.observed_at.isoformat(),
                    "current_best_bid": current_quote.best_bid,
                    "current_best_ask": current_quote.best_ask,
                },
                event_time=current_quote.observed_at,
                observed_at=observed_at,
            )
        )
        existing_ids.add(event_id)

    rows.sort(key=lambda row: (float(row[3]), str(row[10])), reverse=True)
    return rows


async def run_once() -> int:
    settings = get_settings()
    ch = await get_async_client()
    try:
        observed_at = utcnow()
        rows = await build_signal_rows(
            ch,
            observed_at=observed_at,
            batch_size=settings.microstructure_signals_max_markets,
            large_print_min_notional_usdc=settings.large_print_min_notional_usdc,
            book_shock_lookback=timedelta(minutes=settings.book_shock_lookback_minutes),
            book_shock_min_delta=settings.book_shock_min_delta,
        )
        if not rows:
            log.info("microstructure_signals.done", events=0)
            return 0
        await ch.insert("signal_events", rows, column_names=SIGNAL_EVENTS_COLS)
        log.info("microstructure_signals.done", events=len(rows))
        return len(rows)
    finally:
        await ch.close()


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    settings = get_settings()
    dlq = DeadLetterQueue(settings.redis_url, stream="microstructure_signals")
    try:
        while True:
            try:
                await run_once()
            except Exception as exc:
                log.error("microstructure_signals.failed", error=repr(exc))
                await dlq.push(
                    source="microstructure.signals",
                    reason="microstructure_signals_failed",
                    payload={"error": repr(exc)},
                )
            await asyncio.sleep(settings.microstructure_signals_interval_s)
    finally:
        await dlq.aclose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
