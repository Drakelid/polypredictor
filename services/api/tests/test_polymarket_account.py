from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.polymarket_account import (
    PolymarketAddressInput,
    _normalize_proxy_wallet,
    fetch_public_account_summary,
    get_linked_address,
    update_linked_address,
)


class _FakeConn:
    def __init__(self, rows: list[object | None]) -> None:
        self.rows = rows
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object):
        self.fetchrow_calls.append((query, args))
        return self.rows.pop(0) if self.rows else None

    async def execute(self, query: str, *args: object):
        self.execute_calls.append((query, args))
        return "DELETE 1"


class _AcquireCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self.conn)


class _FakeDataClient:
    async def positions(self, user: str, *, limit: int = 500, redeemable: bool | None = None):
        assert user.startswith("0x")
        del limit
        if redeemable:
            return [
                type("Position", (), {"current_value": 2.5})(),
            ]
        return [
            type("Position", (), {"current_value": 12.5})(),
            type("Position", (), {"current_value": 7.5})(),
        ]

    async def user_trades(self, user: str, *, limit: int = 100):
        del user, limit
        return [
            {
                "id": "trade-1",
                "conditionId": "cond-1",
                "outcome": "YES",
                "side": "BUY",
                "price": 0.61,
                "size": 50,
                "timestamp": "2026-04-24T12:00:00+00:00",
            }
        ]

    async def user_earnings(self, address: str):
        del address
        return type("Earnings", (), {"total_earnings": 123.45})()


def test_normalize_proxy_wallet_accepts_hex_address() -> None:
    value = _normalize_proxy_wallet("0xABCDEFabcdefABCDEFabcdefABCDEFabcdef1234")
    assert value == "0xabcdefabcdefabcdefabcdefabcdefabcdef1234"


def test_normalize_proxy_wallet_rejects_invalid_address() -> None:
    with pytest.raises(ValueError, match="proxy_wallet"):
        _normalize_proxy_wallet("not-an-address")


@pytest.mark.asyncio
async def test_fetch_public_account_summary_aggregates_public_surfaces() -> None:
    summary = await fetch_public_account_summary(
        proxy_wallet="0xabcdefabcdefabcdefabcdefabcdefabcdef1234",
        verified_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        settings=type("Settings", (), {"polymarket_data_base": "https://example.com"})(),
        data_client=_FakeDataClient(),  # type: ignore[arg-type]
    )

    assert summary.open_positions == 2
    assert summary.redeemable_positions == 1
    assert summary.total_position_value_usdc == pytest.approx(20.0)
    assert summary.total_earnings_usdc == pytest.approx(123.45)
    assert summary.recent_trades[0].trade_id == "trade-1"


@pytest.mark.asyncio
async def test_get_linked_address_returns_summary_for_existing_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.polymarket_account.ensure_user_by_email", fake_ensure_user_by_email
    )
    conn = _FakeConn(
        [
            {
                "proxy_wallet": "0xabcdefabcdefabcdefabcdefabcdefabcdef1234",
                "verified_at": datetime(2026, 4, 24, 12, tzinfo=UTC),
            }
        ]
    )
    pool = _FakePool(conn)

    link = await get_linked_address(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        settings=type("Settings", (), {"polymarket_data_base": "https://example.com"})(),
        data_client=_FakeDataClient(),  # type: ignore[arg-type]
    )

    assert link.proxy_wallet == "0xabcdefabcdefabcdefabcdefabcdefabcdef1234"
    assert link.summary is not None
    assert link.summary.open_positions == 2


@pytest.mark.asyncio
async def test_update_linked_address_persists_verified_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.polymarket_account.ensure_user_by_email", fake_ensure_user_by_email
    )
    conn = _FakeConn(
        [
            {
                "proxy_wallet": "0xabcdefabcdefabcdefabcdefabcdefabcdef1234",
                "verified_at": datetime(2026, 4, 24, 12, tzinfo=UTC),
            }
        ]
    )
    pool = _FakePool(conn)

    link = await update_linked_address(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        settings=type("Settings", (), {"polymarket_data_base": "https://example.com"})(),
        payload=PolymarketAddressInput(
            proxy_wallet="0xABCDEFabcdefABCDEFabcdefABCDEFabcdef1234"
        ),
        data_client=_FakeDataClient(),  # type: ignore[arg-type]
    )

    assert link.proxy_wallet == "0xabcdefabcdefabcdefabcdefabcdefabcdef1234"
    assert conn.execute_calls
    insert_args = conn.fetchrow_calls[-1][1]
    assert insert_args[1] == "0xabcdefabcdefabcdefabcdefabcdefabcdef1234"


@pytest.mark.asyncio
async def test_update_linked_address_clears_saved_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_user_by_email(pool, *, email, display_name=None):
        del pool, email, display_name
        return "user-1"

    monkeypatch.setattr(
        "api.polymarket_account.ensure_user_by_email", fake_ensure_user_by_email
    )
    conn = _FakeConn([])
    pool = _FakePool(conn)

    link = await update_linked_address(
        pool=pool,  # type: ignore[arg-type]
        email="alice@example.com",
        settings=type("Settings", (), {"polymarket_data_base": "https://example.com"})(),
        payload=PolymarketAddressInput(proxy_wallet=None),
    )

    assert link.proxy_wallet is None
    assert conn.execute_calls
