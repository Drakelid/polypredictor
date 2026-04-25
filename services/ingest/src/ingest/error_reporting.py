"""Worker-side helper for the first-party error-report sink."""

from __future__ import annotations

import json
import traceback
from collections.abc import Mapping
from typing import Any, Literal
from uuid import uuid4

import asyncpg

from .settings import IngestSettings

ErrorSeverity = Literal["info", "warning", "error", "fatal"]


async def report_worker_error(
    *,
    pool: asyncpg.Pool,
    settings: IngestSettings,
    exc: BaseException,
    severity: ErrorSeverity = "error",
    context: Mapping[str, Any] | None = None,
) -> None:
    """Persist a sanitized worker exception.

    Workers should call this from broad exception handlers after normal
    per-worker retry/DLQ handling. Reporting failure is swallowed so an outage in
    Postgres never masks the original worker error path.
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO error_reports (
                  id, app_env, source, severity, message, stack, context_json
                )
                VALUES ($1, $2, 'worker', $3, $4, $5, $6::jsonb)
                """,
                uuid4(),
                settings.app_env,
                severity,
                str(exc)[:2_000],
                "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )[:20_000],
                json.dumps(dict(context or {}), sort_keys=True, default=str),
            )
    except Exception:
        return
