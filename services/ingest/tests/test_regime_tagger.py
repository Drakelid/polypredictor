from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest.workers.regime_tagger import CLASSIFIER_VERSION, run_once
from ingest.writers import REGIME_LABELS_COLS
from model import BinanceDailyKline


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(
        self,
        *,
        m2sl_rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.insert_calls: list[tuple[str, list[tuple[object, ...]], tuple[str, ...]]] = []
        self.queries: list[tuple[str, dict[str, object] | None]] = []
        self._m2sl_rows = m2sl_rows or []
        self.closed = False

    async def insert(
        self, table: str, rows: list[tuple[object, ...]], column_names: tuple[str, ...]
    ) -> None:
        self.insert_calls.append((table, rows, column_names))

    async def query(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _QueryResult:
        self.queries.append((query, parameters))
        return _QueryResult(list(self._m2sl_rows))

    async def close(self) -> None:
        self.closed = True


class _FakeBinanceKlines:
    def __init__(self, closes: list[float]) -> None:
        self._closes = closes
        self.calls: list[tuple[str, int]] = []

    async def daily_klines(self, *, symbol: str, days: int) -> list[BinanceDailyKline]:
        self.calls.append((symbol, days))
        base = datetime(2026, 4, 1, tzinfo=UTC)
        return [
            BinanceDailyKline(
                symbol=symbol,
                open_time=base,
                close_time=base,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=0.0,
            )
            for close in self._closes
        ]

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_run_once_tags_bull_trend_and_writes_regime_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.regime_tagger.get_async_client",
        _fake_get_async_client,
    )

    # 14 daily closes growing ~0.5%/day → bullish 7d momentum, modest vol.
    # 2.0% daily growth → log-return 0.0198 → annualized vol ~0.378 (above
    # the 0.30 moderate-vol gate) and 7d momentum ~13.9% (above 4% bull
    # threshold), so the classifier picks bull_trend rather than chop.
    binance = _FakeBinanceKlines([100.0 * (1.020 ** i) for i in range(14)])
    stats = await run_once(binance=binance, symbol="BTCUSDT", days=14)

    assert stats.label == "bull_trend"
    assert stats.confidence > 0.6
    assert stats.closes_seen == 14
    assert stats.stablecoin_proxy_used is False  # no M2SL rows configured
    assert ch.closed is True

    # One row written into regime_labels with the rule_v1 classifier tag.
    table, rows, cols = ch.insert_calls[0]
    assert table == "regime_labels"
    assert cols == REGIME_LABELS_COLS
    by = dict(zip(cols, rows[0], strict=True))
    assert by["label"] == "bull_trend"
    assert by["classifier"] == CLASSIFIER_VERSION
    assert by["btc_momentum_7d"] is not None
    assert by["btc_momentum_7d"] > 0


@pytest.mark.asyncio
async def test_run_once_uses_m2sl_proxy_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # M2SL latest = 100, prior = 110 → -9% proxy delta. The classifier
    # treats anything <= -3% as a liquidity-crisis signal.
    rows = [
        (datetime(2026, 4, 1).date(), 100.0),
        (datetime(2026, 3, 25).date(), 102.0),
        (datetime(2026, 3, 18).date(), 105.0),
        (datetime(2026, 3, 11).date(), 110.0),
    ]
    ch = _FakeClickHouse(m2sl_rows=rows)

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.regime_tagger.get_async_client",
        _fake_get_async_client,
    )

    # 2.0% daily growth → log-return 0.0198 → annualized vol ~0.378 (above
    # the 0.30 moderate-vol gate) and 7d momentum ~13.9% (above 4% bull
    # threshold), so the classifier picks bull_trend rather than chop.
    binance = _FakeBinanceKlines([100.0 * (1.020 ** i) for i in range(14)])
    stats = await run_once(binance=binance, symbol="BTCUSDT", days=14)

    assert stats.stablecoin_proxy_used is True
    assert stats.label == "liquidity_crisis"


@pytest.mark.asyncio
async def test_run_once_does_not_block_on_macro_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BoomClickHouse(_FakeClickHouse):
        async def query(
            self, query: str, parameters: dict[str, object] | None = None
        ) -> _QueryResult:
            raise RuntimeError("macro_series table missing")

    ch = _BoomClickHouse()

    async def _fake_get_async_client() -> _BoomClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.regime_tagger.get_async_client",
        _fake_get_async_client,
    )

    # 2.0% daily growth → log-return 0.0198 → annualized vol ~0.378 (above
    # the 0.30 moderate-vol gate) and 7d momentum ~13.9% (above 4% bull
    # threshold), so the classifier picks bull_trend rather than chop.
    binance = _FakeBinanceKlines([100.0 * (1.020 ** i) for i in range(14)])
    stats = await run_once(binance=binance, symbol="BTCUSDT", days=14)
    assert stats.label == "bull_trend"
    assert stats.stablecoin_proxy_used is False
