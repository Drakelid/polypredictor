"""WSS bridge — subscribes to the Polymarket market channel and persists
trades + orderbook deltas to ClickHouse with PIT columns.

Subscribes to every token in the active :class:`MarketsRegistry`. When the
registry changes (Gamma discovery pass updates it), the bridge reconciles
subscriptions without reconnecting.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

import structlog
from polymarket_client import MarketWssManager, WssEvent

from ..clickhouse import get_async_client
from ..markets_registry import MarketsRegistry
from ..settings import get_settings
from ..writers import ORDERBOOK_COLS, TRADES_COLS, orderbook_rows, trade_row

log = structlog.get_logger(__name__)


class WssBridge:
    def __init__(self, registry: MarketsRegistry) -> None:
        self._registry = registry
        self._ch = None
        self._mgr: MarketWssManager | None = None
        self._reconcile_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        s = get_settings()
        self._ch = await get_async_client()
        self._mgr = MarketWssManager(
            base_url=s.polymarket_wss_base,
            callback=self._on_event,
        )
        await self._mgr.start()
        self._reconcile_task = asyncio.create_task(
            self._reconcile_loop(), name="wss-reconcile"
        )

    async def stop(self) -> None:
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconcile_task
            self._reconcile_task = None
        if self._mgr is not None:
            await self._mgr.stop()
        if self._ch is not None:
            await self._ch.close()

    async def _reconcile_loop(self) -> None:
        """Every 30s, diff the registry vs our subscribed tokens and send
        subscribe/unsubscribe frames for the delta.
        """
        while True:
            try:
                refs = await self._registry.all()
                desired = {t for r in refs for t in r.token_ids}
                if self._mgr is None:
                    await asyncio.sleep(30)
                    continue
                current = set(self._mgr.tracked_tokens())
                to_add = sorted(desired - current)
                to_drop = sorted(current - desired)
                if to_add:
                    await self._mgr.subscribe(to_add)
                if to_drop:
                    await self._mgr.unsubscribe(to_drop)
            except Exception as exc:
                log.warning("wss.bridge.reconcile_failed", error=repr(exc))
            await asyncio.sleep(30)

    async def _on_event(self, ev: WssEvent) -> None:
        if self._ch is None:
            return
        observed = datetime.fromtimestamp(ev.observed_at, tz=UTC)
        event_time = (
            datetime.fromtimestamp(ev.event_time, tz=UTC) if ev.event_time else observed
        )

        if ev.event_type == "book":
            bids = [(float(b["price"]), float(b["size"])) for b in ev.raw.get("bids", [])]
            asks = [(float(a["price"]), float(a["size"])) for a in ev.raw.get("asks", [])]
            rows = orderbook_rows(
                token_id=ev.token_id or "",
                condition_id=ev.condition_id or "",
                bids=bids,
                asks=asks,
                observed_at=observed,
                source="wss_init",
            )
            if rows:
                await self._ch.insert(
                    "orderbook_snapshots", rows, column_names=ORDERBOOK_COLS
                )

        elif ev.event_type == "price_change":
            # Delta levels — write only the changed levels.
            bids: list[tuple[float, float]] = []
            asks: list[tuple[float, float]] = []
            for c in ev.raw.get("changes", []):
                side = str(c.get("side", "")).lower()
                pair = (float(c["price"]), float(c["size"]))
                if side.startswith("b"):
                    bids.append(pair)
                else:
                    asks.append(pair)
            rows = orderbook_rows(
                token_id=ev.token_id or "",
                condition_id=ev.condition_id or "",
                bids=bids,
                asks=asks,
                observed_at=observed,
                source="wss_delta",
            )
            if rows:
                await self._ch.insert(
                    "orderbook_snapshots", rows, column_names=ORDERBOOK_COLS
                )

        elif ev.event_type in ("trade", "last_trade_price"):
            t = ev.raw
            trade_id = str(t.get("trade_id") or t.get("id") or "")
            if not trade_id:
                return
            row = trade_row(
                trade_id=trade_id,
                token_id=ev.token_id or "",
                condition_id=ev.condition_id or "",
                price=float(t.get("price", 0.0)),
                size=float(t.get("size", 0.0)),
                side=str(t.get("side", "BUY")).upper(),
                aggressor_side=_opt_str(t.get("aggressor_side")),
                crossed_spread=_opt_bool(t.get("crossed_spread")),
                event_time=event_time,
                observed_at=observed,
            )
            await self._ch.insert("trades_raw", [row], column_names=TRADES_COLS)


def _opt_str(v: object) -> str | None:
    return str(v) if isinstance(v, str) and v else None


def _opt_bool(v: object) -> bool | None:
    if isinstance(v, bool):
        return v
    return None


async def run_forever() -> None:
    logging.basicConfig(level=get_settings().log_level)
    registry = MarketsRegistry()
    bridge = WssBridge(registry)
    await bridge.start()
    try:
        # Keep the process alive; registry updates come from the Gamma worker.
        while True:
            await asyncio.sleep(3600)
    finally:
        await bridge.stop()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
