from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from api.error_reports import ErrorReportInput, record_error_report
from api.settings import Settings


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> None:
        self.calls.append((query, args))


class _Acquire:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_record_error_report_persists_sanitized_payload() -> None:
    pool = _FakePool()
    reported_at = datetime(2026, 4, 25, 12, tzinfo=UTC)
    report = await record_error_report(
        pool=pool,
        settings=Settings(app_env="test"),
        reported_at=reported_at,
        payload=ErrorReportInput(
            source="web",
            severity="fatal",
            message="  render failed  ",
            stack="stack",
            url="https://app.local/status",
            user_agent="playwright",
            context={"digest": "abc"},
        ),
    )

    assert report.source == "web"
    assert report.severity == "fatal"
    assert report.message == "render failed"
    _, args = pool.conn.calls[0]
    assert args[1] == "test"
    assert args[4] == "render failed"
    assert '"digest": "abc"' in args[8]
    assert args[9] == reported_at


@pytest.mark.asyncio
async def test_record_error_report_rejects_blank_message() -> None:
    with pytest.raises(ValueError, match="message is required"):
        await record_error_report(
            pool=_FakePool(),
            settings=Settings(),
            payload=ErrorReportInput(source="worker", message=" "),
        )
