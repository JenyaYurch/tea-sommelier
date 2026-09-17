"""Telegram bot backend selection (in-process vs HTTP)."""

from __future__ import annotations

import pytest

from telegram_integration.adk_client import AdkHttpClient, AdkQuotaError
from telegram_integration.main import START_TEXT, _is_quota_error, ask_agent


def test_start_text_does_not_claim_local_only() -> None:
    lowered = START_TEXT.lower()
    assert "локальн" not in lowered
    assert "лунцзин" in lowered


def test_quota_error_detects_adk_quota() -> None:
    assert _is_quota_error(AdkQuotaError("429"))
    assert not _is_quota_error(RuntimeError("timeout"))


@pytest.mark.asyncio
async def test_ask_agent_uses_http_client_when_present() -> None:
    class FakeClient(AdkHttpClient):
        def __init__(self) -> None:
            super().__init__("https://tea-agent.example")

        async def ask(self, user_id: str, session_id: str, text: str) -> str:
            assert user_id == "tg-9"
            assert session_id == "tg-sess-9"
            assert text == "мягче"
            return "ok-remote"

    reply = await ask_agent({"adk_client": FakeClient()}, 9, "мягче")
    assert reply == "ok-remote"


def test_local_runner_applies_sqlite_default(monkeypatch) -> None:
    from tea_agent.app_utils import services
    from tea_agent.app_utils.session_uri import LOCAL_SQLITE_URI
    from telegram_integration import main as bot

    calls: list[str] = []

    def fake_create(*, base_dir, session_service_uri):
        calls.append(session_service_uri)
        return object()

    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.delenv("CLOUD_SQL_INSTANCE", raising=False)
    monkeypatch.delenv("SESSION_DB_PASSWORD", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)
    monkeypatch.setattr(services, "create_session_service_from_options", fake_create)
    monkeypatch.setattr(services, "get_artifact_service", lambda: object())
    monkeypatch.setattr(services, "get_memory_service", lambda: object())
    monkeypatch.setattr("google.adk.runners.Runner", lambda **kwargs: kwargs)
    services.get_session_service.cache_clear()
    try:
        runner = bot._build_local_runner()
    finally:
        services.get_session_service.cache_clear()
        monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)

    assert calls == [LOCAL_SQLITE_URI]
    assert runner["session_service"] is not None
    assert runner["auto_create_session"] is True
