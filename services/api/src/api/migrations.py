"""Idempotent boot-time Postgres migration runner.

Postgres' docker-entrypoint only runs ``/docker-entrypoint-initdb.d/*.sql``
on FIRST init of the data volume — if the volume predates a migration
(or the migration was added later) it never runs, leaving the schema
out-of-sync with the code that expects it.

This runner closes that gap by applying every migration once at api
startup. All migrations in ``services/ingest/migrations/postgres`` use
``CREATE TABLE/INDEX IF NOT EXISTS`` (and equivalent ``CREATE EXTENSION``)
so they're safe to re-run on already-initialised databases.

We also track applied migration names in a ``schema_migrations`` table so
new ``ALTER`` migrations (which aren't naturally idempotent) can be added
in the future without re-running them every boot.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


_CLICKHOUSE_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name        String,
    applied_at  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(applied_at)
ORDER BY name
"""


def _discover_migrations(migrations_dir: Path) -> list[Path]:
    """Return migration files in lexicographic order (matches numeric prefix)."""
    if not migrations_dir.is_dir():
        return []
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


async def _already_applied(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT name FROM schema_migrations")
    return {row["name"] for row in rows}


async def apply_postgres_migrations(
    pool: asyncpg.Pool,
    migrations_dir: Path | str,
) -> list[str]:
    """Apply every migration in ``migrations_dir`` that hasn't been applied yet.

    Returns the list of migration names that were applied this call (empty
    list when the schema was already up-to-date). Errors propagate so the
    api fails fast on a bad migration rather than booting against a broken
    schema.
    """
    path = Path(migrations_dir)
    migrations = _discover_migrations(path)
    if not migrations:
        logger.info("migrations.no_files_found", extra={"dir": str(path)})
        return []

    applied: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute(_TRACKING_TABLE_SQL)
        seen = await _already_applied(conn)
        for migration in migrations:
            name = migration.name
            if name in seen:
                continue
            sql = migration.read_text(encoding="utf-8")
            async with conn.transaction():
                # asyncpg's simple-query protocol (no params) supports
                # multi-statement scripts.
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (name) VALUES ($1) "
                    "ON CONFLICT (name) DO NOTHING",
                    name,
                )
            applied.append(name)
            logger.info("migrations.applied", extra={"name": name})

    return applied


# ---------------------------------------------------------------------------
# ClickHouse migrations
# ---------------------------------------------------------------------------


def split_clickhouse_sql(sql: str) -> list[str]:
    """Split a ClickHouse migration file into individual statements.

    Strips line comments (``-- ...``) and splits on top-level semicolons. A
    ``USE <db>;`` statement at the top of a file is dropped because the
    api's ClickHouse client is already connected to the configured
    database; some HTTP-mode clients reject ``USE`` outside an interactive
    session.
    """
    cleaned_lines: list[str] = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        cleaned_lines.append(line)
    joined = "\n".join(cleaned_lines)

    statements: list[str] = []
    for raw in joined.split(";"):
        stmt = raw.strip()
        if not stmt:
            continue
        if stmt.upper().startswith("USE "):
            # The api client already targets the correct database; skip the
            # legacy ``USE polypredictor`` preamble.
            continue
        statements.append(stmt)
    return statements


async def _clickhouse_command(ch: Any, query: str, *args: Any, **kwargs: Any) -> Any:
    """Run a DDL/DML statement via the async ClickHouse client."""
    return await ch.command(query, *args, **kwargs)


async def _clickhouse_already_applied(ch: Any) -> set[str]:
    result = await ch.query(
        "SELECT DISTINCT name FROM schema_migrations FINAL"
    )
    return {str(row[0]) for row in result.result_rows}


async def apply_clickhouse_migrations(
    ch: Any,
    migrations_dir: Path | str,
) -> list[str]:
    """Apply every ClickHouse migration in ``migrations_dir`` not yet applied.

    Mirrors :func:`apply_postgres_migrations`: discover ``*.sql`` files in
    lexicographic order, track applied names in a ``schema_migrations``
    table, split each file into statements, and execute them in order.
    Returns the list of newly-applied migration names.
    """
    path = Path(migrations_dir)
    migrations = _discover_migrations(path)
    if not migrations:
        logger.info("clickhouse_migrations.no_files_found", extra={"dir": str(path)})
        return []

    # The first migration creates the database; the tracking table needs that
    # database to exist, so we run the first migration *before* the tracking
    # table when no migrations have been applied yet. After that, every
    # subsequent invocation can rely on the tracking table living alongside
    # the rest of the schema.
    applied: list[str] = []
    bootstrap_done = False

    try:
        seen = await _clickhouse_already_applied(ch)
        bootstrap_done = True
    except Exception:
        seen = set()

    for migration in migrations:
        name = migration.name
        if bootstrap_done and name in seen:
            continue
        sql = migration.read_text(encoding="utf-8")
        statements = split_clickhouse_sql(sql)
        for stmt in statements:
            await _clickhouse_command(ch, stmt)
        if not bootstrap_done:
            # First migration just landed; create the tracking table.
            await _clickhouse_command(ch, _CLICKHOUSE_TRACKING_TABLE_SQL)
            bootstrap_done = True
            seen = set()
        await ch.insert(
            "schema_migrations",
            [[name]],
            column_names=["name"],
        )
        applied.append(name)
        logger.info("clickhouse_migrations.applied", extra={"name": name})

    return applied
