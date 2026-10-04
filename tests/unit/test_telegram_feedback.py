# ruff: noqa: RUF001
"""In-chat 👍 / 👎 and /feedback (TEA-17)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from telegram import Chat, Message, User
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from tea_agent.app_utils.agent_auth import AUTH_HEADER
from tea_agent.next_steps import ACTION_LABELS, HEADING, format_next_steps_block
from telegram_integration.access import (
    CLOSED_BETA_TEXT,
    RATE_LIMIT_MINUTE_TEXT,
    AccessConfig,
    AccessGate,
    RateLimiter,
)
from telegram_integration.adk_client import AdkHttpClient
from telegram_integration.feedback import (
    FEEDBACK_PROMPT_TEXT,
    FEEDBACK_SAVED_TEXT,
    FEEDBACK_SKIPPED_TEXT,
    HELP_TEXT,
    THUMBS_DOWN_TEXT,
    THUMBS_UP_TEXT,
    feedback_cmd,
    help_cmd,
    on_feedback_callback,
    record_feedback,
)
from telegram_integration.keyboard import (
    CALLBACK_PATTERN,
    FEEDBACK_CALLBACK_PATTERN,
    action_callback_data,
    feedback_callback_data,
    parse_action_callback,
    parse_feedback_callback,
    prepare_telegram_reply,
)
from telegram_integration.main import (
    _register_handlers,
    on_callback,
    on_text,
)

LONGJING_URL = "https://www.teashop.by/product/longjing-1/"
BILUOCHUN_URL = "https://www.teashop.by/product/duntin-bi-lo-chun/"


class _Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


class _FakeBot:
    async def send_chat_action(self, **kwargs) -> None:
        return None


class _FakeMessage:
    chat_id = 1

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


class _FakeQuery:
    def __init__(self, data: str, message: Message) -> None:
        self.data = data
        self.message = message
        self.answered = 0

    async def answer(self, *args, **kwargs) -> None:
        self.answered += 1


def _gate(
    *,
    allowed: tuple[int, ...] = (),
    per_minute: int = 4,
    per_day: int = 30,
) -> AccessGate:
    config = AccessConfig(
        enforced=True,
        allowed_user_ids=frozenset(allowed),
        admin_user_ids=frozenset(),
        invite_code="",
        rate_limit_per_minute=per_minute,
        rate_limit_per_day=per_day,
    )
    limiter = RateLimiter(
        per_minute,
        per_day,
        now=_Clock(datetime(2026, 10, 4, 12, 0, tzinfo=UTC)),
    )
    return AccessGate(config, rate_limiter=limiter)


def _context(gate: AccessGate, args: list[str] | None = None) -> SimpleNamespace:
    application = SimpleNamespace(bot_data={"access_gate": gate})
    return SimpleNamespace(
        args=list(args or []),
        application=application,
        bot=_FakeBot(),
    )


def _text_update(user_id: int, text: str) -> tuple[SimpleNamespace, _FakeMessage]:
    message = _FakeMessage(text)
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=None,
    )
    return update, message


def _telegram_message(text: str = "1. Лунцзин — мягкий утренний чай.") -> Message:
    return Message(
        message_id=77,
        date=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="Tea"),
        text=text,
    )


def _callback_update(
    user_id: int, data: str, text: str | None = None
) -> SimpleNamespace:
    message = _telegram_message() if text is None else _telegram_message(text)
    return SimpleNamespace(
        message=None,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=_FakeQuery(data, message),
    )


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    rows: list[dict] = []

    async def fake_record(bot_data: dict, payload: dict) -> None:
        rows.append(payload)

    monkeypatch.setattr("telegram_integration.feedback.record_feedback", fake_record)
    return rows


@pytest.fixture
def agent_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []

    async def fake_ask(bot_data: dict, user_id: int, text: str) -> str:
        calls.append((user_id, text))
        return "ответ сомелье"

    monkeypatch.setattr("telegram_integration.main.ask_agent", fake_ask)
    return calls


@pytest.fixture
def sent_replies(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        rows.append((text, kwargs))

    monkeypatch.setattr(Message, "reply_text", reply_text)
    return rows


def _recommendation() -> str:
    block = format_next_steps_block(
        [
            {"product_name": "Си Ху Лун Цзин", "product_url": LONGJING_URL},
            {"product_name": "Дунтин Би Ло Чунь", "product_url": BILUOCHUN_URL},
        ]
    )
    return f"1. Лунцзин — мягкий утренний чай.\n2. Би Ло Чунь — без горечи.\n\n{block}"


def test_thumbs_share_the_recommendation_keyboard_and_fit_telegram_limits() -> None:
    chunks, markup = prepare_telegram_reply(_recommendation())
    assert markup is not None
    assert HEADING not in "\n".join(chunks)
    rows = markup.to_dict()["inline_keyboard"]
    buttons = [button for row in rows for button in row]
    assert sum(len(row) for row in rows) <= 100
    assert all(len(row) <= 8 for row in rows)
    assert [button["text"] for button in rows[-1]] == ["👍", "👎"]
    for button in rows[-1]:
        payload = button["callback_data"]
        assert len(payload.encode("utf-8")) <= 64
        assert parse_feedback_callback(payload) in {"up", "down"}
        assert parse_action_callback(payload) is None
        assert CALLBACK_PATTERN.match(payload) is None
        assert FEEDBACK_CALLBACK_PATTERN.match(payload)
    action_labels = [
        button["text"]
        for button in buttons
        if str(button.get("callback_data", "")).startswith("tea:a:")
    ]
    for label in ACTION_LABELS:
        assert label in action_labels
    urls = [button["url"] for button in buttons if "url" in button]
    assert LONGJING_URL in urls
    assert BILUOCHUN_URL in urls


def test_plain_reply_has_no_thumbs() -> None:
    chunks, markup = prepare_telegram_reply("Какой у вас опыт с китайским чаем?")
    assert markup is None
    assert chunks == ["Какой у вас опыт с китайским чаем?"]


def test_feedback_callback_data_is_not_an_agent_action() -> None:
    for kind in ("up", "down", "skip"):
        payload = feedback_callback_data(kind)
        assert len(payload.encode("utf-8")) <= 64
        assert parse_feedback_callback(payload) == kind
        assert parse_action_callback(payload) is None
    assert parse_feedback_callback(action_callback_data("мягче")) is None
    assert parse_feedback_callback("tea:f:nope") is None
    assert parse_feedback_callback("tea:f:up-extra") is None


@pytest.mark.asyncio
async def test_submit_feedback_posts_token_and_does_not_open_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEA_AGENT_AUTH_SECRET", "sek")
    seen: list[tuple[str, str, str | None, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers.get(AUTH_HEADER),
                json.loads(request.content.decode()),
            )
        )
        return httpx.Response(200, json={"status": "success"})

    client = AdkHttpClient(
        "https://tea-agent.example",
        transport=httpx.MockTransport(handler),
    )
    await client.submit_feedback(
        {"log_type": "feedback", "score": 1, "user_id": "tg-5"}
    )
    assert seen == [
        (
            "POST",
            "/feedback",
            "sek",
            {"log_type": "feedback", "score": 1, "user_id": "tg-5"},
        )
    ]


@pytest.mark.asyncio
async def test_successful_post_is_not_copied_to_the_local_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict] = []
    monkeypatch.setattr(
        "telegram_integration.feedback.log_feedback",
        lambda payload: logged.append(payload),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success"})

    client = AdkHttpClient(
        "https://tea-agent.example",
        transport=httpx.MockTransport(handler),
        auth_secret="sek",
    )
    await record_feedback({"adk_client": client}, {"text": "ок", "score": 1})
    assert logged == []


@pytest.mark.asyncio
async def test_failed_post_is_logged_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[dict] = []
    monkeypatch.setattr(
        "telegram_integration.feedback.log_feedback",
        lambda payload: logged.append(payload),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})

    client = AdkHttpClient(
        "https://tea-agent.example",
        transport=httpx.MockTransport(handler),
        auth_secret="sek",
    )
    await record_feedback(
        {"adk_client": client}, {"text": "горчит", "log_type": "feedback"}
    )
    assert logged == [{"text": "горчит", "log_type": "feedback"}]


@pytest.mark.asyncio
async def test_local_polling_logs_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[dict] = []
    monkeypatch.setattr(
        "telegram_integration.feedback.log_feedback",
        lambda payload: logged.append(payload),
    )
    payload = {"text": "ок", "user_id": "tg-5", "log_type": "feedback"}
    await record_feedback({}, payload)
    assert logged == [payload]


@pytest.mark.asyncio
async def test_thumbs_up_records_user_session_and_message_without_quota(
    stored: list[dict],
    agent_calls: list[tuple[int, str]],
    sent_replies: list[tuple[str, dict]],
) -> None:
    gate = _gate(allowed=(5,), per_minute=1)
    context = _context(gate)
    await on_feedback_callback(
        _callback_update(5, feedback_callback_data("up")),
        context,
    )
    assert agent_calls == []
    assert stored[0]["score"] == 1
    assert stored[0]["rating"] == "up"
    assert stored[0]["source"] == "thumbs"
    assert stored[0]["user_id"] == "tg-5"
    assert stored[0]["session_id"] == "tg-sess-5"
    assert stored[0]["message_id"] == "77"
    assert "Лунцзин" in stored[0]["reply_excerpt"]
    assert sent_replies[0][0] == THUMBS_UP_TEXT

    update, message = _text_update(5, "лунцзин")
    await on_text(update, context)
    assert agent_calls == [(5, "лунцзин")]
    assert message.replies[-1] == "ответ сомелье"


@pytest.mark.asyncio
async def test_thumbs_down_followup_is_feedback_not_an_agent_turn(
    stored: list[dict],
    agent_calls: list[tuple[int, str]],
    sent_replies: list[tuple[str, dict]],
) -> None:
    gate = _gate(allowed=(5,), per_minute=1)
    context = _context(gate)
    await on_feedback_callback(
        _callback_update(5, feedback_callback_data("down")),
        context,
    )
    assert agent_calls == []
    assert stored[0]["score"] == 0
    assert stored[0]["rating"] == "down"
    assert stored[0]["source"] == "thumbs"
    assert sent_replies[0][0] == THUMBS_DOWN_TEXT
    skip = sent_replies[0][1]["reply_markup"].to_dict()["inline_keyboard"][0][0]
    assert skip["callback_data"] == feedback_callback_data("skip")

    update, message = _text_update(5, "слишком горько")
    await on_text(update, context)
    assert agent_calls == []
    assert message.replies == [FEEDBACK_SAVED_TEXT]
    assert stored[1]["source"] == "thumbs_reason"
    assert stored[1]["text"] == "слишком горько"
    assert stored[1]["score"] == 0
    assert stored[1]["message_id"] == "77"
    assert stored[1]["user_id"] == "tg-5"

    update, message = _text_update(5, "лунцзин")
    await on_text(update, context)
    assert agent_calls == [(5, "лунцзин")]


@pytest.mark.asyncio
async def test_skip_returns_the_next_message_to_the_sommelier(
    stored: list[dict],
    agent_calls: list[tuple[int, str]],
    sent_replies: list[tuple[str, dict]],
) -> None:
    gate = _gate(allowed=(5,))
    context = _context(gate)
    await on_feedback_callback(
        _callback_update(5, feedback_callback_data("down")),
        context,
    )
    await on_feedback_callback(
        _callback_update(5, feedback_callback_data("skip")),
        context,
    )
    assert sent_replies[-1][0] == FEEDBACK_SKIPPED_TEXT
    assert len(stored) == 1
    update, _message = _text_update(5, "шен пуэр")
    await on_text(update, context)
    assert agent_calls == [(5, "шен пуэр")]


@pytest.mark.asyncio
async def test_next_step_button_cancels_a_pending_reason(
    stored: list[dict],
    agent_calls: list[tuple[int, str]],
    sent_replies: list[tuple[str, dict]],
) -> None:
    gate = _gate(allowed=(5,))
    context = _context(gate)
    await on_feedback_callback(
        _callback_update(5, feedback_callback_data("down")),
        context,
    )
    await on_callback(_callback_update(5, action_callback_data("мягче")), context)
    assert agent_calls == [(5, "мягче")]
    update, _message = _text_update(5, "ещё мягче")
    await on_text(update, context)
    assert agent_calls[-1] == (5, "ещё мягче")
    assert all(row["source"] != "thumbs_reason" for row in stored)


@pytest.mark.asyncio
async def test_feedback_command_with_and_without_text(
    stored: list[dict],
    agent_calls: list[tuple[int, str]],
) -> None:
    gate = _gate(allowed=(5,), per_minute=1)
    context = _context(gate, args=["кнопка купить ведёт не туда"])
    update, message = _text_update(5, "/feedback")
    await feedback_cmd(update, context)
    assert message.replies == [FEEDBACK_SAVED_TEXT]
    assert stored[0]["source"] == "command"
    assert stored[0]["text"] == "кнопка купить ведёт не туда"
    assert stored[0]["score"] is None
    assert stored[0]["user_id"] == "tg-5"
    assert stored[0]["session_id"] == "tg-sess-5"

    context.args = []
    await feedback_cmd(update, context)
    assert message.replies[-1] == FEEDBACK_PROMPT_TEXT
    follow, follow_message = _text_update(5, "после рестарта забыл вкус")
    await on_text(follow, context)
    assert follow_message.replies == [FEEDBACK_SAVED_TEXT]
    assert stored[-1]["source"] == "command"
    assert stored[-1]["text"] == "после рестарта забыл вкус"
    assert agent_calls == []

    question, question_message = _text_update(5, "бай му дань")
    await on_text(question, context)
    assert agent_calls == [(5, "бай му дань")]
    assert question_message.replies[-1] == "ответ сомелье"


@pytest.mark.asyncio
async def test_rate_limited_user_can_still_send_feedback(
    stored: list[dict],
    agent_calls: list[tuple[int, str]],
    sent_replies: list[tuple[str, dict]],
) -> None:
    gate = _gate(allowed=(5,), per_minute=1)
    context = _context(gate)
    update, message = _text_update(5, "лунцзин")
    await on_text(update, context)
    await on_text(update, context)
    assert message.replies[-1] == RATE_LIMIT_MINUTE_TEXT
    assert agent_calls == [(5, "лунцзин")]

    await on_feedback_callback(
        _callback_update(5, feedback_callback_data("up")),
        context,
    )
    assert stored[-1]["rating"] == "up"
    context.args = ["всё равно хочу оставить отзыв"]
    await feedback_cmd(update, context)
    assert message.replies[-1] == FEEDBACK_SAVED_TEXT
    assert stored[-1]["source"] == "command"
    assert agent_calls == [(5, "лунцзин")]


@pytest.mark.asyncio
async def test_blocked_user_cannot_record_feedback(
    stored: list[dict],
    agent_calls: list[tuple[int, str]],
    sent_replies: list[tuple[str, dict]],
) -> None:
    gate = _gate(allowed=(5,))
    context = _context(gate, args=["привет"])
    await on_feedback_callback(
        _callback_update(9, feedback_callback_data("up")),
        context,
    )
    update, message = _text_update(9, "/feedback")
    await feedback_cmd(update, context)
    await help_cmd(update, context)
    assert stored == []
    assert agent_calls == []
    assert sent_replies[0][0] == CLOSED_BETA_TEXT
    assert message.replies == [CLOSED_BETA_TEXT, CLOSED_BETA_TEXT]


@pytest.mark.asyncio
async def test_help_does_not_spend_the_message_quota(
    agent_calls: list[tuple[int, str]],
) -> None:
    gate = _gate(allowed=(5,), per_minute=1)
    context = _context(gate)
    update, message = _text_update(5, "/help")
    await help_cmd(update, context)
    assert message.replies == [HELP_TEXT]
    question, _question_message = _text_update(5, "габа")
    await on_text(question, context)
    assert agent_calls == [(5, "габа")]


@pytest.mark.asyncio
async def test_action_callback_still_ignores_feedback_payload(
    agent_calls: list[tuple[int, str]],
    sent_replies: list[tuple[str, dict]],
) -> None:
    gate = _gate(allowed=(5,))
    await on_callback(
        _callback_update(5, feedback_callback_data("up")),
        _context(gate),
    )
    assert agent_calls == []
    assert sent_replies == []


def test_handlers_include_help_feedback_and_both_callback_prefixes() -> None:
    application = (
        Application.builder()
        .token("123456789:AAEtestTokenValueForUnitTestOnly")
        .build()
    )
    _register_handlers(application)
    commands: set[str] = set()
    patterns: list[str] = []
    for handlers in application.handlers.values():
        for handler in handlers:
            if isinstance(handler, CommandHandler):
                commands.update(handler.commands)
            if (
                isinstance(handler, CallbackQueryHandler)
                and handler.pattern is not None
            ):
                pattern = handler.pattern
                patterns.append(
                    pattern.pattern if hasattr(pattern, "pattern") else str(pattern)
                )
    assert {"start", "help", "city", "currency", "feedback"} <= commands
    assert any(item.startswith("^tea:f:") for item in patterns)
    assert any(item.startswith("^tea:c:") for item in patterns)
    assert any(item.startswith("^tea:a:") for item in patterns)
