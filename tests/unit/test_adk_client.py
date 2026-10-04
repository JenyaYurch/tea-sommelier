"""Unit tests for the Telegram → ADK HTTP client (TEA-13)."""

from __future__ import annotations

import httpx
import pytest

from tea_agent.app_utils.agent_auth import AUTH_HEADER
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
    payload_looks_like_quota,
    session_has_quota_error,
)


@pytest.mark.asyncio
async def test_ask_sends_configured_secret_and_omits_it_in_dev(monkeypatch) -> None:
    monkeypatch.delenv("TEA_AGENT_AUTH_SECRET", raising=False)
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get(AUTH_HEADER))
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(200, json=[{"content": {"parts": [{"text": "ok"}]}}])

    open_client = AdkHttpClient(
        "https://tea-agent.example",
        "tea_agent",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await open_client.ask("tg-1", "tg-sess-1", "hi") == "ok"
    finally:
        await open_client.aclose()
    assert seen == [None, None]

    monkeypatch.setenv("TEA_AGENT_AUTH_SECRET", "from-env")
    seen.clear()

    def authed(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get(AUTH_HEADER))
        if request.method == "GET":
            return httpx.Response(404, text="missing")
        if request.url.path.endswith("/sessions/tg-sess-1"):
            return httpx.Response(200, json={"id": "tg-sess-1"})
        return httpx.Response(200, json=[{"content": {"parts": [{"text": "ok"}]}}])

    env_client = AdkHttpClient(
        "https://tea-agent.example",
        "tea_agent",
        transport=httpx.MockTransport(authed),
    )
    try:
        assert await env_client.ask("tg-1", "tg-sess-1", "hi") == "ok"
    finally:
        await env_client.aclose()
    assert seen
    assert set(seen) == {"from-env"}


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


async def _ask(handler, user_id: str, session_id: str, text: str) -> str:
    client = _client(handler)
    try:
        return await client.ask(user_id, session_id, text)
    finally:
        await client.aclose()


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

    reply = await _ask(handler, "tg-1", "tg-sess-1", "мягкий")
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

    reply = await _ask(handler, "tg-1", "tg-sess-1", "ещё")
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
        await _ask(handler, "tg-1", "tg-sess-1", "hi")


@pytest.mark.asyncio
async def test_ask_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(503, text="down")

    with pytest.raises(AdkUnavailableError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_UNAVAILABLE
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_ask_quota_sets_error_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(429, text="RESOURCE_EXHAUSTED")

    with pytest.raises(AdkQuotaError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_QUOTA
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_session_get_timeout_is_cold_start() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AdkTimeoutError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_COLD_START


@pytest.mark.asyncio
async def test_session_create_timeout_is_cold_start() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, text="missing")
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AdkTimeoutError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_COLD_START


@pytest.mark.asyncio
async def test_run_timeout_is_timeout_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AdkTimeoutError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_TIMEOUT_RUN


@pytest.mark.asyncio
async def test_connect_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(AdkUnavailableError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_UNAVAILABLE


@pytest.mark.asyncio
async def test_run_500_is_agent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(500, text="boom")

    with pytest.raises(AdkClientError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_AGENT_ERROR
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_run_500_with_resource_exhausted_body_is_quota() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(500, text="_ResourceExhaustedError")

    with pytest.raises(AdkQuotaError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_QUOTA


@pytest.mark.asyncio
async def test_run_500_internal_error_reads_session_quota_event() -> None:
    """Prod: /run returns generic 500; Gemini 429 is only on the session event."""
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        if request.method == "GET":
            gets += 1
            if gets == 1:
                return httpx.Response(200, json={"id": "s", "events": []})
            return httpx.Response(
                200,
                json={
                    "id": "s",
                    "events": [
                        {
                            "author": "tea_sommelier",
                            "errorCode": "_ResourceExhaustedError",
                            "errorMessage": (
                                "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
                                "generate_content_free_tier_requests, limit: 20"
                            ),
                        }
                    ],
                },
            )
        return httpx.Response(500, text="Internal Server Error")

    with pytest.raises(AdkQuotaError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_QUOTA
    assert exc.value.status_code == 429
    assert gets == 2


def test_payload_and_session_quota_helpers() -> None:
    assert payload_looks_like_quota("_ResourceExhaustedError")
    assert payload_looks_like_quota("429 RESOURCE_EXHAUSTED")
    assert not payload_looks_like_quota("Internal Server Error")
    assert session_has_quota_error(
        {
            "events": [
                {"author": "user", "content": {"parts": [{"text": "hi"}]}},
                {"errorCode": "_ResourceExhaustedError"},
            ]
        }
    )
    assert not session_has_quota_error({"id": "s", "events": []})


@pytest.mark.asyncio
async def test_session_check_500_is_session_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="session down")

    with pytest.raises(AdkClientError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_SESSION_FAILED
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_session_create_502_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, text="missing")
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(AdkUnavailableError) as exc:
        await _ask(handler, "tg-1", "tg-sess-1", "hi")
    assert exc.value.error_code == TEA_UNAVAILABLE
    assert exc.value.status_code == 502


def _reply(text: str) -> httpx.Response:
    return httpx.Response(200, json=[{"content": {"parts": [{"text": text}]}}])


@pytest.mark.asyncio
async def test_one_http_client_is_reused_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ask, feedback, and session patch share one client for the process."""
    created: list[httpx.AsyncClient] = []
    real_client = httpx.AsyncClient

    class _CountingClient(real_client):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(
        "telegram_integration.adk_client.httpx.AsyncClient", _CountingClient
    )
    headers_seen: list[str | None] = []
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        headers_seen.append(request.headers.get(AUTH_HEADER))
        if request.method == "GET":
            gets += 1
            return httpx.Response(200, json={"id": "tg-sess-1"})
        if request.url.path.endswith("/feedback"):
            return httpx.Response(200, json={"status": "success"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "tg-sess-1"})
        return _reply("ok")

    client = AdkHttpClient(
        "https://tea-agent.example",
        "tea_agent",
        transport=httpx.MockTransport(handler),
        auth_secret="sek",
    )
    try:
        assert await client.ask("tg-1", "tg-sess-1", "раз") == "ok"
        assert await client.ask("tg-1", "tg-sess-1", "два") == "ok"
        await client.submit_feedback({"log_type": "feedback", "score": 1})
        await client.patch_session_state("tg-1", "tg-sess-1", {"user:city": "Minsk"})
        assert len(created) == 1
        assert client._http is created[0]
        assert gets == 1
        assert headers_seen
        assert set(headers_seen) == {"sek"}
    finally:
        await client.aclose()
    assert created[0].is_closed
    await client.aclose()


@pytest.mark.asyncio
async def test_known_session_skips_lookup_and_retries_once_when_missing() -> None:
    """A seen session is not looked up again. A wiped session is created once."""
    gets = 0
    creates = 0
    runs = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets, creates, runs
        if request.method == "GET":
            gets += 1
            if gets == 1:
                return httpx.Response(200, json={"id": "tg-sess-1", "events": []})
            return httpx.Response(404, text='{"detail":"Session not found."}')
        if request.method == "POST" and request.url.path.endswith(
            "/sessions/tg-sess-1"
        ):
            creates += 1
            return httpx.Response(201, json={"id": "tg-sess-1"})
        runs += 1
        if runs == 3:
            return httpx.Response(404, json={"detail": "Session not found."})
        label = "снова" if runs > 3 else "ок"
        return _reply(label)

    client = _client(handler)
    try:
        assert await client.ask("tg-1", "tg-sess-1", "раз") == "ок"
        assert await client.ask("tg-1", "tg-sess-1", "два") == "ок"
        assert gets == 1
        assert creates == 0
        assert runs == 2
        assert await client.ask("tg-1", "tg-sess-1", "три") == "снова"
        assert gets == 2
        assert creates == 1
        assert runs == 4
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, '{"detail":"Session not found."}'),
        (500, "Session not found."),
    ],
)
async def test_missing_session_is_not_retried_more_than_once(
    status: int, body: str
) -> None:
    runs = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs
        if request.method == "GET":
            return httpx.Response(404, text="Session not found")
        if request.method == "POST" and request.url.path.endswith(
            "/sessions/tg-sess-1"
        ):
            return httpx.Response(201, json={"id": "tg-sess-1"})
        runs += 1
        if runs == 1:
            return _reply("ок")
        return httpx.Response(status, text=body)

    client = _client(handler)
    try:
        assert await client.ask("tg-1", "tg-sess-1", "раз") == "ок"
        with pytest.raises(AdkClientError):
            await client.ask("tg-1", "tg-sess-1", "два")
        # First ask, then the failing ask: the 404/missing response and one retry.
        assert runs == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_unavailable_run_does_not_drop_the_session_cache() -> None:
    gets = 0
    creates = 0
    runs = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets, creates, runs
        if request.method == "GET":
            gets += 1
            return httpx.Response(200, json={"id": "tg-sess-1"})
        if request.method == "POST" and request.url.path.endswith(
            "/sessions/tg-sess-1"
        ):
            creates += 1
            return httpx.Response(201, json={"id": "tg-sess-1"})
        runs += 1
        if runs == 2:
            return httpx.Response(503, text="down")
        return _reply("ок")

    client = _client(handler)
    try:
        assert await client.ask("tg-1", "tg-sess-1", "раз") == "ок"
        with pytest.raises(AdkUnavailableError):
            await client.ask("tg-1", "tg-sess-1", "два")
        assert await client.ask("tg-1", "tg-sess-1", "три") == "ок"
        assert gets == 1
        assert creates == 0
        assert runs == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_patch_retries_once_when_the_cached_session_was_wiped() -> None:
    gets = 0
    creates = 0
    patches = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets, creates, patches
        if request.method == "GET":
            gets += 1
            if gets == 1:
                return httpx.Response(200, json={"id": "tg-sess-1"})
            return httpx.Response(404, text="Session not found")
        if request.method == "POST" and request.url.path.endswith(
            "/sessions/tg-sess-1"
        ):
            creates += 1
            return httpx.Response(201, json={"id": "tg-sess-1"})
        patches += 1
        if patches == 3:
            return httpx.Response(404, json={"detail": "Session not found."})
        return httpx.Response(200, json={"id": "tg-sess-1", "state": {}})

    client = _client(handler)
    try:
        await client.patch_session_state("tg-1", "tg-sess-1", {"user:currency": "EUR"})
        await client.patch_session_state("tg-1", "tg-sess-1", {"user:currency": "USD"})
        assert gets == 1
        assert creates == 0
        assert patches == 2
        await client.patch_session_state("tg-1", "tg-sess-1", {"user:currency": "BYN"})
        assert gets == 2
        assert creates == 1
        assert patches == 4
    finally:
        await client.aclose()
