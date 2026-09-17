"""Unit tests for the Telegram → ADK HTTP client (TEA-13)."""

from __future__ import annotations

import httpx
import pytest

from telegram_integration.adk_client import (
    AdkClientError,
    AdkHttpClient,
    AdkQuotaError,
    extract_reply_text,
    normalize_adk_base_url,
)


def test_normalize_strips_run_suffix() -> None:
    assert normalize_adk_base_url("https://agent.run.app/run") == "https://agent.run.app"
    assert normalize_adk_base_url("https://agent.run.app/") == "https://agent.run.app"


def test_extract_reply_joins_non_user_text() -> None:
    events = [
        {"author": "user", "content": {"parts": [{"text": "hi"}]}},
        {"partial": True, "content": {"parts": [{"text": "partial"}]}},
        {"content": {"parts": [{"text": "Лунцзин"}, {"text": " мягче"}]}},
        {"content": {"parts": [{"text": "Би Ло Чунь"}]}},
    ]
    assert extract_reply_text(events) == "Лунцзин мягче\n\nБи Ло Чунь"


def test_extract_reply_empty() -> None:
    assert extract_reply_text([]) == ""
    assert extract_reply_text(None) == ""


def _client(handler) -> AdkHttpClient:
    return AdkHttpClient(
        "https://tea-agent.example",
        "tea_agent",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_ask_creates_session_then_runs() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(404, text="missing")
        if request.url.path.endswith("/sessions/tg-sess-1"):
            return httpx.Response(200, json={"id": "tg-sess-1"})
        payload = request.content.decode()
        assert "tg-1" in payload
        assert "мягкий" in payload
        assert "tea_agent" in payload
        return httpx.Response(
            200,
            json=[{"content": {"parts": [{"text": "три сорта"}]}}],
        )

    reply = await _client(handler).ask("tg-1", "tg-sess-1", "мягкий")
    assert reply == "три сорта"
    assert any(method == "GET" for method, _ in calls)
    assert any(method == "POST" and "/run" in url for method, url in calls)


@pytest.mark.asyncio
async def test_ask_quota_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(429, text="RESOURCE_EXHAUSTED")

    with pytest.raises(AdkQuotaError):
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")


@pytest.mark.asyncio
async def test_ask_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(503, text="down")

    with pytest.raises(AdkClientError):
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
