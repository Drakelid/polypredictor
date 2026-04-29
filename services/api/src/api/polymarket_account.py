"""Read-only Polymarket account linkage for M7.1.

This module persists a single connected Polymarket proxy wallet for the demo
user and validates it by fetching the public account surfaces that require no
credentials:

* ``/positions``
* ``/trades?user=...``
* ``/users/{address}/earnings``

The write path remains read-only by design. No trading endpoints or private
credentials are used here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from asyncpg import Pool
from polymarket_client import DataClient

from .settings import Settings
from .users import ensure_user_by_email

_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass(frozen=True)
class PolymarketTrade:
    trade_id: str | None
    condition_id: str | None
    outcome: str | None
    side: str | None
    price: float | None
    size: float | None
    timestamp: str | None


@dataclass(frozen=True)
class PolymarketAccountSummary:
    proxy_wallet: str
    verified_at: datetime | None
    open_positions: int
    redeemable_positions: int
    total_position_value_usdc: float
    total_earnings_usdc: float
    recent_trades: list[PolymarketTrade]


@dataclass(frozen=True)
class PolymarketAddressLink:
    proxy_wallet: str | None
    verified_at: datetime | None
    summary: PolymarketAccountSummary | None


@dataclass(frozen=True)
class PolymarketAddressInput:
    proxy_wallet: str | None


async def get_linked_address(
    *,
    pool: Pool,
    email: str,
    settings: Settings,
    data_client: DataClient | None = None,
) -> PolymarketAddressLink:
    user_id = await ensure_user_by_email(pool, email=email)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT proxy_wallet, verified_at
            FROM user_polymarket_addresses
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            user_id,
        )
    if row is None:
        return PolymarketAddressLink(
            proxy_wallet=None,
            verified_at=None,
            summary=None,
        )
    proxy_wallet = str(row["proxy_wallet"])
    verified_at = _coerce_utc(row["verified_at"]) if row["verified_at"] is not None else None
    summary = await fetch_public_account_summary(
        proxy_wallet=proxy_wallet,
        verified_at=verified_at,
        settings=settings,
        data_client=data_client,
    )
    return PolymarketAddressLink(
        proxy_wallet=proxy_wallet,
        verified_at=verified_at,
        summary=summary,
    )


async def update_linked_address(
    *,
    pool: Pool,
    email: str,
    settings: Settings,
    payload: PolymarketAddressInput,
    data_client: DataClient | None = None,
) -> PolymarketAddressLink:
    user_id = await ensure_user_by_email(pool, email=email)
    proxy_wallet = _normalize_proxy_wallet(payload.proxy_wallet)
    async with pool.acquire() as conn:
        if proxy_wallet is None:
            await conn.execute(
                "DELETE FROM user_polymarket_addresses WHERE user_id = $1",
                user_id,
            )
            return PolymarketAddressLink(
                proxy_wallet=None,
                verified_at=None,
                summary=None,
            )
        summary = await fetch_public_account_summary(
            proxy_wallet=proxy_wallet,
            verified_at=datetime.now(UTC),
            settings=settings,
            data_client=data_client,
        )
        await conn.execute(
            """
            DELETE FROM user_polymarket_addresses
            WHERE user_id = $1
            """,
            user_id,
        )
        stored = await conn.fetchrow(
            """
            INSERT INTO user_polymarket_addresses (
                user_id,
                proxy_wallet,
                verified_at
            )
            VALUES ($1, $2, $3)
            RETURNING proxy_wallet, verified_at
            """,
            user_id,
            proxy_wallet,
            datetime.now(UTC),
        )
    assert stored is not None
    verified_at = _coerce_utc(stored["verified_at"]) if stored["verified_at"] is not None else None
    return PolymarketAddressLink(
        proxy_wallet=str(stored["proxy_wallet"]),
        verified_at=verified_at,
        summary=summary,
    )


async def fetch_public_account_summary(
    *,
    proxy_wallet: str,
    verified_at: datetime | None,
    settings: Settings,
    data_client: DataClient | None = None,
) -> PolymarketAccountSummary:
    owns_client = data_client is None
    client = data_client or DataClient(base_url=settings.polymarket_data_base)
    try:
        positions = await client.positions(proxy_wallet, limit=500)
        redeemable = await client.positions(proxy_wallet, limit=500, redeemable=True)
        trades_raw = await client.user_trades(proxy_wallet, limit=20)
        earnings = await client.user_earnings(proxy_wallet)
    finally:
        if owns_client:
            await client.aclose()
    total_position_value = sum(float(position.current_value) for position in positions)
    recent_trades = [_trade_from_payload(item) for item in trades_raw[:10]]
    return PolymarketAccountSummary(
        proxy_wallet=proxy_wallet,
        verified_at=verified_at,
        open_positions=len(positions),
        redeemable_positions=len(redeemable),
        total_position_value_usdc=total_position_value,
        total_earnings_usdc=float(earnings.total_earnings),
        recent_trades=recent_trades,
    )


def _normalize_proxy_wallet(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if not _ADDRESS_RE.fullmatch(raw):
        raise ValueError("proxy_wallet must be a 0x-prefixed 40-hex-address")
    return raw.lower()


def _trade_from_payload(payload: dict[str, Any]) -> PolymarketTrade:
    trade_id = payload.get("id") or payload.get("tradeID") or payload.get("trade_id")
    condition_id = payload.get("conditionId") or payload.get("condition_id") or payload.get("market")
    outcome = payload.get("outcome")
    side = payload.get("side")
    price = _float_or_none(payload.get("price"))
    size = _float_or_none(payload.get("size"))
    timestamp = payload.get("timestamp")
    return PolymarketTrade(
        trade_id=str(trade_id) if trade_id is not None else None,
        condition_id=str(condition_id) if condition_id is not None else None,
        outcome=str(outcome) if outcome is not None else None,
        side=str(side) if side is not None else None,
        price=price,
        size=size,
        timestamp=str(timestamp) if timestamp is not None else None,
    )


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
