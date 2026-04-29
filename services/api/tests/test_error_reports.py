from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from api.error_reports import ErrorReportInput, record_error_report
from api.main import (
    ErrorReportRequest,
    _client_ip,
    _error_report_fingerprint,
    _StackDedupe,
    app,
    get_pg,
)
from api.settings import Settings
from fastapi.testclient import TestClient


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


# ---------------------------------------------------------------------------
# Stack-hash dedupe + IP helpers
# ---------------------------------------------------------------------------


def test_stack_dedupe_first_call_returns_false_then_true() -> None:
    dedupe = _StackDedupe()
    assert dedupe.seen("hash-a", window_seconds=60.0) is False
    assert dedupe.seen("hash-a", window_seconds=60.0) is True
    # A different hash within the same window is not a duplicate.
    assert dedupe.seen("hash-b", window_seconds=60.0) is False


def test_stack_dedupe_window_zero_disables_check() -> None:
    dedupe = _StackDedupe()
    assert dedupe.seen("hash-a", window_seconds=0) is False
    assert dedupe.seen("hash-a", window_seconds=0) is False


def test_stack_dedupe_empty_key_never_dedupes() -> None:
    dedupe = _StackDedupe()
    assert dedupe.seen("", window_seconds=60.0) is False
    assert dedupe.seen("", window_seconds=60.0) is False


def test_error_report_fingerprint_uses_stack_when_present() -> None:
    payload_a = ErrorReportRequest(source="web", message="x", stack="stack-1")
    payload_b = ErrorReportRequest(source="api", message="y", stack="stack-1")
    payload_c = ErrorReportRequest(source="web", message="x", stack="stack-2")
    assert _error_report_fingerprint(payload_a) == _error_report_fingerprint(payload_b)
    assert _error_report_fingerprint(payload_a) != _error_report_fingerprint(payload_c)


def test_error_report_fingerprint_falls_back_when_no_stack() -> None:
    payload_a = ErrorReportRequest(
        source="web", message="boom", url="https://app/", stack=None
    )
    payload_a_again = ErrorReportRequest(
        source="web", message="boom", url="https://app/", stack=None
    )
    payload_b = ErrorReportRequest(
        source="web", message="boom", url="https://other/", stack=None
    )
    assert _error_report_fingerprint(payload_a) == _error_report_fingerprint(payload_a_again)
    assert _error_report_fingerprint(payload_a) != _error_report_fingerprint(payload_b)


def test_client_ip_prefers_x_forwarded_for() -> None:
    class _Headers:
        def get(self, name: str) -> str | None:
            return "203.0.113.5, 10.0.0.1" if name == "x-forwarded-for" else None

    class _Client:
        host = "10.0.0.99"

    class _Request:
        headers = _Headers()
        client = _Client()

    assert _client_ip(_Request()) == "203.0.113.5"  # type: ignore[arg-type]


def test_client_ip_falls_back_to_request_client_host() -> None:
    class _Headers:
        def get(self, name: str) -> str | None:
            return None

    class _Client:
        host = "10.0.0.99"

    class _Request:
        headers = _Headers()
        client = _Client()

    assert _client_ip(_Request()) == "10.0.0.99"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End-to-end /v1/error-reports
# ---------------------------------------------------------------------------


def test_create_error_report_dedupes_repeat_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second POST with the same stack returns deduped without writing."""
    pool = _FakePool()
    app.dependency_overrides[get_pg] = lambda: pool
    # Reset shared limiters so prior tests don't bleed in.
    from api.main import _error_report_dedupe, _error_report_limiter

    _error_report_dedupe._last_seen.clear()
    _error_report_limiter._hits.clear()
    try:
        client = TestClient(app)
        body = {
            "source": "web",
            "severity": "error",
            "message": "render failed",
            "stack": "Error: boom\n  at foo.tsx:42",
        }
        first = client.post("/v1/error-reports", json=body)
        second = client.post("/v1/error-reports", json=body)
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["deduped"] is False
        assert second.json()["deduped"] is True
        # Only the first call should have hit the database.
        assert len(pool.conn.calls) == 1
    finally:
        app.dependency_overrides.pop(get_pg, None)


def test_create_error_report_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Content-Length over the cap returns 413 without writing."""
    pool = _FakePool()
    app.dependency_overrides[get_pg] = lambda: pool
    from api.main import _error_report_dedupe, _error_report_limiter

    _error_report_dedupe._last_seen.clear()
    _error_report_limiter._hits.clear()
    try:
        client = TestClient(app)
        # Forge a Content-Length that exceeds the default 64 KB cap.
        resp = client.post(
            "/v1/error-reports",
            json={"source": "web", "message": "x"},
            headers={"content-length": str(1024 * 1024)},
        )
        assert resp.status_code == 413
        assert pool.conn.calls == []
    finally:
        app.dependency_overrides.pop(get_pg, None)
