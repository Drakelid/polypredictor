from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from ingest.retention import (
    ArchiveCandidate,
    ArchivePlan,
    archive_cutoff,
    archive_plan_asof,
    write_manifest,
)
from ingest.settings import IngestSettings


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def query(self, query: str, parameters: dict[str, object]) -> _QueryResult:
        self.calls.append((query, parameters))
        if parameters["table"] == "market_quotes":
            return _QueryResult(
                [
                    (
                        "market_quotes",
                        "202404",
                        datetime(2024, 4, 1, tzinfo=UTC),
                        datetime(2024, 4, 30, tzinfo=UTC),
                        123,
                    )
                ]
            )
        return _QueryResult([])


def test_archive_cutoff_uses_configured_archive_age() -> None:
    assert archive_cutoff(
        now=datetime(2026, 4, 25, 12, tzinfo=UTC),
        settings=IngestSettings(retention_cold_archive_after_years=1),
    ) == datetime(2025, 4, 25, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_archive_plan_queries_each_table_with_cutoff() -> None:
    now = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ch = _FakeClickHouse()
    plan = await archive_plan_asof(ch, settings=IngestSettings(), now=now)
    assert plan.archive_before == datetime(2025, 4, 25, 12, tzinfo=UTC)
    assert plan.retention_years == 2
    assert plan.candidates == [
        ArchiveCandidate(
            table="market_quotes",
            partition_id="202404",
            min_observed_at=datetime(2024, 4, 1, tzinfo=UTC),
            max_observed_at=datetime(2024, 4, 30, tzinfo=UTC),
            rows=123,
        )
    ]
    assert all(call[1]["archive_before"] == plan.archive_before for call in ch.calls)


def test_write_manifest_serializes_datetimes(tmp_path) -> None:
    path = tmp_path / "archive-manifest.json"
    write_manifest(
        ArchivePlan(
            generated_at=datetime(2026, 4, 25, 12, tzinfo=UTC),
            archive_before=datetime(2025, 4, 25, 12, tzinfo=UTC),
            retention_years=2,
            destination_uri="s3://bucket/prefix",
            candidates=[
                ArchiveCandidate(
                    table="market_quotes",
                    partition_id="202404",
                    min_observed_at=datetime(2024, 4, 1, tzinfo=UTC),
                    max_observed_at=datetime(2024, 4, 30, tzinfo=UTC),
                    rows=123,
                )
            ],
        ),
        path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["destination_uri"] == "s3://bucket/prefix"
    assert payload["candidates"][0]["max_observed_at"] == "2024-04-30T00:00:00+00:00"
