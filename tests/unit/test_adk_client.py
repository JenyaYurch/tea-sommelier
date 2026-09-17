"""Unit tests for the Telegram → ADK HTTP client (TEA-13)."""

from __future__ import annotations

import httpx
import pytest

from telegram_integration.adk_client import (
    AdkClientError,
    AdkHttpClient,
    AdkQuotaError,
    cloud_run_identity_token,
    extract_reply_text,
    normalize_adk_base_url,
)


def test_normalize_strips_run_suffix() -> None:
    assert normalize_adk_base_url("https://agent.run.app/run") == "https://agent.run.app"
    assert normalize_adk_base_url("https://agent.run.app/") == "https://agent.run.app"


def test_cloud_run_identity_token_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_RUN_ID_TOKEN", "env-token")
    assert cloud_run_identity_token("https://tea-agent.example") == "env-token"


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
async def test_ask_reuses_existing_session_and_does_not_recreate() -> None:
    """Webhook restart path: GET 200 must not POST an empty session and wipe state."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "tg-sess-1",
                    "userId": "tg-1",
                    "state": {"user:experience": "новичок"},
                },
            )
        assert request.url.path.endswith("/run")
        return httpx.Response(
            200,
            json=[{"content": {"parts": [{"text": "снова мягкий"}]}}],
        )

    reply = await _client(handler).ask("tg-1", "tg-sess-1", "ещё")
    assert reply == "снова мягкий"
    assert calls[0] == ("GET", "/apps/tea_agent/users/tg-1/sessions/tg-sess-1")
    assert not any(
        method == "POST" and path.endswith("/sessions/tg-sess-1")
        for method, path in calls
    )


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


@pytest.mark.asyncio
async def test_ask_retries_get_401_with_identity_token(monkeypatch) -> None:
    """Private tea-agent: first GET 401, retry with Cloud Run identity token."""
    auths: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auths.append(request.headers.get("Authorization"))
        if request.method == "GET" and request.headers.get("Authorization") is None:
            return httpx.Response(401, text="Unauthorized")
        if request.method == "GET":
            return httpx.Response(200, json={"id": "tg-sess-1"})
        assert request.headers.get("Authorization") == "Bearer id-token"
        return httpx.Response(
            200,
            json=[{"content": {"parts": [{"text": "живой профиль"}]}}],
        )

    monkeypatch.setattr(
        "telegram_integration.adk_client.cloud_run_identity_token",
        lambda _audience: "id-token",
    )
    reply = await _client(handler).ask("tg-1", "tg-sess-1", "ещё")
    assert reply == "живой профиль"
    assert auths[0] is None
    assert auths[1] == "Bearer id-token"
    assert auths[2] == "Bearer id-token"


@pytest.mark.asyncio
async def test_ask_401_without_token_does_not_retry(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="Unauthorized")

    monkeypatch.setattr(
        "telegram_integration.adk_client.cloud_run_identity_token",
        lambda _audience: "",
    )
    with pytest.raises(AdkClientError, match="session check failed: 401"):
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert calls["n"] == 1
