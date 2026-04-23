from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.smart_money_refresh import (
    LEADERBOARD_CATEGORIES,
    LEADERBOARD_ORDERINGS,
    LEADERBOARD_TIME_PERIODS,
    LeaderboardWallet,
    fetch_leaderboard_wallets,
    merge_wallets,
    run_once,
    snapshot_wallet_positions,
)
from ingest.writers import SMART_MONEY_POSITIONS_COLS
from polymarket_client import LeaderboardEntry, Position


def _entry(
    wallet: str,
    *,
    rank: int,
    pnl: float,
    volume: float,
) -> LeaderboardEntry:
    return LeaderboardEntry.model_validate(
        {
            "proxyWallet": wallet,
            "rank": rank,
            "pnl": pnl,
            "volume": volume,
        }
    )


def _position(
    wallet: str,
    *,
    condition_id: str,
    token_id: str,
    outcome: str = "yes",
    size: float = 100.0,
    avg_price: float = 0.45,
    current_value: float = 55.0,
) -> Position:
    return Position.model_validate(
        {
            "proxyWallet": wallet,
            "conditionId": condition_id,
            "asset": token_id,
            "outcome": outcome,
            "size": size,
            "avgPrice": avg_price,
            "currentValue": current_value,
            "cashPnl": 10.0,
        }
    )


class _FakeClickHouse:
    def __init__(self) -> None:
        self.insert_calls: list[tuple[str, list[tuple[object, ...]], list[str]]] = []
        self.closed = False

    async def insert(
        self, table: str, rows: list[tuple[object, ...]], column_names: list[str]
    ) -> None:
        self.insert_calls.append((table, rows, column_names))

    async def close(self) -> None:
        self.closed = True


class _FakeDataClient:
    def __init__(
        self,
        *,
        leaderboard_rows: dict[tuple[str, str, str], list[LeaderboardEntry]] | None = None,
        positions_by_wallet: dict[str, list[Position]] | None = None,
    ) -> None:
        self.leaderboard_rows = leaderboard_rows or {}
        self.positions_by_wallet = positions_by_wallet or {}
        self.leaderboard_calls: list[tuple[str, str, str, int]] = []
        self.position_calls: list[tuple[str, int]] = []

    async def leaderboard(
        self,
        *,
        category: str,
        time_period: str,
        order_by: str,
        limit: int,
    ) -> list[LeaderboardEntry]:
        self.leaderboard_calls.append((category, time_period, order_by, limit))
        return list(self.leaderboard_rows.get((category, time_period, order_by), []))

    async def positions(self, user: str, *, limit: int = 500) -> list[Position]:
        self.position_calls.append((user, limit))
        return list(self.positions_by_wallet.get(user, []))


class _FakePm:
    def __init__(self, data: _FakeDataClient) -> None:
        self.data = data


def test_merge_wallets_deduplicates_by_wallet_and_keeps_best_context() -> None:
    wallets = merge_wallets(
        [
            ("CRYPTO:MONTH:PNL", _entry("0xaaa", rank=12, pnl=50.0, volume=1000.0)),
            ("CRYPTO:ALL:VOL", _entry("0xaaa", rank=5, pnl=-150.0, volume=2500.0)),
            ("FINANCE:MONTH:PNL", _entry("0xbbb", rank=3, pnl=20.0, volume=500.0)),
        ]
    )

    assert [wallet.proxy_wallet for wallet in wallets] == ["0xbbb", "0xaaa"]
    assert wallets[1].rank == 5
    assert wallets[1].pnl == pytest.approx(-150.0)
    assert wallets[1].volume == pytest.approx(2500.0)
    assert wallets[1].sources == ("CRYPTO:ALL:VOL", "CRYPTO:MONTH:PNL")


@pytest.mark.asyncio
async def test_fetch_leaderboard_wallets_queries_all_requested_slices() -> None:
    rows = {
        ("CRYPTO", "MONTH", "PNL"): [_entry("0xaaa", rank=1, pnl=100.0, volume=800.0)],
        ("FINANCE", "ALL", "VOL"): [_entry("0xbbb", rank=2, pnl=80.0, volume=1200.0)],
    }
    data = _FakeDataClient(leaderboard_rows=rows)

    wallets = await fetch_leaderboard_wallets(_FakePm(data), limit=250)

    assert len(data.leaderboard_calls) == (
        len(LEADERBOARD_CATEGORIES)
        * len(LEADERBOARD_TIME_PERIODS)
        * len(LEADERBOARD_ORDERINGS)
    )
    assert set(data.leaderboard_calls) == {
        (category, time_period, order_by, 250)
        for category in LEADERBOARD_CATEGORIES
        for time_period in LEADERBOARD_TIME_PERIODS
        for order_by in LEADERBOARD_ORDERINGS
    }
    assert [wallet.proxy_wallet for wallet in wallets] == ["0xaaa", "0xbbb"]


@pytest.mark.asyncio
async def test_snapshot_wallet_positions_inserts_position_rows_with_leaderboard_context() -> None:
    observed_at = datetime(2026, 4, 23, 12, tzinfo=UTC)
    data = _FakeDataClient(
        positions_by_wallet={
            "0xaaa": [
                _position("0xaaa", condition_id="cond-1", token_id="tok-yes"),
                _position(
                    "0xaaa",
                    condition_id="cond-2",
                    token_id="tok-no",
                    outcome="no",
                    size=50.0,
                    avg_price=0.62,
                    current_value=18.0,
                ),
            ]
        }
    )
    ch = _FakeClickHouse()
    wallet = LeaderboardWallet(
        proxy_wallet="0xaaa",
        rank=4,
        pnl=1250.0,
        volume=25_000.0,
        sources=("CRYPTO:MONTH:PNL",),
    )

    inserted = await snapshot_wallet_positions(
        _FakePm(data),
        ch,
        wallet,
        positions_limit=200,
        observed_at=observed_at,
    )

    assert inserted == 2
    assert data.position_calls == [("0xaaa", 200)]
    assert len(ch.insert_calls) == 1
    table, rows, cols = ch.insert_calls[0]
    assert table == "positions_smart_money"
    assert cols == SMART_MONEY_POSITIONS_COLS
    by = dict(zip(cols, rows[0], strict=True))
    assert by["proxy_wallet"] == "0xaaa"
    assert by["condition_id"] == "cond-1"
    assert by["token_id"] == "tok-yes"
    assert by["leaderboard_rank"] == 4
    assert by["leaderboard_pnl"] == pytest.approx(1250.0)
    assert by["leaderboard_vol"] == pytest.approx(25_000.0)
    assert by["event_time"] == observed_at
    assert by["observed_at"] == observed_at


@pytest.mark.asyncio
async def test_run_once_refreshes_unique_wallets_and_closes_clickhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    ch = _FakeClickHouse()
    data = _FakeDataClient(
        leaderboard_rows={
            ("CRYPTO", "MONTH", "PNL"): [_entry("0xaaa", rank=1, pnl=100.0, volume=800.0)],
            ("CRYPTO", "ALL", "VOL"): [_entry("0xaaa", rank=2, pnl=120.0, volume=1600.0)],
            ("FINANCE", "MONTH", "PNL"): [_entry("0xbbb", rank=3, pnl=90.0, volume=700.0)],
        },
        positions_by_wallet={
            "0xaaa": [
                _position("0xaaa", condition_id="cond-1", token_id="tok-1"),
                _position("0xaaa", condition_id="cond-2", token_id="tok-2"),
            ],
            "0xbbb": [_position("0xbbb", condition_id="cond-3", token_id="tok-3")],
        },
    )

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.smart_money_refresh.get_async_client",
        _fake_get_async_client,
    )

    wallets, inserted_rows = await run_once(
        _FakePm(data),
        leaderboard_limit=100,
        positions_limit=50,
    )

    assert wallets == 2
    assert inserted_rows == 3
    assert ch.closed is True
    assert [call[:3] for call in data.leaderboard_calls].count(("CRYPTO", "MONTH", "PNL")) == 1
    assert sorted(data.position_calls) == [("0xaaa", 50), ("0xbbb", 50)]
