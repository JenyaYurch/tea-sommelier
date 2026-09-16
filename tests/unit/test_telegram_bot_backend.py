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
            assert user_id == "tg_9"
            assert session_id == "tg_sess_9"
            assert text == "мягче"
            return "ok-remote"

    reply = await ask_agent({"adk_client": FakeClient()}, 9, "мягче")
    assert reply == "ok-remote"
