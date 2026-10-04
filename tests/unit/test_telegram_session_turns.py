"""Per-session turn lock (TEA-47).

One Telegram user runs one agent turn at a time. A second tap waits, still
shows typing, and the reply comes back after the first. Another user is not
blocked. The rate-limit token is still spent when the update arrives.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from telegram import Chat, Message, User

from telegram_integration.access import (
    RATE_LIMIT_MINUTE_TEXT,
    AccessConfig,
    AccessGate,
)
from telegram_integration.adk_client import AdkHttpClient
from telegram_integration.city import CITY_SAVE_FAILED_TEXT, city_cmd
from telegram_integration.feedback import record_feedback
from telegram_integration.keyboard import action_callback_data
from telegram_integration.main import (
    _close_adk_client,
    _run_agent_and_reply,
    on_callback,
)
from telegram_integration.turn_gate import SessionTurnGate


class _Bot:
    def __init__(self) -> None:
        self.typed_chats: list[int] = []
        self._typed = asyncio.Event()

    async def send_chat_action(self, **kwargs) -> None:
        chat_id = int(kwargs["chat_id"])
        self.typed_chats.append(chat_id)
        self._typed.set()

    async def wait_typed(self, chat_id: int) -> None:
        while chat_id not in self.typed_chats:
            await self._typed.wait()
            self._typed.clear()


class _Reply:
    def __init__(self, label: str, chat_id: int = 1) -> None:
        self.label = label
        self.chat_id = chat_id
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


class _ScriptedClient(AdkHttpClient):
    def __init__(self) -> None:
        super().__init__("https://tea-agent.example")
        self.order: list[tuple[str, str]] = []
        self.release_a = asyncio.Event()
        self.started_a = asyncio.Event()
        self.patches: list[dict] = []
        self.feedback: dict | None = None

    async def ask(self, user_id: str, session_id: str, text: str) -> str:
        self.order.append(("start", text))
        if text == "a":
            self.started_a.set()
            await self.release_a.wait()
        self.order.append(("end", text))
        return f"reply-{text}"

    async def patch_session_state(
        self, user_id: str, session_id: str, state_delta: dict
    ) -> None:
        self.patches.append(state_delta)

    async def submit_feedback(self, payload: dict) -> None:
        self.feedback = payload


def _gate(*, per_minute: int = 4) -> AccessGate:
    return AccessGate(
        AccessConfig(
            enforced=True,
            allowed_user_ids=frozenset({5}),
            admin_user_ids=frozenset(),
            invite_code="",
            rate_limit_per_minute=per_minute,
            rate_limit_per_day=30,
        )
    )


def _message(message_id: int, chat_id: int) -> Message:
    return Message(
        message_id=message_id,
        date=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=5, is_bot=False, first_name="Tea"),
    )


class _Query:
    def __init__(self, data: str, message: Message) -> None:
        self.data = data
        self.message = message
        self.answered = 0

    async def answer(self, *args, **kwargs) -> None:
        self.answered += 1


async def _turn(
    bot_data: dict, bot: _Bot, target: _Reply, text: str, user: int
) -> None:
    await _run_agent_and_reply(
        target=target,  # type: ignore[arg-type]
        telegram_user_id=user,
        text=text,
        bot=bot,
        bot_data=bot_data,
    )


@pytest.mark.asyncio
async def test_double_tap_replies_in_order_while_another_user_overlaps() -> None:
    client = _ScriptedClient()
    bot_data = {"adk_client": client}
    bot = _Bot()
    first = _Reply("a")
    second = _Reply("b")
    other = _Reply("c")

    task_a = asyncio.create_task(_turn(bot_data, bot, first, "a", 1))
    await client.started_a.wait()
    task_b = asyncio.create_task(_turn(bot_data, bot, second, "b", 1))
    task_c = asyncio.create_task(_turn(bot_data, bot, other, "c", 2))

    async def _other_finished() -> None:
        while ("end", "c") not in client.order:
            await asyncio.sleep(0)

    await asyncio.wait_for(_other_finished(), timeout=1)
    assert ("start", "b") not in client.order
    assert ("end", "a") not in client.order
    assert first.replies == []
    assert other.replies == ["reply-c"]

    client.release_a.set()
    await asyncio.wait_for(asyncio.gather(task_a, task_b, task_c), timeout=1)

    assert client.order == [
        ("start", "a"),
        ("start", "c"),
        ("end", "c"),
        ("end", "a"),
        ("start", "b"),
        ("end", "b"),
    ]
    assert first.replies == ["reply-a"]
    assert second.replies == ["reply-b"]
    assert other.replies == ["reply-c"]


@pytest.mark.asyncio
async def test_queued_tap_shows_typing_and_spends_the_rate_limit_at_arrival(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ScriptedClient()
    bot = _Bot()
    gate = _gate(per_minute=2)
    bot_data = {"adk_client": client, "access_gate": gate}
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data=bot_data),
        bot=bot,
    )
    replies: list[tuple[int, str]] = []

    async def reply_text(self: Message, text: str, **kwargs) -> None:
        replies.append((self.chat_id, text))

    monkeypatch.setattr(Message, "reply_text", reply_text)

    async def ask(self, user_id: str, session_id: str, text: str) -> str:
        self.order.append(("start", text))
        if text == "мягче":
            self.started_a.set()
            await self.release_a.wait()
        self.order.append(("end", text))
        return f"ответ:{text}"

    monkeypatch.setattr(_ScriptedClient, "ask", ask)

    def _update(query: _Query) -> SimpleNamespace:
        return SimpleNamespace(
            message=None,
            effective_user=SimpleNamespace(id=5),
            callback_query=query,
        )

    first_query = _Query(action_callback_data("мягче"), _message(1, 11))
    second_query = _Query(action_callback_data("дешевле"), _message(2, 22))
    third_query = _Query(action_callback_data("подробнее"), _message(3, 33))

    first = asyncio.create_task(on_callback(_update(first_query), context))
    await client.started_a.wait()
    second = asyncio.create_task(on_callback(_update(second_query), context))
    await asyncio.wait_for(bot.wait_typed(22), timeout=1)
    assert ("start", "дешевле") not in client.order
    assert second_query.answered == 1
    assert first_query.answered == 1

    third = asyncio.create_task(on_callback(_update(third_query), context))
    await asyncio.wait_for(third, timeout=1)
    assert replies == [(33, RATE_LIMIT_MINUTE_TEXT)]
    assert ("start", "подробнее") not in client.order
    assert third_query.answered == 1

    client.release_a.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
    assert [item for item in replies if item[0] != 33] == [
        (11, "ответ:мягче"),
        (22, "ответ:дешевле"),
    ]
    assert client.order == [
        ("start", "мягче"),
        ("end", "мягче"),
        ("start", "дешевле"),
        ("end", "дешевле"),
    ]


@pytest.mark.asyncio
async def test_city_waits_for_the_in_flight_turn() -> None:
    client = _ScriptedClient()
    bot_data = {"adk_client": client, "access_gate": _gate()}
    bot = _Bot()
    target = _Reply("a")
    turn = asyncio.create_task(_turn(bot_data, bot, target, "a", 5))
    await client.started_a.wait()

    message = _Reply("city")
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=5),
    )
    context = SimpleNamespace(
        args=["Warsaw"],
        application=SimpleNamespace(bot_data=bot_data),
        bot=bot,
    )
    city = asyncio.create_task(city_cmd(update, context))
    lock = bot_data["turn_gate"].lock_for(5)

    async def _city_is_queued() -> None:
        while not lock._waiters:
            await asyncio.sleep(0)

    await asyncio.wait_for(_city_is_queued(), timeout=1)
    assert client.patches == []
    assert message.replies == []

    client.release_a.set()
    await asyncio.wait_for(asyncio.gather(turn, city), timeout=1)
    assert client.patches
    assert client.patches[0]["user:city"]
    assert message.replies
    assert message.replies[-1] != CITY_SAVE_FAILED_TEXT


@pytest.mark.asyncio
async def test_feedback_does_not_wait_on_the_session_lock() -> None:
    client = _ScriptedClient()
    bot_data = {"adk_client": client}
    bot = _Bot()
    target = _Reply("a")
    turn = asyncio.create_task(_turn(bot_data, bot, target, "a", 5))
    await client.started_a.wait()
    await asyncio.wait_for(
        record_feedback(bot_data, {"log_type": "feedback", "score": 1, "text": "ок"}),
        timeout=1,
    )
    assert client.feedback == {"log_type": "feedback", "score": 1, "text": "ок"}
    client.release_a.set()
    await asyncio.wait_for(turn, timeout=1)


@pytest.mark.asyncio
async def test_idle_locks_are_dropped_and_a_held_lock_stays() -> None:
    gate = SessionTurnGate(max_idle=2)
    held = gate.lock_for(1)
    await held.acquire()
    gate.lock_for(2)
    assert set(gate._locks) == {1, 2}

    gate.lock_for(3)
    assert set(gate._locks) == {1, 3}
    assert gate.lock_for(1) is held

    same = gate.lock_for(1)
    assert same is held
    held.release()

    gate.lock_for(4)
    assert set(gate._locks) == {4}
    assert gate.lock_for(4) is not held


@pytest.mark.asyncio
async def test_shutdown_closes_the_shared_http_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "s"})
        return httpx.Response(200, json=[{"content": {"parts": [{"text": "ok"}]}}])

    client = AdkHttpClient(
        "https://tea-agent.example",
        transport=httpx.MockTransport(handler),
        auth_secret="sek",
    )
    application = SimpleNamespace(bot_data={"adk_client": client})
    try:
        assert await client.ask("tg-1", "tg-sess-1", "hi") == "ok"
        http = client._http
        assert http is not None and not http.is_closed
    finally:
        await _close_adk_client(application)  # type: ignore[arg-type]
    assert http is not None and http.is_closed
    await _close_adk_client(application)  # type: ignore[arg-type]
