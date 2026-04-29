from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import pytest
from api import journal_autosync
from polymarket_client import UserAuth, WssEvent


class _FakeManager:
    instances: ClassVar[list[_FakeManager]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        _FakeManager.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_refresh_starts_manager_when_credentials_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_load_user_auth(*, pool, email, settings):
        del pool, email, settings
        return UserAuth(api_key="key", secret="secret", passphrase="pass")

    monkeypatch.setattr(journal_autosync, "load_user_auth", fake_load_user_auth)
    _FakeManager.instances.clear()
    service = journal_autosync.UserJournalAutoSyncService(
        pool=object(),  # type: ignore[arg-type]
        ch=object(),  # type: ignore[arg-type]
        settings=type(
            "Settings",
            (),
            {
                "polymarket_wss_base": "wss://example.com",
                "journal_demo_user_email": "demo@polypredictor.local",
            },
        )(),
        manager_factory=_FakeManager,  # type: ignore[arg-type]
    )

    await service.refresh()

    assert len(_FakeManager.instances) == 1
    assert _FakeManager.instances[0].started is True
    assert _FakeManager.instances[0].kwargs["auth"].api_key == "key"


@pytest.mark.asyncio
async def test_refresh_without_credentials_stops_existing_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    async def fake_load_user_auth(*, pool, email, settings):
        del pool, email, settings
        calls["count"] += 1
        return None

    monkeypatch.setattr(journal_autosync, "load_user_auth", fake_load_user_auth)
    _FakeManager.instances.clear()
    service = journal_autosync.UserJournalAutoSyncService(
        pool=object(),  # type: ignore[arg-type]
        ch=object(),  # type: ignore[arg-type]
        settings=type(
            "Settings",
            (),
            {
                "polymarket_wss_base": "wss://example.com",
                "journal_demo_user_email": "demo@polypredictor.local",
            },
        )(),
        manager_factory=_FakeManager,  # type: ignore[arg-type]
    )
    service._manager = _FakeManager()

    await service.refresh()

    assert calls["count"] == 1
    assert _FakeManager.instances[0].stopped is True
    assert service._manager is None


@pytest.mark.asyncio
async def test_handle_event_logs_fill_with_model_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_load_user_auth(*, pool, email, settings):
        del pool, email, settings
        return UserAuth(api_key="key", secret="secret", passphrase="pass")

    async def fake_create_auto_fill_call(*, pool, ch, email, settings, payload, asked_at):
        del pool, ch, email, settings
        captured["payload"] = payload
        captured["asked_at"] = asked_at
        return None

    monkeypatch.setattr(journal_autosync, "load_user_auth", fake_load_user_auth)
    monkeypatch.setattr(journal_autosync.journal_q, "create_auto_fill_call", fake_create_auto_fill_call)
    _FakeManager.instances.clear()
    service = journal_autosync.UserJournalAutoSyncService(
        pool=object(),  # type: ignore[arg-type]
        ch=object(),  # type: ignore[arg-type]
        settings=type(
            "Settings",
            (),
            {
                "polymarket_wss_base": "wss://example.com",
                "journal_demo_user_email": "demo@polypredictor.local",
            },
        )(),
        manager_factory=_FakeManager,  # type: ignore[arg-type]
    )

    await service.refresh()
    callback = _FakeManager.instances[0].kwargs["callback"]
    await callback(
        WssEvent(
            channel="user",
            event_type="trade",
            token_id="tok-yes",
            condition_id="cond-1",
            raw={
                "id": "fill-1",
                "market": "cond-1",
                "asset_id": "tok-yes",
                "side": "BUY",
                "price": 0.61,
                "size": 100,
                "timestamp": "2026-04-24T12:00:00+00:00",
            },
            observed_at=datetime(2026, 4, 24, 12, 0, 1, tzinfo=UTC).timestamp(),
        )
    )

    payload = captured["payload"]
    assert payload.condition_id == "cond-1"
    assert payload.token_id == "tok-yes"
    assert payload.side == "BUY"
    assert payload.source_event_id == "fill-1"
    assert captured["asked_at"] == datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)


def test_parse_user_fill_event_ignores_non_fills() -> None:
    parsed = journal_autosync.parse_user_fill_event(
        WssEvent(
            channel="user",
            event_type="order",
            token_id="tok-yes",
            condition_id="cond-1",
            raw={"status": "open"},
            observed_at=datetime(2026, 4, 24, 12, 0, tzinfo=UTC).timestamp(),
        )
    )

    assert parsed is None
