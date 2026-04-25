from __future__ import annotations

import pytest
from ingest.workers.smart_money_qualify import QualificationThresholds
from ingest.workers.smart_money_refresh import (
    LEADERBOARD_CATEGORIES,
    LEADERBOARD_ORDERINGS,
    LEADERBOARD_TIME_PERIODS,
    LeaderboardWallet,
    fetch_leaderboard_wallets,
    merge_wallets,
    qualify_all,
    run_once,
)
from ingest.workers.whale_flow import WhaleFlowThresholds
from ingest.writers import (
    SIGNAL_EVENTS_COLS,
    SMART_MONEY_PER_MARKET_COLS,
    SMART_MONEY_POSITIONS_COLS,
    SMART_MONEY_QUALIFICATIONS_COLS,
)
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


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(
        self,
        *,
        prior_position_rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.insert_calls: list[tuple[str, list[tuple[object, ...]], list[str]]] = []
        self.query_calls: list[tuple[str, dict[str, object] | None]] = []
        self._prior_position_rows = prior_position_rows or []
        self.closed = False

    async def insert(
        self, table: str, rows: list[tuple[object, ...]], column_names: list[str]
    ) -> None:
        self.insert_calls.append((table, rows, column_names))

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.query_calls.append((query, parameters))
        return _QueryResult(list(self._prior_position_rows))

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


def test_qualify_all_returns_one_decision_per_wallet() -> None:
    wallets = [
        LeaderboardWallet(
            proxy_wallet="0xgood",
            rank=3,
            pnl=4000.0,
            volume=60_000.0,
            sources=("CRYPTO:MONTH:PNL", "CRYPTO:ALL:VOL"),
        ),
        LeaderboardWallet(
            proxy_wallet="0xthin",
            rank=10,
            pnl=100.0,
            volume=100.0,
            sources=("CRYPTO:MONTH:PNL",),
        ),
    ]
    positions_by_wallet = {
        "0xgood": [
            _position("0xgood", condition_id=f"m{i}", token_id=f"tok{i}")
            for i in range(4)
        ],
        "0xthin": [_position("0xthin", condition_id="m1", token_id="tok1")],
    }
    thresholds = QualificationThresholds(min_volume=10_000.0, min_distinct_markets=3)

    decisions = qualify_all(wallets, positions_by_wallet, thresholds)

    by_wallet = {decision.proxy_wallet: decision for decision in decisions}
    assert by_wallet["0xgood"].qualified is True
    assert by_wallet["0xthin"].qualified is False


@pytest.mark.asyncio
async def test_run_once_writes_qualifications_positions_and_per_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Prior snapshot: 0xgood previously held 5k USDC on cond-1 YES. Current
    # pass we add cond-1 YES at 55k (resize above 10k), cond-2 YES at new 12k
    # (open above threshold), and close cond-legacy NO at 40k (close above
    # threshold). cond-3 NO is new but only 2k USDC, below thresholds.
    ch = _FakeClickHouse(
        prior_position_rows=[
            # (wallet, condition_id, token_id, outcome, size, avg_entry, usdc)
            ("0xgood", "cond-1", "tok-1", "YES", 100.0, 0.45, 5_000.0),
            ("0xgood", "cond-legacy", "tok-l", "NO", 80.0, 0.5, 40_000.0),
        ]
    )
    data = _FakeDataClient(
        leaderboard_rows={
            ("CRYPTO", "MONTH", "PNL"): [
                _entry("0xgood", rank=1, pnl=5000.0, volume=60_000.0),
                _entry("0xthin", rank=2, pnl=500.0, volume=500.0),
            ],
            ("CRYPTO", "ALL", "VOL"): [
                _entry("0xgood", rank=5, pnl=4500.0, volume=65_000.0),
            ],
        },
        positions_by_wallet={
            "0xgood": [
                _position(
                    "0xgood",
                    condition_id="cond-1",
                    token_id="tok-1",
                    current_value=55_000.0,
                ),
                _position(
                    "0xgood",
                    condition_id="cond-2",
                    token_id="tok-2",
                    current_value=12_000.0,
                ),
                _position(
                    "0xgood",
                    condition_id="cond-3",
                    token_id="tok-3",
                    outcome="no",
                    current_value=500.0,
                ),
            ],
            "0xthin": [_position("0xthin", condition_id="cond-1", token_id="tok-1")],
        },
    )

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.smart_money_refresh.get_async_client",
        _fake_get_async_client,
    )

    stats = await run_once(
        _FakePm(data),
        leaderboard_limit=100,
        positions_limit=50,
        thresholds=QualificationThresholds(min_volume=10_000.0, min_distinct_markets=3),
        whale_thresholds=WhaleFlowThresholds(
            open_threshold_usdc=10_000.0, resize_pct=0.20
        ),
    )

    assert stats.wallets == 2
    assert stats.qualified_wallets == 1
    assert stats.position_rows == 4  # 3 positions for 0xgood + 1 for 0xthin
    assert stats.per_market_rows == 3  # only 0xgood's 3 markets roll up
    # cond-1 resize, cond-2 open, cond-legacy close — cond-3 is too small.
    assert stats.whale_events == 3
    assert ch.closed is True

    tables_written = [call[0] for call in ch.insert_calls]
    assert "positions_smart_money" in tables_written
    assert "smart_money_qualifications" in tables_written
    assert "smart_money_per_market" in tables_written
    assert "signal_events" in tables_written

    # Qualifications: 2 rows, one per merged wallet.
    qual_call = next(c for c in ch.insert_calls if c[0] == "smart_money_qualifications")
    _, qual_rows, qual_cols = qual_call
    assert qual_cols == SMART_MONEY_QUALIFICATIONS_COLS
    assert len(qual_rows) == 2
    by_wallet = {row[0]: row for row in qual_rows}
    assert by_wallet["0xgood"][1] == 1
    assert by_wallet["0xthin"][1] == 0

    pos_call = next(c for c in ch.insert_calls if c[0] == "positions_smart_money")
    assert pos_call[2] == SMART_MONEY_POSITIONS_COLS

    per_market_call = next(c for c in ch.insert_calls if c[0] == "smart_money_per_market")
    assert per_market_call[2] == SMART_MONEY_PER_MARKET_COLS
    per_market_condition_ids = {row[0] for row in per_market_call[1]}
    assert per_market_condition_ids == {"cond-1", "cond-2", "cond-3"}

    whale_call = next(c for c in ch.insert_calls if c[0] == "signal_events")
    assert whale_call[2] == SIGNAL_EVENTS_COLS
    rows = [dict(zip(SIGNAL_EVENTS_COLS, row, strict=True)) for row in whale_call[1]]
    by_market = {r["condition_id"]: r for r in rows}
    assert by_market["cond-1"]["event_type"] == "whale_resize"
    assert by_market["cond-2"]["event_type"] == "whale_open"
    assert by_market["cond-legacy"]["event_type"] == "whale_close"

    assert sorted(data.position_calls) == [("0xgood", 50), ("0xthin", 50)]
