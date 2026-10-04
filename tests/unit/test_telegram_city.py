"""Telegram /city writes session state and does not call the model."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from tea_agent.app_utils.agent_auth import AUTH_HEADER
from telegram_integration.access import (
    CLOSED_BETA_TEXT,
    AccessConfig,
    AccessGate,
    RateLimiter,
)
from telegram_integration.adk_client import AdkHttpClient
from telegram_integration.city import (
    CITY_ASK_TEXT,
    CITY_NEED_COUNTRY_TEXT,
    city_cmd,
    maybe_capture_city_text,
)
from telegram_integration.keyboard import telegram_session_id, telegram_user_key
from telegram_integration.main import on_text


class _Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


class _FakeBot:
    async def send_chat_action(self, **kwargs) -> None:
        del kwargs


class _FakeMessage:
    chat_id = 1

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        del kwargs
        self.replies.append(text)


def _gate(*, allowed: tuple[int, ...] = (5,), per_minute: int = 4) -> AccessGate:
    config = AccessConfig(
        enforced=True,
        allowed_user_ids=frozenset(allowed),
        admin_user_ids=frozenset(),
        invite_code="",
        rate_limit_per_minute=per_minute,
        rate_limit_per_day=30,
    )
    limiter = RateLimiter(
        per_minute,
        30,
        now=_Clock(datetime(2026, 10, 4, 12, 0, tzinfo=UTC)),
    )
    return AccessGate(config, rate_limiter=limiter)


def _update(user_id: int, text: str = "/city") -> tuple[SimpleNamespace, _FakeMessage]:
    message = _FakeMessage(text)
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=None,
    )
    return update, message


@pytest.mark.asyncio
async def test_city_command_stores_place_on_the_local_session() -> None:
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    service = InMemorySessionService()
    runner = SimpleNamespace(session_service=service, app_name="tea_agent")
    context = SimpleNamespace(
        args=["Warsaw,", "Poland"],
        application=SimpleNamespace(bot_data={"access_gate": _gate(), "runner": runner}),
        bot=None,
    )
    update, message = _update(5, "/city Warsaw, Poland")
    await city_cmd(update, context)

    assert message.replies
    assert "Warsaw" in message.replies[-1]
    assert "Poland" in message.replies[-1]
    session = await service.get_session(
        app_name="tea_agent",
        user_id=telegram_user_key(5),
        session_id=telegram_session_id(5),
    )
    assert session is not None
    assert session.state.get("user:city") == "Warsaw"
    assert session.state.get("user:country") == "Poland"
    assert session.state.get("city") == "Warsaw"


@pytest.mark.asyncio
async def test_city_without_arguments_then_a_known_city_does_not_call_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    calls: list[tuple[int, str]] = []

    async def fake_ask(bot_data: dict, user_id: int, text: str) -> str:
        del bot_data
        calls.append((user_id, text))
        return "ответ"

    monkeypatch.setattr("telegram_integration.main.ask_agent", fake_ask)
    service = InMemorySessionService()
    runner = SimpleNamespace(session_service=service, app_name="tea_agent")
    gate = _gate(per_minute=1)
    bot_data = {"access_gate": gate, "runner": runner}
    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(bot_data=bot_data),
        bot=_FakeBot(),
    )
    update, message = _update(5, "/city")
    await city_cmd(update, context)
    assert message.replies == [CITY_ASK_TEXT]

    follow, follow_message = _update(5, "Варшава")
    await on_text(follow, context)
    assert calls == []
    assert "Варшава" in follow_message.replies[-1]
    assert "Poland" in follow_message.replies[-1]
    session = await service.get_session(
        app_name="tea_agent",
        user_id=telegram_user_key(5),
        session_id=telegram_session_id(5),
    )
    assert session is not None
    assert session.state.get("user:country") == "Poland"

    question, _question_message = _update(5, "лунцзин")
    await on_text(question, context)
    assert calls == [(5, "лунцзин")]


@pytest.mark.asyncio
async def test_pending_city_lets_a_tea_question_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_ask(bot_data: dict, user_id: int, text: str) -> str:
        del bot_data, user_id
        calls.append(text)
        return "ответ"

    monkeypatch.setattr("telegram_integration.main.ask_agent", fake_ask)
    bot_data = {"access_gate": _gate(), "runner": object()}
    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(bot_data=bot_data),
        bot=_FakeBot(),
    )
    update, message = _update(5)
    await city_cmd(update, context)
    question, question_message = _update(5, "как заварить лунцзин?")
    await on_text(question, context)
    assert calls == ["как заварить лунцзин?"]
    assert question_message.replies[-1] == "ответ"
    assert message.replies == [CITY_ASK_TEXT]


@pytest.mark.asyncio
async def test_unknown_city_asks_for_a_country() -> None:
    context = SimpleNamespace(
        args=["Неизвестныйград"],
        application=SimpleNamespace(bot_data={"access_gate": _gate(), "runner": object()}),
        bot=None,
    )
    update, message = _update(5, "/city Неизвестныйград")
    await city_cmd(update, context)
    assert message.replies == [CITY_NEED_COUNTRY_TEXT]


@pytest.mark.asyncio
async def test_blocked_user_cannot_set_a_city() -> None:
    context = SimpleNamespace(
        args=["Warsaw"],
        application=SimpleNamespace(bot_data={"access_gate": _gate(allowed=()), "runner": object()}),
        bot=None,
    )
    update, message = _update(9, "/city Warsaw")
    await city_cmd(update, context)
    assert message.replies == [CLOSED_BETA_TEXT]


@pytest.mark.asyncio
async def test_city_patches_remote_session_without_run() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": "tg-sess-5", "state": {}})
        assert request.method == "PATCH"
        body = json_body(request)
        assert body["state_delta"]["user:city"] == "Minsk"
        assert body["state_delta"]["user:country"] == "Belarus"
        assert request.headers.get(AUTH_HEADER) == "secret"
        return httpx.Response(200, json={"id": "tg-sess-5", "state": body["state_delta"]})

    client = AdkHttpClient(
        "https://tea-agent.example",
        "tea_agent",
        transport=httpx.MockTransport(handler),
        auth_secret="secret",
    )
    context = SimpleNamespace(
        args=["Минск"],
        application=SimpleNamespace(
            bot_data={"access_gate": _gate(), "adk_client": client}
        ),
        bot=None,
    )
    update, message = _update(5, "/city Минск")
    await city_cmd(update, context)
    assert "Minsk" in message.replies[-1]
    assert seen[0][0] == "GET"
    assert seen[1] == (
        "PATCH",
        "/apps/tea_agent/users/tg-5/sessions/tg-sess-5",
    )
    assert not any(method == "POST" and path.endswith("/run") for method, path in seen)


def json_body(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content.decode())


@pytest.mark.asyncio
async def test_capture_helper_saves_a_parsed_place() -> None:
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    service = InMemorySessionService()
    message = _FakeMessage("Gdansk")
    saved = await maybe_capture_city_text(
        message,
        5,
        {
            "access_gate": _gate(),
            "runner": SimpleNamespace(session_service=service, app_name="tea_agent"),
            "city_pending": {5},
        },
    )
    assert saved is True
    assert "Gdańsk" in message.replies[-1]
    session = await service.get_session(
        app_name="tea_agent",
        user_id=telegram_user_key(5),
        session_id=telegram_session_id(5),
    )
    assert session is not None
    assert session.state.get("user:city") == "Gdańsk"
    assert session.state.get("user:country") == "Poland"
