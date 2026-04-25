"""First-party error reporting sink.

This is intentionally small: web and worker processes can post sanitized error
metadata here without wiring a third-party account during beta. The storage
shape remains compatible with a later Sentry export because it preserves source,
environment, message, stack, URL, user-agent, and arbitrary structured context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from .settings import Settings

ErrorSource = Literal["web", "api", "worker"]
ErrorSeverity = Literal["info", "warning", "error", "fatal"]


@dataclass(frozen=True)
class ErrorReportInput:
    source: ErrorSource
    message: str
    severity: ErrorSeverity = "error"
    stack: str | None = None
    url: str | None = None
    user_agent: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorReport:
    id: UUID
    source: ErrorSource
    severity: ErrorSeverity
    message: str
    reported_at: datetime


def _bounded(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:max_len]


def _validate(payload: ErrorReportInput) -> ErrorReportInput:
    message = _bounded(payload.message, 2_000)
    if message is None:
        raise ValueError("message is required")
    return ErrorReportInput(
        source=payload.source,
        severity=payload.severity,
        message=message,
        stack=_bounded(payload.stack, 20_000),
        url=_bounded(payload.url, 2_000),
        user_agent=_bounded(payload.user_agent, 1_000),
        context=payload.context,
    )


async def record_error_report(
    *,
    pool: Any,
    settings: Settings,
    payload: ErrorReportInput,
    reported_at: datetime | None = None,
) -> ErrorReport:
    clean = _validate(payload)
    row_id = uuid4()
    now = reported_at or datetime.now(tz=UTC)
    context_json = json.dumps(clean.context, sort_keys=True, default=str)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO error_reports (
              id, app_env, source, severity, message, stack,
              url, user_agent, context_json, reported_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
            """,
            row_id,
            settings.app_env,
            clean.source,
            clean.severity,
            clean.message,
            clean.stack,
            clean.url,
            clean.user_agent,
            context_json,
            now,
        )
    return ErrorReport(
        id=row_id,
        source=clean.source,
        severity=clean.severity,
        message=clean.message,
        reported_at=now,
    )
