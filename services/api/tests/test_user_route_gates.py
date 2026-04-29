"""Smoke tests for the routes gated by `current_user_email_or_demo`.

Confirms that:
* with `auth_enforce_user_routes=False`, the routes resolve as the demo
  user (no cookie required) — keeps the dashboard working during the
  rollout window.
* with `auth_enforce_user_routes=True`, the same routes 401 without a
  valid session cookie.

Each route is exercised once for each flag state. The point isn't to
reproduce the full module-level behaviour (those modules already have
unit tests) — it's to lock down the auth wiring at the route layer so a
future Depends-rewrite can't silently un-gate them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from api import auth
from api import paper_trading as paper_trading_q
from api import privacy_prefs as privacy_prefs_q
from api import push_prefs as push_prefs_q
from api import tuning as tuning_q
from api.main import app, get_pg
from api.settings import Settings
from fastapi.testclient import TestClient


@dataclass
class _Conn:
    async def execute(self, *args: object, **kwargs: object) -> str:
        return "OK"

    async def fetchrow(self, *args: object, **kwargs: object) -> Any:
        return None

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _Pool:
    conn: _Conn = field(default_factory=_Conn)

    def acquire(self) -> _Conn:
        return self.conn


def _settings(*, enforce: bool) -> Settings:
    return Settings(
        auth_enforce_user_routes=enforce,
        auth_session_secret_b64="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY",
        journal_demo_user_email="demo@polypredictor.local",
    )


def _patch_settings(monkeypatch: pytest.MonkeyPatch, *, enforce: bool) -> Settings:
    s = _settings(enforce=enforce)
    monkeypatch.setattr(auth, "get_settings", lambda: s)
    return s


def _stub_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the underlying module functions so we don't exercise DB paths."""

    async def _fake_paper_status(pool: Any, *, email: str) -> Any:
        return paper_trading_q.PaperTradingStatus(
            email=email, enabled=False, updated_at=None
        )

    async def _fake_paper_set(pool: Any, *, email: str, enabled: bool) -> Any:
        return paper_trading_q.PaperTradingStatus(
            email=email, enabled=enabled, updated_at=None
        )

    async def _fake_privacy_get(*, pool: Any, email: str) -> Any:
        del pool, email
        return privacy_prefs_q.PrivacyPreferences(
            cross_user_learning_opt_in=False, updated_at=None
        )

    async def _fake_privacy_update(*, pool: Any, email: str, payload: Any) -> Any:
        del pool, email
        return privacy_prefs_q.PrivacyPreferences(
            cross_user_learning_opt_in=payload.cross_user_learning_opt_in,
            updated_at=None,
        )

    async def _fake_push_get(*, pool: Any, email: str) -> Any:
        return push_prefs_q.PushPreferences(
            email_enabled=False,
            email_to=email,
            webhook_enabled=False,
            webhook_url=None,
            min_severity=1.0,
            event_types=[],
            condition_ids=[],
            updated_at=None,
        )

    async def _fake_push_update(*, pool: Any, email: str, payload: Any) -> Any:
        return push_prefs_q.PushPreferences(
            email_enabled=payload.email_enabled,
            email_to=email,
            webhook_enabled=payload.webhook_enabled,
            webhook_url=payload.webhook_url,
            min_severity=payload.min_severity,
            event_types=payload.event_types,
            condition_ids=payload.condition_ids,
            updated_at=None,
        )

    async def _fake_tuning_get(*, pool: Any, email: str) -> Any:
        del pool, email
        return tuning_q.TuningProfile(
            name="Balanced",
            preset="balanced",
            log_odds_shifts=tuning_q._preset_shifts("balanced"),
            is_active=True,
            updated_at=None,
        )

    monkeypatch.setattr(
        paper_trading_q, "get_paper_trading_status", _fake_paper_status
    )
    monkeypatch.setattr(paper_trading_q, "set_paper_trading", _fake_paper_set)
    monkeypatch.setattr(privacy_prefs_q, "get_preferences", _fake_privacy_get)
    monkeypatch.setattr(privacy_prefs_q, "update_preferences", _fake_privacy_update)
    monkeypatch.setattr(push_prefs_q, "get_preferences", _fake_push_get)
    monkeypatch.setattr(push_prefs_q, "update_preferences", _fake_push_update)
    monkeypatch.setattr(tuning_q, "get_active_profile", _fake_tuning_get)


# Each entry: (method, path, body) for the routes that newly gate via
# current_user_email_or_demo.
_GATED_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/v1/paper-trading", None),
    ("PUT", "/v1/paper-trading", {"enabled": True}),
    ("GET", "/v1/privacy-preferences", None),
    ("PUT", "/v1/privacy-preferences", {"cross_user_learning_opt_in": True}),
    ("GET", "/v1/push-preferences", None),
    (
        "PUT",
        "/v1/push-preferences",
        {
            "email_enabled": False,
            "email_to": None,
            "webhook_enabled": False,
            "webhook_url": None,
            "min_severity": 1.0,
            "event_types": [],
            "condition_ids": [],
        },
    ),
    ("GET", "/v1/tuning-profile", None),
]


@pytest.mark.parametrize("method,path,body", _GATED_ROUTES)
def test_gated_route_resolves_as_demo_when_enforce_off(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    _patch_settings(monkeypatch, enforce=False)
    _stub_modules(monkeypatch)
    app.dependency_overrides[get_pg] = lambda: _Pool()
    try:
        client = TestClient(app)
        resp = client.request(method, path, json=body)
        assert resp.status_code == 200, (
            f"{method} {path} returned {resp.status_code}: {resp.text}"
        )
    finally:
        app.dependency_overrides.pop(get_pg, None)


@pytest.mark.parametrize("method,path,body", _GATED_ROUTES)
def test_gated_route_returns_401_when_enforce_on_without_cookie(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    _patch_settings(monkeypatch, enforce=True)
    _stub_modules(monkeypatch)
    app.dependency_overrides[get_pg] = lambda: _Pool()
    try:
        client = TestClient(app)
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401, (
            f"{method} {path} returned {resp.status_code}: {resp.text}"
        )
    finally:
        app.dependency_overrides.pop(get_pg, None)


def test_gated_route_accepts_valid_cookie_when_enforce_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _patch_settings(monkeypatch, enforce=True)
    _stub_modules(monkeypatch)
    app.dependency_overrides[get_pg] = lambda: _Pool()
    token = auth.create_session_token(email="alice@example.com", settings=settings)
    try:
        client = TestClient(app)
        resp = client.get(
            "/v1/tuning-profile",
            cookies={settings.auth_session_cookie_name: token},
        )
        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.pop(get_pg, None)
