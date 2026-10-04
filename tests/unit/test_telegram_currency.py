"""Telegram /currency writes session state and does not call the model."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from telegram import Chat, Message, User

from tea_agent.app_utils.agent_auth import AUTH_HEADER
from telegram_integration.access import (
    CLOSED_BETA_TEXT,
    AccessConfig,
    AccessGate,
    RateLimiter,
)
from telegram_integration.adk_client import AdkHttpClient
from telegram_integration.city import CITY_ASK_TEXT, city_cmd
from telegram_integration.currency import (
    CURRENCY_ASK_TEXT,
    CURRENCY_UNKNOWN_TEXT,
    currency_callback_data,
    currency_cmd,
    on_currency_callback,
    parse_currency_callback,
)
from telegram_integration.feedback import HELP_TEXT
from telegram_integration.keyboard import (
    CALLBACK_PATTERN,
    FEEDBACK_CALLBACK_PATTERN,
    parse_action_callback,
    parse_feedback_callback,
)
from telegram_integration.main import START_TEXT, on_text


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
        self.markups: list[object] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)
        self.markups.append(kwargs.get("reply_markup"))


class _FakeQuery:
    def __init__(self, data: str, message: _FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answered = 0

    async def answer(self, *args, **kwargs) -> None:
        del args, kwargs
        self.answered += 1


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


def _update(user_id: int, text: str = "/currency") -> tuple[SimpleNamespace, _FakeMessage]:
    message = _FakeMessage(text)
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=None,
    )
    return update, message


def _labels(markup) -> list[str]:
    assert markup is not None
    return [button.text for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_currency_command_stores_usd_on_the_local_session() -> None:
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    from telegram_integration.keyboard import telegram_session_id, telegram_user_key

    service = InMemorySessionService()
    runner = SimpleNamespace(session_service=service, app_name="tea_agent")
    context = SimpleNamespace(
        args=["usd"],
        application=SimpleNamespace(bot_data={"access_gate": _gate(), "runner": runner}),
        bot=None,
    )
    update, message = _update(5, "/currency usd")
    await currency_cmd(update, context)

    assert message.replies
    assert "USD" in message.replies[-1]
    session = await service.get_session(
        app_name="tea_agent",
        user_id=telegram_user_key(5),
        session_id=telegram_session_id(5),
    )
    assert session is not None
    assert session.state.get("user:currency") == "USD"
    assert session.state.get("currency") == "USD"
    assert "city" not in session.state


@pytest.mark.asyncio
async def test_currency_without_arguments_offers_three_buttons_and_does_not_call_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, str]] = []

    async def fake_ask(bot_data: dict, user_id: int, text: str) -> str:
        del bot_data
        calls.append((user_id, text))
        return "ответ"

    monkeypatch.setattr("telegram_integration.main.ask_agent", fake_ask)
    gate = _gate(per_minute=1)
    bot_data = {"access_gate": gate, "runner": object()}
    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(bot_data=bot_data),
        bot=_FakeBot(),
    )
    update, message = _update(5, "/currency")
    await currency_cmd(update, context)
    assert message.replies == [CURRENCY_ASK_TEXT]
    assert _labels(message.markups[-1]) == ["EUR", "USD", "BYN"]
    for code in ("EUR", "USD", "BYN"):
        payload = currency_callback_data(code)
        assert len(payload.encode("utf-8")) <= 64
        assert parse_currency_callback(payload) == code
        assert parse_action_callback(payload) is None
        assert parse_feedback_callback(payload) is None
        assert CALLBACK_PATTERN.match(payload) is None
        assert FEEDBACK_CALLBACK_PATTERN.match(payload) is None

    question, _question_message = _update(5, "лунцзин")
    await on_text(question, context)
    assert calls == [(5, "лунцзин")]


@pytest.mark.asyncio
async def test_currency_button_stores_eur_without_spending_a_rate_limit_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    from telegram_integration.keyboard import telegram_session_id, telegram_user_key

    calls: list[str] = []

    async def fake_ask(bot_data: dict, user_id: int, text: str) -> str:
        del bot_data, user_id
        calls.append(text)
        return "ответ"

    monkeypatch.setattr("telegram_integration.main.ask_agent", fake_ask)
    replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        del self, kwargs
        replies.append(text)

    monkeypatch.setattr(Message, "reply_text", reply_text)
    service = InMemorySessionService()
    runner = SimpleNamespace(session_service=service, app_name="tea_agent")
    gate = _gate(per_minute=1)
    bot_data = {"access_gate": gate, "runner": runner}
    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(bot_data=bot_data),
        bot=_FakeBot(),
    )
    message = Message(
        message_id=77,
        date=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="Tea"),
        text="валюта",
    )
    query = _FakeQuery(currency_callback_data("EUR"), message)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=5),
        message=None,
    )
    await on_currency_callback(update, context)
    assert query.answered == 1
    assert replies
    assert "EUR" in replies[-1]
    session = await service.get_session(
        app_name="tea_agent",
        user_id=telegram_user_key(5),
        session_id=telegram_session_id(5),
    )
    assert session is not None
    assert session.state.get("user:currency") == "EUR"

    follow, _follow_message = _update(5, "лунцзин")
    await on_text(follow, context)
    assert calls == ["лунцзин"]


@pytest.mark.asyncio
async def test_unknown_currency_shows_buttons_and_does_not_store() -> None:
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    from telegram_integration.keyboard import telegram_session_id, telegram_user_key

    service = InMemorySessionService()
    runner = SimpleNamespace(session_service=service, app_name="tea_agent")
    context = SimpleNamespace(
        args=["евро"],
        application=SimpleNamespace(bot_data={"access_gate": _gate(), "runner": runner}),
        bot=None,
    )
    update, message = _update(5, "/currency евро")
    await currency_cmd(update, context)
    assert message.replies == [CURRENCY_UNKNOWN_TEXT]
    assert _labels(message.markups[-1]) == ["EUR", "USD", "BYN"]
    session = await service.get_session(
        app_name="tea_agent",
        user_id=telegram_user_key(5),
        session_id=telegram_session_id(5),
    )
    assert session is None


@pytest.mark.asyncio
async def test_blocked_user_cannot_set_a_currency() -> None:
    context = SimpleNamespace(
        args=["USD"],
        application=SimpleNamespace(
            bot_data={"access_gate": _gate(allowed=()), "runner": object()}
        ),
        bot=None,
    )
    update, message = _update(9, "/currency USD")
    await currency_cmd(update, context)
    assert message.replies == [CLOSED_BETA_TEXT]
    assert message.markups == [None]


@pytest.mark.asyncio
async def test_currency_patches_remote_session_without_run() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": "tg-sess-5", "state": {}})
        assert request.method == "PATCH"
        body = json.loads(request.content.decode())
        assert body["state_delta"]["user:currency"] == "BYN"
        assert body["state_delta"]["currency"] == "BYN"
        assert "city" not in body["state_delta"]
        assert request.headers.get(AUTH_HEADER) == "secret"
        return httpx.Response(200, json={"id": "tg-sess-5", "state": body["state_delta"]})

    client = AdkHttpClient(
        "https://tea-agent.example",
        "tea_agent",
        transport=httpx.MockTransport(handler),
        auth_secret="secret",
    )
    context = SimpleNamespace(
        args=["BYN"],
        application=SimpleNamespace(
            bot_data={"access_gate": _gate(), "adk_client": client}
        ),
        bot=None,
    )
    update, message = _update(5, "/currency BYN")
    await currency_cmd(update, context)
    assert "BYN" in message.replies[-1]
    assert seen[0][0] == "GET"
    assert seen[1] == (
        "PATCH",
        "/apps/tea_agent/users/tg-5/sessions/tg-sess-5",
    )
    assert not any(method == "POST" and path.endswith("/run") for method, path in seen)


@pytest.mark.asyncio
async def test_currency_clears_a_pending_city_so_the_next_line_reaches_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_ask(bot_data: dict, user_id: int, text: str) -> str:
        del bot_data, user_id
        calls.append(text)
        return "ответ"

    monkeypatch.setattr("telegram_integration.main.ask_agent", fake_ask)
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    service = InMemorySessionService()
    runner = SimpleNamespace(session_service=service, app_name="tea_agent")
    bot_data = {"access_gate": _gate(), "runner": runner}
    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(bot_data=bot_data),
        bot=_FakeBot(),
    )
    city_update, city_message = _update(5, "/city")
    await city_cmd(city_update, context)
    assert city_message.replies == [CITY_ASK_TEXT]

    context.args = ["EUR"]
    currency_update, currency_message = _update(5, "/currency EUR")
    await currency_cmd(currency_update, context)
    assert "EUR" in currency_message.replies[-1]

    question, question_message = _update(5, "как заварить лунцзин?")
    await on_text(question, context)
    assert calls == ["как заварить лунцзин?"]
    assert question_message.replies[-1] == "ответ"


def test_help_and_start_mention_currency() -> None:
    assert "/currency" in HELP_TEXT
    assert "EUR" in HELP_TEXT
    assert "/currency" in START_TEXT
