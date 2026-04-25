from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from ingest.workers.macro_series_ingest import (
    BlsSeriesConfig,
    FredSeriesConfig,
    load_bls_config,
    load_fred_config,
    parse_bls_json,
    parse_fred_csv,
    run_once,
)
from ingest.writers import MACRO_SERIES_COLS

CPI_CSV = """observation_date,CPIAUCSL
2026-01-01,309.7
2026-02-01,310.4
2026-03-01,.
2026-04-01,311.6
"""

UNRATE_CSV = """observation_date,UNRATE
2026-02-01,3.9
2026-03-01,4.0
"""

BLS_CPI_PAYLOAD = {
    "status": "REQUEST_SUCCEEDED",
    "Results": {
        "series": [
            {
                "seriesID": "CUSR0000SA0",
                "data": [
                    {"year": "2026", "period": "M03", "value": "322.2"},
                    {"year": "2026", "period": "M02", "value": "321.9"},
                    {"year": "2026", "period": "A01", "value": "320.0"},
                ],
            }
        ]
    },
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


def test_parse_fred_csv_skips_missing_value_marker() -> None:
    series = FredSeriesConfig(series_id="CPIAUCSL", title="CPI", units="Index")
    observations = parse_fred_csv(CPI_CSV, series=series)
    # All four rows produce observations; the missing-marker row has value=None
    # but is still emitted so callers can decide what to drop.
    assert len(observations) == 4
    by_date = {obs.period_date.date().isoformat(): obs for obs in observations}
    assert by_date["2026-03-01"].value is None
    assert by_date["2026-01-01"].value == pytest.approx(309.7)
    assert by_date["2026-04-01"].value == pytest.approx(311.6)


def test_parse_fred_csv_handles_legacy_date_header() -> None:
    legacy = "DATE,CPIAUCSL\n2026-01-01,309.7\n"
    observations = parse_fred_csv(
        legacy, series=FredSeriesConfig(series_id="CPIAUCSL", title="CPI")
    )
    assert len(observations) == 1
    assert observations[0].value == pytest.approx(309.7)


def test_parse_bls_json_skips_non_monthly_periods() -> None:
    series = BlsSeriesConfig(series_id="CUSR0000SA0", title="BLS CPI", units="Index")
    observations = parse_bls_json(BLS_CPI_PAYLOAD, series=series)
    assert [obs.period_date.date().isoformat() for obs in observations] == [
        "2026-03-01",
        "2026-02-01",
    ]
    assert observations[0].value == pytest.approx(322.2)
    assert observations[1].value == pytest.approx(321.9)


def test_load_fred_config_parses_series_list(tmp_path: Path) -> None:
    path = tmp_path / "fred.json"
    path.write_text(
        '{"series":[{"series_id":"CPIAUCSL","title":"CPI","units":"Index"}]}',
        encoding="utf-8",
    )
    series = load_fred_config(str(path))
    assert series == [
        FredSeriesConfig(series_id="CPIAUCSL", title="CPI", units="Index")
    ]


def test_load_bls_config_parses_series_list(tmp_path: Path) -> None:
    path = tmp_path / "bls.json"
    path.write_text(
        '{"series":[{"series_id":"cusr0000sa0","title":"BLS CPI","units":"Index"}]}',
        encoding="utf-8",
    )
    series = load_bls_config(str(path))
    assert series == [
        BlsSeriesConfig(series_id="CUSR0000SA0", title="BLS CPI", units="Index")
    ]


def test_load_config_returns_empty_when_file_absent(tmp_path: Path) -> None:
    assert load_fred_config(str(tmp_path / "missing-fred.json")) == []
    assert load_bls_config(str(tmp_path / "missing-bls.json")) == []
    assert load_fred_config(None) == []
    assert load_bls_config(None) == []


@pytest.mark.asyncio
async def test_run_once_writes_observations_for_mixed_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.macro_series_ingest.get_async_client",
        _fake_get_async_client,
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fred.stlouisfed.org":
            if request.url.params.get("id") == "CPIAUCSL":
                return httpx.Response(200, text=CPI_CSV)
            if request.url.params.get("id") == "UNRATE":
                return httpx.Response(200, text=UNRATE_CSV)
        if (
            request.url.host == "api.bls.gov"
            and request.url.path == "/publicAPI/v2/timeseries/data/CUSR0000SA0"
        ):
            return httpx.Response(200, json=BLS_CPI_PAYLOAD)
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        rows_written = await run_once(
            http_client=http,
            series_configs=[
                FredSeriesConfig(series_id="CPIAUCSL", title="CPI"),
                FredSeriesConfig(series_id="UNRATE", title="Unemployment"),
            ],
            bls_series_configs=[
                BlsSeriesConfig(series_id="CUSR0000SA0", title="BLS CPI"),
            ],
        )

    # CPI: 4 rows but one is missing-value (skipped at insert); UNRATE: 2 rows;
    # BLS CPI: 3 points but annual average is dropped.
    assert rows_written == 3 + 2 + 2
    assert ch.closed is True
    assert len(ch.insert_calls) == 1
    table, rows, cols = ch.insert_calls[0]
    assert table == "macro_series"
    assert cols == MACRO_SERIES_COLS
    payloads = [dict(zip(cols, row, strict=True)) for row in rows]
    assert {row["series_id"] for row in payloads} == {
        "CPIAUCSL",
        "UNRATE",
        "CUSR0000SA0",
    }
    assert {row["source"] for row in payloads} == {"fred", "bls"}
    cpi_rows = [row for row in payloads if row["series_id"] == "CPIAUCSL"]
    sample = cpi_rows[0]
    assert sample["period_date"].isoformat() == sample["event_time"].date().isoformat()


@pytest.mark.asyncio
async def test_run_once_no_series_configured_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.macro_series_ingest.get_async_client",
        _fake_get_async_client,
    )

    rows = await run_once(series_configs=[], bls_series_configs=[])
    # No series -> no-op; we should NOT touch ClickHouse at all.
    assert rows == 0
    assert ch.insert_calls == []


@pytest.mark.asyncio
async def test_run_once_continues_when_one_series_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ch = _FakeClickHouse()

    async def _fake_get_async_client() -> _FakeClickHouse:
        return ch

    monkeypatch.setattr(
        "ingest.workers.macro_series_ingest.get_async_client",
        _fake_get_async_client,
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fred.stlouisfed.org" and request.url.params.get("id") == "GOOD":
            return httpx.Response(200, text="observation_date,GOOD\n2026-01-01,1.0\n")
        if (
            request.url.host == "api.bls.gov"
            and request.url.path == "/publicAPI/v2/timeseries/data/CUSR0000SA0"
        ):
            return httpx.Response(200, json=BLS_CPI_PAYLOAD)
        return httpx.Response(500)

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        rows = await run_once(
            http_client=http,
            series_configs=[
                FredSeriesConfig(series_id="GOOD", title="Good"),
                FredSeriesConfig(series_id="BAD", title="Bad"),
            ],
            bls_series_configs=[
                BlsSeriesConfig(series_id="CUSR0000SA0", title="BLS CPI"),
                BlsSeriesConfig(series_id="BADBLS", title="Bad BLS"),
            ],
        )
    assert rows == 1 + 2  # GOOD + BLS CPI land; the bad series are skipped
