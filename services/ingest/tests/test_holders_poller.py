from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.holders_poller import (
    MarketTokens,
    _concentration_row_for_market,
    _holder_rows_for_market,
    _split_holders_by_outcome,
    run_once,
)
from ingest.writers import HOLDERS_COLS, MARKET_CONCENTRATION_COLS
from polymarket_client import HolderEntry


def _holder(wallet: str, *, outcome: str, size: float) -> HolderEntry:
    return HolderEntry.model_validate(
        {"proxyWallet": wallet, "size": size, "outcome": outcome}
    )


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, markets_rows: list[tuple[object, ...]]) -> None:
        self._markets_rows = markets_rows
        self.queries: list[tuple[str, dict[str, object] | None]] = []
        self.insert_calls: list[tuple[str, list[tuple[object, ...]], list[str]]] = []
        self.closed = False

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.queries.append((query, parameters))
        return _QueryResult(self._markets_rows)

    async def insert(
        self, table: str, rows: list[tuple[object, ...]], column_names: list[str]
    ) -> None:
        self.insert_calls.append((table, rows, column_names))

    async def close(self) -> None:
        self.closed = True


class _FakeDataClient:
    def __init__(self, holders_by_market: dict[str, list[HolderEntry]]) -> None:
        self._holders = holders_by_market
        self.calls: list[list[str]] = []

    async def holders(self, condition_ids: list[str] | str) -> dict[str, list[HolderEntry]]:
        if isinstance(condition_ids, str):
            condition_ids = [condition_ids]
        self.calls.append(list(condition_ids))
        return {cid: list(self._holders.get(cid, [])) for cid in condition_ids}


class _FakePm:
    def __init__(self, data: _FakeDataClient) -> None:
        self.data = data


def test_split_holders_by_outcome_groups_and_sorts_by_size() -> None:
    holders = [
        _holder("0xa", outcome="yes", size=100.0),
        _holder("0xb", outcome="no", size=60.0),
        _holder("0xc", outcome="yes", size=250.0),
        _holder("0xd", outcome="no", size=10.0),
        _holder("0xe", outcome="yes", size=0.0),  # zero skipped
    ]
    grouped = _split_holders_by_outcome(holders)

    assert [h.proxy_wallet for h in grouped["YES"]] == ["0xc", "0xa"]
    assert [h.proxy_wallet for h in grouped["NO"]] == ["0xb", "0xd"]
    assert "UNKNOWN" not in grouped


def test_holder_rows_include_rank_and_pct_of_outcome() -> None:
    tokens = MarketTokens(condition_id="cond-1", yes_token="tok-y", no_token="tok-n")
    grouped = {
        "YES": [
            _holder("0xa", outcome="yes", size=600.0),
            _holder("0xb", outcome="yes", size=400.0),
        ],
        "NO": [_holder("0xc", outcome="no", size=500.0)],
    }
    observed_at = datetime(2026, 4, 23, 12, tzinfo=UTC)

    rows = _holder_rows_for_market(tokens, grouped, observed_at)

    by = [dict(zip(HOLDERS_COLS, row, strict=True)) for row in rows]
    yes_rows = [r for r in by if r["token_id"] == "tok-y"]
    no_rows = [r for r in by if r["token_id"] == "tok-n"]

    assert [r["rank"] for r in yes_rows] == [1, 2]
    assert yes_rows[0]["pct_of_outcome"] == pytest.approx(0.6)
    assert yes_rows[1]["pct_of_outcome"] == pytest.approx(0.4)
    assert no_rows[0]["pct_of_outcome"] == pytest.approx(1.0)


def test_concentration_row_captures_whale_flag() -> None:
    tokens = MarketTokens(condition_id="cond-1", yes_token="tok-y", no_token="tok-n")
    grouped = {
        "YES": [_holder("0xa", outcome="yes", size=500.0), _holder("0xb", outcome="yes", size=100.0)],
        "NO": [
            _holder("0xc", outcome="no", size=100.0),
            _holder("0xd", outcome="no", size=100.0),
            _holder("0xe", outcome="no", size=100.0),
        ],
    }
    observed_at = datetime(2026, 4, 23, 12, tzinfo=UTC)

    row = _concentration_row_for_market(
        tokens, grouped, whale_threshold=0.40, observed_at=observed_at
    )
    data = dict(zip(MARKET_CONCENTRATION_COLS, row, strict=True))

    assert data["condition_id"] == "cond-1"
    assert data["yes_whale_flag"] == 1  # 500 / 600 ≈ 0.83
    assert data["no_whale_flag"] == 0   # uniform
    assert data["any_whale_flag"] == 1
    assert data["yes_top1_pct"] == pytest.approx(500 / 600)


@pytest.mark.asyncio
async def test_run_once_writes_holders_and_concentration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse(
        markets_rows=[
            ("cond-a", ["tok-ay", "tok-an"], 1, 0, 0),
            ("cond-b", ["tok-by", "tok-bn"], 1, 0, 0),
            ("cond-closed", ["tok-cy", "tok-cn"], 1, 1, 0),  # closed, filtered out
        ]
    )
    data = _FakeDataClient(
        {
            "cond-a": [
                _holder("0xwhale", outcome="yes", size=900.0),
                _holder("0xdust1", outcome="yes", size=50.0),
                _holder("0xn1", outcome="no", size=100.0),
                _holder("0xn2", outcome="no", size=100.0),
            ],
            "cond-b": [
                _holder("0xb1", outcome="yes", size=200.0),
                _holder("0xb2", outcome="yes", size=200.0),
                _holder("0xb3", outcome="yes", size=200.0),
            ],
        }
    )

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.holders_poller.get_async_client",
        _fake_get_async_client,
    )

    stats = await run_once(
        _FakePm(data),
        batch_size=5,
        max_markets=100,
        whale_threshold=0.40,
    )

    # cond-closed is filtered out; cond-a + cond-b remain.
    assert stats.markets == 2
    assert data.calls == [["cond-a", "cond-b"]]

    tables_written = [call[0] for call in ch.insert_calls]
    assert "holders_snapshots" in tables_written
    assert "market_concentration" in tables_written

    conc_call = next(c for c in ch.insert_calls if c[0] == "market_concentration")
    rows = [dict(zip(MARKET_CONCENTRATION_COLS, row, strict=True)) for row in conc_call[1]]
    by_cid = {r["condition_id"]: r for r in rows}
    # cond-a: YES is dominated by one whale (90%).
    assert by_cid["cond-a"]["yes_whale_flag"] == 1
    assert by_cid["cond-a"]["any_whale_flag"] == 1
    # cond-b: 200/200 uniform, no whale, Gini should be ~0.
    assert by_cid["cond-b"]["yes_whale_flag"] == 0
    assert by_cid["cond-b"]["any_whale_flag"] == 0
    assert ch.closed is True
