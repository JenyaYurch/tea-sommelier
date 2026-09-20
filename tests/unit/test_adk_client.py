"""Unit tests for the Telegram → ADK HTTP client (TEA-13)."""

from __future__ import annotations

import httpx
import pytest

from telegram_integration.adk_client import (
    TEA_AGENT_ERROR,
    TEA_COLD_START,
    TEA_QUOTA,
    TEA_SESSION_FAILED,
    TEA_TIMEOUT_RUN,
    TEA_UNAVAILABLE,
    AdkClientError,
    AdkHttpClient,
    AdkQuotaError,
    AdkTimeoutError,
    AdkUnavailableError,
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

    with pytest.raises(AdkUnavailableError) as exc:
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_UNAVAILABLE
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_ask_quota_sets_error_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(429, text="RESOURCE_EXHAUSTED")

    with pytest.raises(AdkQuotaError) as exc:
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_QUOTA
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_session_get_timeout_is_cold_start() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AdkTimeoutError) as exc:
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_COLD_START


@pytest.mark.asyncio
async def test_session_create_timeout_is_cold_start() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, text="missing")
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AdkTimeoutError) as exc:
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_COLD_START


@pytest.mark.asyncio
async def test_run_timeout_is_timeout_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AdkTimeoutError) as exc:
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_TIMEOUT_RUN


@pytest.mark.asyncio
async def test_connect_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(AdkUnavailableError) as exc:
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_UNAVAILABLE


@pytest.mark.asyncio
async def test_run_500_is_agent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(500, text="boom")

    with pytest.raises(AdkClientError) as exc:
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_AGENT_ERROR
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_session_check_500_is_session_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="session down")

    with pytest.raises(AdkClientError) as exc:
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_SESSION_FAILED
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_session_create_502_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, text="missing")
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(AdkUnavailableError) as exc:
        await _client(handler).ask("tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_UNAVAILABLE
    assert exc.value.status_code == 502
