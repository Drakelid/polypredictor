from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.deribit_iv_surface import run_once
from ingest.writers import DERIBIT_IV_SURFACE_COLS
from model import DeribitTermStructurePoint


class _FakeClickHouse:
    def __init__(self) -> None:
        self.insert_calls: list[tuple[str, list[tuple[object, ...]], tuple[str, ...]]] = []
        self.closed = False

    async def insert(
        self, table: str, rows: list[tuple[object, ...]], column_names: tuple[str, ...]
    ) -> None:
        self.insert_calls.append((table, rows, column_names))

    async def close(self) -> None:
        self.closed = True


class _FakeDeribit:
    async def fetch_term_structure(
        self,
        *,
        currency: str,
    ) -> list[DeribitTermStructurePoint]:
        assert currency in {"BTC", "ETH"}
        observed = datetime(2026, 6, 30, tzinfo=UTC)
        return [
            DeribitTermStructurePoint(
                currency=currency,
                expiry_date=observed,
                expiry_days=30.0,
                underlying_price=100_000.0 if currency == "BTC" else 5_000.0,
                atm_iv=0.55,
                call_otm_iv=0.60,
                put_otm_iv=0.52,
                strike_skew=0.08,
                atm_instrument=f"{currency}-ATM",
                call_otm_instrument=f"{currency}-CALL",
                put_otm_instrument=f"{currency}-PUT",
            )
        ]


@pytest.mark.asyncio
async def test_run_once_writes_deribit_iv_surface_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.deribit_iv_surface.get_async_client",
        _fake_get_async_client,
    )

    count = await run_once(deribit=_FakeDeribit())

    assert count == 2
    assert ch.closed is True
    assert len(ch.insert_calls) == 1
    table, rows, cols = ch.insert_calls[0]
    assert table == "deribit_iv_surface"
    assert cols == DERIBIT_IV_SURFACE_COLS
    first = dict(zip(cols, rows[0], strict=True))
    assert first["currency"] in {"BTC", "ETH"}
    assert first["atm_iv"] == pytest.approx(0.55)
    assert first["strike_skew"] == pytest.approx(0.08)
