"""ClickHouse client wrapper."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient

from .settings import get_settings


@lru_cache(maxsize=1)
def _sync_client() -> Any:
    s = get_settings()
    return clickhouse_connect.get_client(
        host=s.clickhouse_host,
        port=s.clickhouse_port,
        username=s.clickhouse_user,
        password=s.clickhouse_password,
        database=s.clickhouse_db,
    )


async def get_async_client() -> AsyncClient:
    s = get_settings()
    return await clickhouse_connect.get_async_client(
        host=s.clickhouse_host,
        port=s.clickhouse_port,
        username=s.clickhouse_user,
        password=s.clickhouse_password,
        database=s.clickhouse_db,
    )
