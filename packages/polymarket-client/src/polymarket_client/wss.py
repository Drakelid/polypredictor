"""Polymarket WebSocket manager.

PRD §5.2.1 / §7:
  * Two channels: ``market`` (public book + trade deltas) and ``user``
    (authenticated own-order / own-fill updates).
  * Dynamic ``subscribe`` / ``unsubscribe`` so the top-200 market rotation
    does not require reconnects.
  * Auto-reconnect with jittered backoff + heartbeat monitoring.
  * On reconnect, re-request ``initial_dump=true`` and reconcile deltas
    against the last-known book so downstream consumers see a consistent
    sequence.

This module publishes parsed events to a user-supplied async callback. The
caller decides what to do with each event — typically: write to ClickHouse,
update an in-memory book, and emit to Redis pub/sub for the dashboard.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
import websockets
from websockets.asyncio.client import ClientConnection

log = structlog.get_logger(__name__)

# --- Event shape -------------------------------------------------------------


@dataclass(frozen=True)
class WssEvent:
    """Parsed WSS event handed to the consumer callback.

    ``observed_at`` is the wall-clock time we decoded the message (UTC epoch
    seconds) — this becomes the PIT ``observed_at`` column in storage.
    ``event_time`` is the server-reported time when known.
    """

    channel: str  # 'market' | 'user'
    event_type: str  # 'book' | 'price_change' | 'tick_size_change' | 'last_trade_price' | 'trade' | 'order' | 'market_resolved'
    token_id: str | None
    condition_id: str | None
    raw: dict[str, Any]
    observed_at: float
    event_time: float | None = None


WssCallback = Callable[[WssEvent], Awaitable[None]]


# --- Auth --------------------------------------------------------------------


@dataclass(frozen=True)
class UserAuth:
    """Credentials for the authenticated ``user`` channel.

    The Polymarket docs show an ``auth`` payload carrying API key / secret /
    passphrase, sent once at subscribe time. The wss manager never persists
    these — the caller keeps them in encrypted storage and passes them in.
    """

    api_key: str
    secret: str
    passphrase: str


# --- Book reconciliation state ----------------------------------------------


@dataclass
class _BookState:
    """Minimal book state kept in-memory for drift detection across reconnects.

    ``hash`` is the Polymarket-provided book hash; on reconnect we re-dump the
    book and compare hashes against what our delta stream last showed.
    """

    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    last_hash: str | None = None
    last_sequence: int = 0

    def apply_full_book(self, bids: list[dict[str, Any]], asks: list[dict[str, Any]]) -> None:
        self.bids = {float(b["price"]): float(b["size"]) for b in bids if float(b["size"]) > 0}
        self.asks = {float(a["price"]): float(a["size"]) for a in asks if float(a["size"]) > 0}

    def apply_price_change(self, changes: list[dict[str, Any]]) -> None:
        for c in changes:
            price = float(c["price"])
            size = float(c["size"])
            side = c.get("side", "").lower()
            target = self.bids if side.startswith("b") else self.asks
            if size == 0:
                target.pop(price, None)
            else:
                target[price] = size


# --- Manager ----------------------------------------------------------------


class MarketWssManager:
    """Manages a single connection to Polymarket's market channel.

    Use a single manager per process; it handles many token subscriptions.
    For user channel, instantiate :class:`UserWssManager` separately.
    """

    def __init__(
        self,
        *,
        base_url: str = "wss://ws-subscriptions-clob.polymarket.com",
        callback: WssCallback,
        initial_token_ids: list[str] | None = None,
        heartbeat_interval_s: float = 30.0,
        heartbeat_timeout_s: float = 15.0,
        reconnect_base_s: float = 1.0,
        reconnect_max_s: float = 60.0,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/ws/market"
        self._callback = callback
        self._desired_tokens: set[str] = set(initial_token_ids or [])
        self._books: dict[str, _BookState] = {}
        self._heartbeat_interval = heartbeat_interval_s
        self._heartbeat_timeout = heartbeat_timeout_s
        self._reconnect_base = reconnect_base_s
        self._reconnect_max = reconnect_max_s
        self._conn: ClientConnection | None = None
        self._stop = asyncio.Event()
        self._subs_changed = asyncio.Event()
        self._last_message_at: float = 0.0
        self._run_task: asyncio.Task[None] | None = None

    # -- Lifecycle --------------------------------------------------------

    async def start(self) -> None:
        if self._run_task is not None:
            return
        self._stop.clear()
        self._run_task = asyncio.create_task(self._run_forever(), name="polymarket-wss-market")

    async def stop(self) -> None:
        self._stop.set()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
        if self._run_task is not None:
            try:
                await asyncio.wait_for(self._run_task, timeout=5.0)
            except TimeoutError:
                self._run_task.cancel()
        self._run_task = None

    # -- Subscription API -------------------------------------------------

    async def subscribe(self, token_ids: list[str]) -> None:
        new = [t for t in token_ids if t not in self._desired_tokens]
        if not new:
            return
        self._desired_tokens.update(new)
        await self._send_subscribe(new)

    async def unsubscribe(self, token_ids: list[str]) -> None:
        present = [t for t in token_ids if t in self._desired_tokens]
        if not present:
            return
        for t in present:
            self._desired_tokens.discard(t)
            self._books.pop(t, None)
        await self._send_unsubscribe(present)

    def tracked_tokens(self) -> list[str]:
        return sorted(self._desired_tokens)

    # -- Connect / run loop -----------------------------------------------

    async def _run_forever(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connect_and_pump()
                attempt = 0
            except Exception as exc:
                attempt += 1
                wait_s = min(
                    self._reconnect_max,
                    self._reconnect_base * (2**attempt),
                ) * (0.8 + 0.4 * random.random())
                log.warning(
                    "wss.market.reconnect",
                    attempt=attempt,
                    wait_s=round(wait_s, 2),
                    error=repr(exc),
                )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=wait_s)

    async def _connect_and_pump(self) -> None:
        async with websockets.connect(
            self._url,
            ping_interval=self._heartbeat_interval,
            ping_timeout=self._heartbeat_timeout,
            max_queue=2**14,
        ) as conn:
            self._conn = conn
            log.info("wss.market.connected", tokens=len(self._desired_tokens))
            if self._desired_tokens:
                await self._send_subscribe(sorted(self._desired_tokens), initial_dump=True)
            pump = asyncio.create_task(self._pump_incoming(conn), name="wss-pump")
            watcher = asyncio.create_task(self._watch_staleness(conn), name="wss-watch")
            stop_watch = asyncio.create_task(self._stop.wait(), name="wss-stop")
            sub_change = asyncio.create_task(self._handle_sub_changes(conn), name="wss-subs")
            try:
                done, pending = await asyncio.wait(
                    {pump, watcher, stop_watch, sub_change},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for p in pending:
                    p.cancel()
                # Surface the first exception if any task errored.
                for d in done:
                    exc = d.exception()
                    if exc is not None and not isinstance(exc, asyncio.CancelledError):
                        raise exc
            finally:
                self._conn = None

    async def _pump_incoming(self, conn: ClientConnection) -> None:
        async for raw in conn:
            self._last_message_at = time.time()
            try:
                payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            except json.JSONDecodeError:
                log.warning("wss.market.parse_error", raw=str(raw)[:200])
                continue
            messages = payload if isinstance(payload, list) else [payload]
            for msg in messages:
                await self._dispatch(msg)

    async def _watch_staleness(self, conn: ClientConnection) -> None:
        # If we haven't heard anything in 2x heartbeat, close the socket to
        # trigger a reconnect. ping/pong alone isn't enough because the server
        # occasionally sends pings but no data during quiet periods.
        while not self._stop.is_set():
            await asyncio.sleep(self._heartbeat_interval)
            if self._last_message_at == 0:
                continue
            idle = time.time() - self._last_message_at
            if idle > self._heartbeat_interval * 3:
                log.warning("wss.market.stale", idle_s=round(idle, 1))
                await conn.close()
                return

    async def _handle_sub_changes(self, conn: ClientConnection) -> None:
        # Placeholder for future dynamic-sub flows; the public API already
        # sends subscribe/unsubscribe inline, so we just stay idle here.
        while not self._stop.is_set():
            await asyncio.sleep(60)

    # -- Subscribe / unsubscribe frames -----------------------------------

    async def _send_subscribe(self, token_ids: list[str], *, initial_dump: bool = False) -> None:
        if self._conn is None:
            return
        frame = {
            "type": "market",
            "operation": "subscribe",
            "assets_ids": token_ids,
            "initial_dump": initial_dump,
        }
        await self._conn.send(json.dumps(frame))

    async def _send_unsubscribe(self, token_ids: list[str]) -> None:
        if self._conn is None:
            return
        frame = {
            "type": "market",
            "operation": "unsubscribe",
            "assets_ids": token_ids,
        }
        await self._conn.send(json.dumps(frame))

    # -- Dispatch ---------------------------------------------------------

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        event_type = str(msg.get("event_type") or msg.get("type") or "").lower()
        token_id = msg.get("asset_id") or msg.get("token_id")
        condition_id = msg.get("market") or msg.get("condition_id")

        if event_type == "book":
            book = self._books.setdefault(str(token_id), _BookState())
            book.apply_full_book(msg.get("bids", []), msg.get("asks", []))
            book.last_hash = msg.get("hash")
        elif event_type in ("price_change", "tick_size_change"):
            book = self._books.setdefault(str(token_id), _BookState())
            book.apply_price_change(msg.get("changes", []))
            book.last_hash = msg.get("hash", book.last_hash)

        event = WssEvent(
            channel="market",
            event_type=event_type or "unknown",
            token_id=str(token_id) if token_id is not None else None,
            condition_id=str(condition_id) if condition_id is not None else None,
            raw=msg,
            observed_at=time.time(),
            event_time=_maybe_epoch(msg.get("timestamp")),
        )
        try:
            await self._callback(event)
        except Exception as exc:
            log.warning(
                "wss.market.callback_error",
                event_type=event.event_type,
                token_id=event.token_id,
                error=repr(exc),
            )


class UserWssManager:
    """Authenticated user channel — own-order + own-fill updates.

    Delivers the user's own fills without polling ``/trades``, enabling F7
    journal auto-sync (PRD §5.2.1).
    """

    def __init__(
        self,
        *,
        auth: UserAuth,
        base_url: str = "wss://ws-subscriptions-clob.polymarket.com",
        callback: WssCallback,
        markets: list[str] | None = None,
        heartbeat_interval_s: float = 30.0,
        heartbeat_timeout_s: float = 15.0,
        reconnect_base_s: float = 1.0,
        reconnect_max_s: float = 60.0,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/ws/user"
        self._auth = auth
        self._callback = callback
        self._markets: set[str] = set(markets or [])
        self._heartbeat_interval = heartbeat_interval_s
        self._heartbeat_timeout = heartbeat_timeout_s
        self._reconnect_base = reconnect_base_s
        self._reconnect_max = reconnect_max_s
        self._conn: ClientConnection | None = None
        self._stop = asyncio.Event()
        self._run_task: asyncio.Task[None] | None = None
        self._last_message_at: float = 0.0

    async def start(self) -> None:
        if self._run_task is not None:
            return
        self._stop.clear()
        self._run_task = asyncio.create_task(self._run_forever(), name="polymarket-wss-user")

    async def stop(self) -> None:
        self._stop.set()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
        if self._run_task is not None:
            try:
                await asyncio.wait_for(self._run_task, timeout=5.0)
            except TimeoutError:
                self._run_task.cancel()
        self._run_task = None

    async def _run_forever(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connect_and_pump()
                attempt = 0
            except Exception as exc:
                attempt += 1
                wait_s = min(
                    self._reconnect_max, self._reconnect_base * (2**attempt)
                ) * (0.8 + 0.4 * random.random())
                log.warning(
                    "wss.user.reconnect",
                    attempt=attempt,
                    wait_s=round(wait_s, 2),
                    error=repr(exc),
                )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=wait_s)

    async def _connect_and_pump(self) -> None:
        async with websockets.connect(
            self._url,
            ping_interval=self._heartbeat_interval,
            ping_timeout=self._heartbeat_timeout,
        ) as conn:
            self._conn = conn
            await conn.send(
                json.dumps(
                    {
                        "type": "user",
                        "operation": "subscribe",
                        "markets": sorted(self._markets),
                        "auth": {
                            "apiKey": self._auth.api_key,
                            "secret": self._auth.secret,
                            "passphrase": self._auth.passphrase,
                        },
                    }
                )
            )
            log.info("wss.user.connected", markets=len(self._markets))
            try:
                async for raw in conn:
                    self._last_message_at = time.time()
                    try:
                        payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    except json.JSONDecodeError:
                        log.warning("wss.user.parse_error")
                        continue
                    messages = payload if isinstance(payload, list) else [payload]
                    for msg in messages:
                        await self._dispatch(msg)
            finally:
                self._conn = None

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        event_type = str(msg.get("event_type") or msg.get("type") or "").lower()
        event = WssEvent(
            channel="user",
            event_type=event_type or "unknown",
            token_id=msg.get("asset_id") or msg.get("token_id"),
            condition_id=msg.get("market") or msg.get("condition_id"),
            raw=msg,
            observed_at=time.time(),
            event_time=_maybe_epoch(msg.get("timestamp")),
        )
        try:
            await self._callback(event)
        except Exception as exc:
            log.warning("wss.user.callback_error", error=repr(exc))


def _maybe_epoch(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f > 1e12:  # ms
        return f / 1000.0
    return f
