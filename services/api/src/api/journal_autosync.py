"""User WSS-backed journal auto-sync for Polymarket fills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from asyncpg import Pool
from clickhouse_connect.driver.asyncclient import AsyncClient
from polymarket_client import UserWssManager, WssEvent

from . import journal as journal_q
from .clob_credentials import load_user_auth
from .settings import Settings


@dataclass(frozen=True)
class ParsedUserFill:
    source_event_id: str
    condition_id: str
    token_id: str
    side: str
    price: float
    size: float
    fill_time: datetime


class UserJournalAutoSyncService:
    """Keeps a single authenticated user-channel listener alive for F7."""

    def __init__(
        self,
        *,
        pool: Pool,
        ch: AsyncClient,
        settings: Settings,
        manager_factory: type[UserWssManager] = UserWssManager,
    ) -> None:
        self._pool = pool
        self._ch = ch
        self._settings = settings
        self._manager_factory = manager_factory
        self._manager: UserWssManager | None = None

    async def refresh(self) -> None:
        # The autosync service is a system-level, single-instance listener;
        # it always operates on the demo user's CLOB credentials by design.
        auth = await load_user_auth(
            pool=self._pool,
            email=self._settings.journal_demo_user_email,
            settings=self._settings,
        )
        if auth is None:
            await self.stop()
            return
        await self.stop()
        self._manager = self._manager_factory(
            auth=auth,
            base_url=self._settings.polymarket_wss_base,
            callback=self._handle_event,
            markets=[],
        )
        await self._manager.start()

    async def stop(self) -> None:
        if self._manager is not None:
            await self._manager.stop()
            self._manager = None

    async def _handle_event(self, event: WssEvent) -> None:
        fill = parse_user_fill_event(event)
        if fill is None:
            return
        await journal_q.create_auto_fill_call(
            pool=self._pool,
            ch=self._ch,
            email=self._settings.journal_demo_user_email,
            settings=self._settings,
            payload=journal_q.AutoJournalFillInput(
                condition_id=fill.condition_id,
                token_id=fill.token_id,
                side=fill.side,
                price=fill.price,
                size=fill.size,
                source_event_id=fill.source_event_id,
            ),
            asked_at=fill.fill_time,
        )


def parse_user_fill_event(event: WssEvent) -> ParsedUserFill | None:
    raw = event.raw if isinstance(event.raw, dict) else {}
    event_type = event.event_type.lower()
    if event_type == "order":
        status = str(raw.get("status") or raw.get("orderStatus") or "").lower()
        if status not in {"filled", "matched", "executed", "complete", "completed"}:
            return None
    elif event_type not in {"trade", "fill", "matched"}:
        return None

    condition_id = _first_str(raw, "market", "condition_id", "conditionId")
    token_id = _first_str(raw, "asset_id", "token_id", "tokenId", "asset")
    side = (_first_str(raw, "side", "taker_side", "takerSide") or "").upper()
    price = _first_float(raw, "price", "matched_price", "matchedPrice")
    size = _first_float(raw, "size", "matched_size", "matchedSize", "amount")
    if not condition_id or not token_id or side not in {"BUY", "SELL"}:
        return None
    if price is None or size is None or price <= 0 or size <= 0:
        return None
    source_event_id = (
        _first_str(raw, "id", "trade_id", "tradeID", "fill_id", "fillId", "match_id", "matchId")
        or f"{condition_id}:{token_id}:{side}:{price:.6f}:{size:.6f}:{_parse_event_time(raw, event).isoformat()}"
    )
    return ParsedUserFill(
        source_event_id=source_event_id,
        condition_id=condition_id,
        token_id=token_id,
        side=side,
        price=price,
        size=size,
        fill_time=_parse_event_time(raw, event),
    )


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _first_float(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _parse_event_time(payload: dict[str, Any], event: WssEvent) -> datetime:
    for key in ("timestamp", "time", "created_at", "createdAt"):
        value = payload.get(key)
        parsed = _coerce_datetime(value)
        if parsed is not None:
            return parsed
    if event.event_time is not None:
        return datetime.fromtimestamp(event.event_time, tz=UTC)
    return datetime.fromtimestamp(event.observed_at, tz=UTC)


def _coerce_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1e12:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                timestamp = float(text)
            except ValueError:
                return None
            if timestamp > 1e12:
                timestamp /= 1000.0
            return datetime.fromtimestamp(timestamp, tz=UTC)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None
