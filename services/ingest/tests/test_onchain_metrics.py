from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from ingest.workers.onchain_metrics import (
    DuneQueryConfig,
    GlassnodeMetricConfig,
    load_dune_config,
    load_glassnode_config,
    parse_dune_payload,
    parse_glassnode_payload,
    run_once,
)
from ingest.writers import ONCHAIN_METRICS_COLS

GLASSNODE_INFLOW_PAYLOAD = [
    {"t": 1745452800, "v": 1234.5},  # 2025-04-24 00:00 UTC
    {"t": 1745539200, "v": 1500.0},  # 2025-04-25 00:00 UTC
    {"t": 1745625600, "v": None},
]

GLASSNODE_OHLC_PAYLOAD = [
    {"t": 1745452800, "o": {"c": 250.0, "v": 100.0}},
]

DUNE_PAYLOAD = {
    "result": {
        "rows": [
            {"day": "2026-04-23", "supply": 32_500_000_000, "asset": "USDC"},
            {"day": "2026-04-24", "supply": 32_700_000_000, "asset": "USDC"},
            {"day": "2026-04-25", "supply": None, "asset": "USDC"},
        ]
    }
}


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


def test_parse_glassnode_payload_emits_observation_per_point() -> None:
    config = GlassnodeMetricConfig(
        metric="transactions/transfers_volume_to_exchanges_sum",
        asset="BTC",
        title="BTC inflow",
        units="BTC",
        chain="bitcoin",
        category="flow",
    )
    obs = parse_glassnode_payload(GLASSNODE_INFLOW_PAYLOAD, config=config)
    assert len(obs) == 3
    assert obs[0].source == "glassnode"
    assert obs[0].asset == "BTC"
    assert obs[0].chain == "bitcoin"
    assert obs[0].category == "flow"
    assert obs[0].value == pytest.approx(1234.5)
    # Missing-value point is preserved as None — run_once filters before insert.
    assert obs[2].value is None
    assert obs[0].period_date == datetime(2025, 4, 24, tzinfo=UTC)


def test_parse_glassnode_payload_handles_ohlc_close() -> None:
    config = GlassnodeMetricConfig(
        metric="market/price_usd_ohlc",
        asset="BTC",
        title="BTC OHLC",
    )
    obs = parse_glassnode_payload(GLASSNODE_OHLC_PAYLOAD, config=config)
    assert len(obs) == 1
    assert obs[0].value == pytest.approx(250.0)


def test_parse_dune_payload_uses_configured_columns() -> None:
    config = DuneQueryConfig(
        query_id=4242,
        metric_id="ethereum_stablecoin_supply",
        title="Eth stablecoin supply",
        chain="ethereum",
        category="supply",
        units="USDC",
        date_field="day",
        value_field="supply",
        asset_field="asset",
    )
    obs = parse_dune_payload(DUNE_PAYLOAD, config=config)
    assert len(obs) == 3
    assert obs[0].source == "dune"
    assert obs[0].asset == "USDC"
    assert obs[0].chain == "ethereum"
    assert obs[0].category == "supply"
    assert obs[0].period_date == datetime(2026, 4, 23, tzinfo=UTC)
    assert obs[1].value == pytest.approx(32_700_000_000)
    assert obs[2].value is None


def test_parse_dune_payload_falls_back_to_static_asset() -> None:
    config = DuneQueryConfig(
        query_id=1,
        metric_id="m",
        title="m",
        asset="USDT",
        date_field="day",
        value_field="supply",
        asset_field=None,
    )
    payload = {"result": {"rows": [{"day": "2026-01-01", "supply": 1.0}]}}
    obs = parse_dune_payload(payload, config=config)
    assert obs[0].asset == "USDT"


def test_parse_dune_payload_returns_empty_when_result_missing() -> None:
    config = DuneQueryConfig(query_id=1, metric_id="m", title="m")
    assert parse_dune_payload({}, config=config) == []
    assert parse_dune_payload({"result": {}}, config=config) == []


def test_load_glassnode_config_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "gn.json"
    path.write_text(
        '{"metrics":[{"metric":"transactions/transfers_volume_to_exchanges_sum",'
        '"asset":"btc","title":"BTC inflow","units":"BTC","chain":"bitcoin",'
        '"category":"flow"}]}',
        encoding="utf-8",
    )
    configs = load_glassnode_config(str(path))
    assert configs == [
        GlassnodeMetricConfig(
            metric="transactions/transfers_volume_to_exchanges_sum",
            asset="BTC",
            title="BTC inflow",
            units="BTC",
            chain="bitcoin",
            category="flow",
        )
    ]


def test_load_glassnode_config_skips_invalid_entries(tmp_path: Path) -> None:
    path = tmp_path / "gn.json"
    path.write_text(
        '{"metrics":[{"metric":"","asset":"BTC"},'
        '{"asset":"BTC","title":"missing metric"}]}',
        encoding="utf-8",
    )
    assert load_glassnode_config(str(path)) == []


def test_load_dune_config_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "dune.json"
    path.write_text(
        '{"metrics":[{"query_id":4242,"metric_id":"x","title":"X","asset":"usdc",'
        '"chain":"ethereum","date_field":"day","value_field":"supply"}]}',
        encoding="utf-8",
    )
    configs = load_dune_config(str(path))
    assert configs == [
        DuneQueryConfig(
            query_id=4242,
            metric_id="x",
            title="X",
            asset="USDC",
            chain="ethereum",
            date_field="day",
            value_field="supply",
        )
    ]


def test_load_config_returns_empty_when_file_absent(tmp_path: Path) -> None:
    assert load_glassnode_config(str(tmp_path / "missing.json")) == []
    assert load_dune_config(str(tmp_path / "missing.json")) == []
    assert load_glassnode_config(None) == []
    assert load_dune_config(None) == []


@pytest.mark.asyncio
async def test_run_once_no_metrics_configured_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.onchain_metrics.get_async_client",
        _fake_get_async_client,
    )

    rows = await run_once(glassnode_configs=[], dune_configs=[])
    assert rows == 0
    # No metrics -> no-op; we should NOT touch ClickHouse at all.
    assert ch.insert_calls == []


@pytest.mark.asyncio
async def test_run_once_writes_glassnode_and_dune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.onchain_metrics.get_async_client",
        _fake_get_async_client,
    )
    # The settings module is imported lazily inside run_once; force the keys
    # to non-empty values so the worker actually fetches.
    from ingest.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "glassnode_api_key", "fake-gn-key", raising=False)
    monkeypatch.setattr(settings, "dune_api_key", "fake-dune-key", raising=False)

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.glassnode.com" and request.url.path.endswith(
            "/transactions/transfers_volume_to_exchanges_sum"
        ):
            assert request.url.params.get("a") == "BTC"
            assert request.url.params.get("api_key") == "fake-gn-key"
            return httpx.Response(200, json=GLASSNODE_INFLOW_PAYLOAD)
        if request.url.host == "api.dune.com" and request.url.path.endswith(
            "/query/4242/results"
        ):
            assert request.headers.get("X-Dune-API-Key") == "fake-dune-key"
            return httpx.Response(200, json=DUNE_PAYLOAD)
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        rows_written = await run_once(
            http_client=http,
            glassnode_configs=[
                GlassnodeMetricConfig(
                    metric="transactions/transfers_volume_to_exchanges_sum",
                    asset="BTC",
                    title="BTC inflow",
                    units="BTC",
                    chain="bitcoin",
                    category="flow",
                ),
            ],
            dune_configs=[
                DuneQueryConfig(
                    query_id=4242,
                    metric_id="ethereum_stablecoin_supply",
                    title="Eth stablecoin supply",
                    asset="USDC",
                    chain="ethereum",
                    category="supply",
                    units="USDC",
                    date_field="day",
                    value_field="supply",
                    asset_field="asset",
                ),
            ],
        )

    # Glassnode: 2 valid + 1 null (dropped); Dune: 2 valid + 1 null (dropped).
    assert rows_written == 4
    assert ch.closed is True
    assert len(ch.insert_calls) == 1
    table, rows, cols = ch.insert_calls[0]
    assert table == "onchain_metrics"
    assert cols == ONCHAIN_METRICS_COLS
    payloads = [dict(zip(cols, row, strict=True)) for row in rows]
    assert {p["source"] for p in payloads} == {"glassnode", "dune"}
    glass_rows = [p for p in payloads if p["source"] == "glassnode"]
    assert {p["asset"] for p in glass_rows} == {"BTC"}
    assert {p["category"] for p in glass_rows} == {"flow"}
    dune_rows = [p for p in payloads if p["source"] == "dune"]
    assert {p["asset"] for p in dune_rows} == {"USDC"}
    assert {p["category"] for p in dune_rows} == {"supply"}
    sample = glass_rows[0]
    assert sample["period_date"].isoformat() == sample["event_time"].date().isoformat()


@pytest.mark.asyncio
async def test_run_once_continues_when_one_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.onchain_metrics.get_async_client",
        _fake_get_async_client,
    )
    from ingest.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "glassnode_api_key", "fake-gn-key", raising=False)
    monkeypatch.setattr(settings, "dune_api_key", "fake-dune-key", raising=False)

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.glassnode.com" and request.url.path.endswith(
            "/good/metric"
        ):
            return httpx.Response(200, json=GLASSNODE_INFLOW_PAYLOAD)
        if request.url.host == "api.dune.com" and request.url.path.endswith(
            "/query/4242/results"
        ):
            return httpx.Response(200, json=DUNE_PAYLOAD)
        return httpx.Response(500)

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        rows = await run_once(
            http_client=http,
            glassnode_configs=[
                GlassnodeMetricConfig(metric="good/metric", asset="BTC", title="g"),
                GlassnodeMetricConfig(metric="bad/metric", asset="BTC", title="b"),
            ],
            dune_configs=[
                DuneQueryConfig(
                    query_id=4242,
                    metric_id="ok",
                    title="ok",
                    date_field="day",
                    value_field="supply",
                ),
                DuneQueryConfig(
                    query_id=9999,
                    metric_id="bad",
                    title="bad",
                    date_field="day",
                    value_field="supply",
                ),
            ],
        )
    # GOOD: 2 rows; OK Dune: 2 rows; bad endpoints are skipped, not fatal.
    assert rows == 4
