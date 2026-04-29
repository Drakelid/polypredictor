"""Tests for the boot-time Postgres migration runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from api.migrations import (
    apply_clickhouse_migrations,
    apply_postgres_migrations,
    split_clickhouse_sql,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeTransaction:
    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _FakeConn:
    applied: list[str] = field(default_factory=list)
    """Migration names already recorded in schema_migrations."""

    executes: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def execute(self, query: str, *args: Any) -> str:
        self.executes.append((query, args))
        if "INSERT INTO schema_migrations" in query and args:
            self.applied.append(str(args[0]))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, str]]:
        if "FROM schema_migrations" in query:
            return [{"name": n} for n in self.applied]
        return []

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _FakePool:
    conn: _FakeConn = field(default_factory=_FakeConn)

    def acquire(self) -> _FakeConn:
        return self.conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_migrations_dir_returns_empty_list(tmp_path: Path) -> None:
    pool = _FakePool()
    missing = tmp_path / "does-not-exist"

    applied = await apply_postgres_migrations(pool, missing)

    assert applied == []
    # Even with a missing dir we don't touch the database.
    assert pool.conn.executes == []


@pytest.mark.asyncio
async def test_empty_migrations_dir_returns_empty_list(tmp_path: Path) -> None:
    pool = _FakePool()

    applied = await apply_postgres_migrations(pool, tmp_path)

    assert applied == []
    assert pool.conn.executes == []


@pytest.mark.asyncio
async def test_applies_migrations_in_lexicographic_order(tmp_path: Path) -> None:
    (tmp_path / "002_b.sql").write_text("CREATE TABLE b (id INT);", encoding="utf-8")
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (id INT);", encoding="utf-8")
    (tmp_path / "003_c.sql").write_text("CREATE TABLE c (id INT);", encoding="utf-8")
    pool = _FakePool()

    applied = await apply_postgres_migrations(pool, tmp_path)

    assert applied == ["001_a.sql", "002_b.sql", "003_c.sql"]


@pytest.mark.asyncio
async def test_skips_already_applied_migrations(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("--", encoding="utf-8")
    (tmp_path / "002_b.sql").write_text("--", encoding="utf-8")
    pool = _FakePool()
    pool.conn.applied = ["001_a.sql"]  # already in the tracking table

    applied = await apply_postgres_migrations(pool, tmp_path)

    assert applied == ["002_b.sql"]


@pytest.mark.asyncio
async def test_returns_empty_when_all_already_applied(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("--", encoding="utf-8")
    (tmp_path / "002_b.sql").write_text("--", encoding="utf-8")
    pool = _FakePool()
    pool.conn.applied = ["001_a.sql", "002_b.sql"]

    applied = await apply_postgres_migrations(pool, tmp_path)

    assert applied == []


@pytest.mark.asyncio
async def test_creates_tracking_table_first(tmp_path: Path) -> None:
    (tmp_path / "001.sql").write_text("--", encoding="utf-8")
    pool = _FakePool()

    await apply_postgres_migrations(pool, tmp_path)

    queries = [q for q, _ in pool.conn.executes]
    assert any("CREATE TABLE IF NOT EXISTS schema_migrations" in q for q in queries)
    # Tracking table is created before any migration body.
    tracking_idx = next(
        i for i, q in enumerate(queries)
        if "CREATE TABLE IF NOT EXISTS schema_migrations" in q
    )
    assert tracking_idx == 0


@pytest.mark.asyncio
async def test_executes_migration_body_then_records_in_tracking_table(
    tmp_path: Path,
) -> None:
    body = "CREATE TABLE thing (id INT);"
    (tmp_path / "010_thing.sql").write_text(body, encoding="utf-8")
    pool = _FakePool()

    await apply_postgres_migrations(pool, tmp_path)

    queries = [q for q, _ in pool.conn.executes]
    body_idx = queries.index(body)
    insert_idx = next(
        i for i, q in enumerate(queries)
        if "INSERT INTO schema_migrations" in q
    )
    assert body_idx < insert_idx


@pytest.mark.asyncio
async def test_migration_failure_stops_processing(tmp_path: Path) -> None:
    (tmp_path / "001.sql").write_text("OK SQL", encoding="utf-8")
    (tmp_path / "002_bad.sql").write_text("BAD SQL", encoding="utf-8")
    (tmp_path / "003.sql").write_text("LATER", encoding="utf-8")

    @dataclass
    class _ExplodingConn(_FakeConn):
        async def execute(self, query: str, *args: Any) -> str:
            self.executes.append((query, args))
            if query == "BAD SQL":
                raise RuntimeError("syntax error")
            if "INSERT INTO schema_migrations" in query and args:
                self.applied.append(str(args[0]))
            return "OK"

    pool = _FakePool(conn=_ExplodingConn())

    with pytest.raises(RuntimeError, match="syntax error"):
        await apply_postgres_migrations(pool, tmp_path)

    # The first migration was applied; the third never ran.
    assert pool.conn.applied == ["001.sql"]
    assert "LATER" not in [q for q, _ in pool.conn.executes]


@pytest.mark.asyncio
async def test_ignores_non_sql_files(tmp_path: Path) -> None:
    (tmp_path / "001.sql").write_text("--", encoding="utf-8")
    (tmp_path / "README.md").write_text("not a migration", encoding="utf-8")
    (tmp_path / "002.txt").write_text("ignore me", encoding="utf-8")
    pool = _FakePool()

    applied = await apply_postgres_migrations(pool, tmp_path)

    assert applied == ["001.sql"]


@pytest.mark.asyncio
async def test_accepts_string_path(tmp_path: Path) -> None:
    (tmp_path / "001.sql").write_text("--", encoding="utf-8")
    pool = _FakePool()

    applied = await apply_postgres_migrations(pool, str(tmp_path))

    assert applied == ["001.sql"]


# ---------------------------------------------------------------------------
# split_clickhouse_sql
# ---------------------------------------------------------------------------


def test_split_drops_use_statements() -> None:
    sql = "USE polypredictor;\nCREATE TABLE foo (id UInt32) ENGINE=Memory;"

    statements = split_clickhouse_sql(sql)

    assert statements == ["CREATE TABLE foo (id UInt32) ENGINE=Memory"]


def test_split_strips_line_comments() -> None:
    sql = "-- header comment\nCREATE TABLE foo (id UInt32) ENGINE=Memory; -- trailing"

    statements = split_clickhouse_sql(sql)

    assert statements == ["CREATE TABLE foo (id UInt32) ENGINE=Memory"]


def test_split_handles_multiple_statements() -> None:
    sql = (
        "CREATE TABLE a (id UInt32) ENGINE=Memory;\n"
        "CREATE TABLE b (id UInt32) ENGINE=Memory;\n"
    )

    statements = split_clickhouse_sql(sql)

    assert len(statements) == 2
    assert "CREATE TABLE a" in statements[0]
    assert "CREATE TABLE b" in statements[1]


def test_split_drops_empty_fragments() -> None:
    sql = ";;CREATE TABLE foo (id UInt32) ENGINE=Memory;;\n;;"

    statements = split_clickhouse_sql(sql)

    assert statements == ["CREATE TABLE foo (id UInt32) ENGINE=Memory"]


def test_split_use_is_case_insensitive() -> None:
    sql = "use polypredictor;\nCREATE TABLE foo (id UInt32) ENGINE=Memory;"

    statements = split_clickhouse_sql(sql)

    assert statements == ["CREATE TABLE foo (id UInt32) ENGINE=Memory"]


# ---------------------------------------------------------------------------
# apply_clickhouse_migrations
# ---------------------------------------------------------------------------


class _ChQueryResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


@dataclass
class _FakeClickHouse:
    """Sequential-call fake matching the contract apply_clickhouse_migrations needs."""

    applied: list[str] = field(default_factory=list)
    """Names already in schema_migrations on the server."""
    commands: list[str] = field(default_factory=list)
    inserts: list[tuple[str, list[list[Any]], list[str]]] = field(default_factory=list)
    raise_on_initial_select: bool = False

    async def query(self, query: str, *args: Any, **kwargs: Any) -> _ChQueryResult:
        if "FROM schema_migrations" in query:
            if self.raise_on_initial_select:
                raise RuntimeError("table does not exist yet")
            return _ChQueryResult([(name,) for name in self.applied])
        return _ChQueryResult([])

    async def command(self, query: str, *args: Any, **kwargs: Any) -> None:
        self.commands.append(query)
        if "CREATE TABLE IF NOT EXISTS schema_migrations" in query:
            # Simulate the table being created so future selects work.
            self.raise_on_initial_select = False

    async def insert(
        self,
        table: str,
        data: list[list[Any]],
        *,
        column_names: list[str],
    ) -> None:
        self.inserts.append((table, data, column_names))
        if table == "schema_migrations":
            for row in data:
                self.applied.append(str(row[0]))


@pytest.mark.asyncio
async def test_clickhouse_no_migrations_dir_returns_empty(tmp_path: Path) -> None:
    ch = _FakeClickHouse()
    missing = tmp_path / "missing"

    applied = await apply_clickhouse_migrations(ch, missing)

    assert applied == []
    assert ch.commands == []


@pytest.mark.asyncio
async def test_clickhouse_applies_migrations_and_creates_tracking_table(
    tmp_path: Path,
) -> None:
    (tmp_path / "001.sql").write_text(
        "USE polypredictor;\nCREATE TABLE a (id UInt32) ENGINE=Memory;",
        encoding="utf-8",
    )
    (tmp_path / "002.sql").write_text(
        "CREATE TABLE b (id UInt32) ENGINE=Memory;",
        encoding="utf-8",
    )
    ch = _FakeClickHouse(raise_on_initial_select=True)

    applied = await apply_clickhouse_migrations(ch, tmp_path)

    assert applied == ["001.sql", "002.sql"]
    # The tracking table is created right after the first migration's body.
    tracking_idx = next(
        i for i, c in enumerate(ch.commands)
        if "CREATE TABLE IF NOT EXISTS schema_migrations" in c
    )
    body_a_idx = next(i for i, c in enumerate(ch.commands) if "CREATE TABLE a" in c)
    body_b_idx = next(i for i, c in enumerate(ch.commands) if "CREATE TABLE b" in c)
    assert body_a_idx < tracking_idx < body_b_idx
    # Both names recorded via insert().
    inserted_names = [row[0] for _, rows, _ in ch.inserts for row in rows]
    assert inserted_names == ["001.sql", "002.sql"]


@pytest.mark.asyncio
async def test_clickhouse_skips_already_applied(tmp_path: Path) -> None:
    (tmp_path / "001.sql").write_text(
        "CREATE TABLE a (id UInt32) ENGINE=Memory;", encoding="utf-8"
    )
    (tmp_path / "002.sql").write_text(
        "CREATE TABLE b (id UInt32) ENGINE=Memory;", encoding="utf-8"
    )
    # Tracking table already exists with 001 applied.
    ch = _FakeClickHouse(applied=["001.sql"])

    applied = await apply_clickhouse_migrations(ch, tmp_path)

    assert applied == ["002.sql"]
    # Only the body of 002 was executed (plus its tracking insert).
    table_a_runs = [c for c in ch.commands if "CREATE TABLE a" in c]
    table_b_runs = [c for c in ch.commands if "CREATE TABLE b" in c]
    assert table_a_runs == []
    assert table_b_runs == ["CREATE TABLE b (id UInt32) ENGINE=Memory"]


@pytest.mark.asyncio
async def test_clickhouse_returns_empty_when_all_already_applied(tmp_path: Path) -> None:
    (tmp_path / "001.sql").write_text(
        "CREATE TABLE a (id UInt32) ENGINE=Memory;", encoding="utf-8"
    )
    ch = _FakeClickHouse(applied=["001.sql"])

    applied = await apply_clickhouse_migrations(ch, tmp_path)

    assert applied == []
    assert ch.inserts == []


@pytest.mark.asyncio
async def test_clickhouse_empty_dir_returns_empty(tmp_path: Path) -> None:
    ch = _FakeClickHouse()

    applied = await apply_clickhouse_migrations(ch, tmp_path)

    assert applied == []
    assert ch.commands == []
