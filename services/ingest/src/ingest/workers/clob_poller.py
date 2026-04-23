"""CLOB poller — tiered polling per PRD §5.2.1.

Top-N (by volume) markets: book + midpoint every ~5s.
Tail markets:             book + midpoint every ~60s.

Shares a :class:`MarketsRegistry` with the Gamma discovery worker so the
set of polled markets stays in sync with the active catalog.

Polled data lands in ``market_quotes`` and ``orderbook_snapshots``, both with
``observed_at = now()`` for PIT correctness.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import structlog
from polymarket_client import Book, PolymarketClient

from ..clickhouse import get_async_client
from ..dlq import DeadLetterQueue
from ..health import HealthSink
from ..markets_registry import MarketRef, MarketsRegistry
from ..settings import get_settings
from ..writers import ORDERBOOK_COLS, QUOTES_COLS, orderbook_rows, quote_row, utcnow

log = structlog.get_logger(__name__)


async def poll_one_market(
    pm: PolymarketClient,
    ref: MarketRef,
    *,
    ch: Any,
    dlq: DeadLetterQueue,
) -> None:
    """Pull book + midpoint for every token of one market and persist."""
    observed = utcnow()
    for token_id in ref.token_ids:
        try:
            book = await pm.clob.book(token_id)
            midpoint = await pm.clob.midpoint(token_id)
        except Exception as exc:
            log.warning(
                "clob.poll.failed",
                condition_id=ref.condition_id,
                token_id=token_id,
                error=repr(exc),
            )
            await dlq.push(
                source="polymarket.clob",
                reason="poll_market_failed",
                payload={"condition_id": ref.condition_id, "token_id": token_id, "error": repr(exc)},
            )
            continue

        await _persist(
            ch=ch,
            ref=ref,
            token_id=token_id,
            book=book,
            mid=midpoint.mid,
            observed=observed,
        )


async def _persist(
    *,
    ch: Any,
    ref: MarketRef,
    token_id: str,
    book: Book,
    mid: float,
    observed: datetime,
) -> None:
    best_bid = book.bids[0].price if book.bids else 0.0
    bid_size = book.bids[0].size if book.bids else 0.0
    best_ask = book.asks[0].price if book.asks else 0.0
    ask_size = book.asks[0].size if book.asks else 0.0

    quote = quote_row(
        token_id=token_id,
        condition_id=ref.condition_id,
        mid=float(mid),
        best_bid=best_bid,
        best_ask=best_ask,
        bid_size=bid_size,
        ask_size=ask_size,
        observed_at=observed,
    )
    await ch.insert("market_quotes", [quote], column_names=QUOTES_COLS)

    ob_rows = orderbook_rows(
        token_id=token_id,
        condition_id=ref.condition_id,
        bids=[(b.price, b.size) for b in book.bids],
        asks=[(a.price, a.size) for a in book.asks],
        observed_at=observed,
        source="rest",
    )
    if ob_rows:
        await ch.insert("orderbook_snapshots", ob_rows, column_names=ORDERBOOK_COLS)


async def _top_loop(
    pm: PolymarketClient,
    registry: MarketsRegistry,
    ch: Any,
    dlq: DeadLetterQueue,
    *,
    n: int,
    interval_s: float,
) -> None:
    while True:
        refs = await registry.top_n_by_volume(n)
        if not refs:
            await asyncio.sleep(interval_s)
            continue
        # Poll concurrently but lightly — rate limiter handles Polymarket-side.
        await asyncio.gather(
            *(poll_one_market(pm, r, ch=ch, dlq=dlq) for r in refs),
            return_exceptions=False,
        )
        await asyncio.sleep(interval_s)


async def _tail_loop(
    pm: PolymarketClient,
    registry: MarketsRegistry,
    ch: Any,
    dlq: DeadLetterQueue,
    *,
    skip: int,
    interval_s: float,
) -> None:
    while True:
        refs = await registry.tail_by_volume(skip)
        if not refs:
            await asyncio.sleep(interval_s)
            continue
        # Iterate serially for the tail — no need for extra concurrency.
        for r in refs:
            await poll_one_market(pm, r, ch=ch, dlq=dlq)
        await asyncio.sleep(interval_s)


async def run_forever(registry: MarketsRegistry | None = None) -> None:
    """Run the poller. If no registry is passed, this worker runs
    stand-alone and maintains its own snapshot via a one-shot discovery call —
    production deployments share a registry with the Gamma discovery worker.
    """
    logging.basicConfig(level=get_settings().log_level)
    s = get_settings()
    registry = registry or MarketsRegistry()
    ch = await get_async_client()
    health_ch = await get_async_client()
    health = HealthSink(health_ch)
    dlq = DeadLetterQueue(s.redis_url, stream="clob_poller")
    try:
        async with PolymarketClient(
            gamma_base_url=s.polymarket_gamma_base,
            clob_base_url=s.polymarket_clob_base,
            data_base_url=s.polymarket_data_base,
            health_cb=health,
        ) as pm:
            if registry.size() == 0:
                # Stand-alone bootstrap: one discovery pass so we have targets.
                from .gamma_discovery import run_once as gamma_once

                await gamma_once(pm, registry)

            await asyncio.gather(
                _top_loop(pm, registry, ch, dlq, n=s.clob_top_n, interval_s=s.clob_top_interval_s),
                _tail_loop(
                    pm,
                    registry,
                    ch,
                    dlq,
                    skip=s.clob_top_n,
                    interval_s=s.clob_tail_interval_s,
                ),
            )
    finally:
        await dlq.aclose()
        await ch.close()
        await health_ch.close()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
