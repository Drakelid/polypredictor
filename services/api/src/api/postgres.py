"""Async Postgres pool for user-scoped journal data."""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg import Pool

from .settings import get_settings


async def get_async_pool() -> Pool:
    settings = get_settings()
    return await asyncpg.create_pool(dsn=settings.postgres_dsn, min_size=1, max_size=5)


def row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row.items()) if row is not None else {}
