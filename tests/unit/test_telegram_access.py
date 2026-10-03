"""Closed-beta allowlist, invite code, and per-user rate limit (TEA-36)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telegram import Chat, Message, User

from telegram_integration.access import (
    CLOSED_BETA_TEXT,
    CLOSED_BETA_WITH_INVITE_TEXT,
    DEFAULT_RATE_LIMIT_PER_DAY,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    ENV_ACCESS_MODE,
    ENV_ADMIN_USER_IDS,
    ENV_ALLOWED_USER_IDS,
    ENV_INVITE_CODE,
    ENV_RATE_LIMIT_PER_DAY,
    ENV_RATE_LIMIT_PER_MINUTE,
    INVITE_ACCEPTED_TEXT,
    INVITE_REJECTED_TEXT,
    RATE_LIMIT_DAY_TEXT,
    RATE_LIMIT_MINUTE_TEXT,
    AccessConfig,
    AccessGate,
    RateLimiter,
    get_access_gate,
    load_access_config,
    parse_user_ids,
)
from telegram_integration.keyboard import action_callback_data
from telegram_integration.main import START_TEXT, on_callback, on_text, start_cmd

_BETA_ENV = (
    ENV_ACCESS_MODE,
    ENV_ALLOWED_USER_IDS,
    ENV_ADMIN_USER_IDS,
    ENV_INVITE_CODE,
    ENV_RATE_LIMIT_PER_MINUTE,
    ENV_RATE_LIMIT_PER_DAY,
    "TELEGRAM_ALLOWLIST_SECRET",
    "TELEGRAM_INVITE_CODE_SECRET",
)


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


@pytest.fixture(autouse=True)
def _clear_beta_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _BETA_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def agent_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []

    async def fake_ask(bot_data: dict, user_id: int, text: str) -> str:
        calls.append((user_id, text))
        return "ответ сомелье"

    monkeypatch.setattr("telegram_integration.main.ask_agent", fake_ask)
    return calls


def _gate(
    *,
    enforced: bool = True,
    allowed: tuple[int, ...] = (),
    admins: tuple[int, ...] = (),
    invite: str = "",
    per_minute: int = 4,
    per_day: int = 30,
    moment: datetime | None = None,
) -> tuple[AccessGate, _Clock]:
    config = AccessConfig(
        enforced=enforced,
        allowed_user_ids=frozenset(allowed),
        admin_user_ids=frozenset(admins),
        invite_code=invite,
        rate_limit_per_minute=per_minute,
        rate_limit_per_day=per_day,
    )
    clock = _Clock(moment or datetime(2026, 10, 4, 12, 0, tzinfo=UTC))
    limiter = RateLimiter(per_minute, per_day, now=clock)
    return AccessGate(config, rate_limiter=limiter), clock


def _context(gate: AccessGate | None, args: list[str] | None = None) -> SimpleNamespace:
    bot_data: dict = {}
    if gate is not None:
        bot_data["access_gate"] = gate
    application = SimpleNamespace(bot_data=bot_data)
    return SimpleNamespace(
        args=list(args or []), application=application, bot=_FakeBot()
    )


def _text_update(user_id: int, text: str) -> tuple[SimpleNamespace, _FakeMessage]:
    message = _FakeMessage(text)
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=None,
    )
    return update, message


def _telegram_message() -> Message:
    return Message(
        message_id=1,
        date=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="Tea"),
        text="привет",
    )


def test_parse_user_ids_splits_commas_semicolons_and_whitespace() -> None:
    assert parse_user_ids(" 10, 20;30\n40 ") == frozenset({10, 20, 30, 40})
    assert parse_user_ids("abc, 5") == frozenset({5})
    assert parse_user_ids("") == frozenset()
    assert parse_user_ids(None) == frozenset()


def test_local_auto_with_no_policy_is_open() -> None:
    config = load_access_config(webhook=False)
    assert config.enforced is False
    assert config.rate_limit_per_minute == DEFAULT_RATE_LIMIT_PER_MINUTE == 4
    assert config.rate_limit_per_day == DEFAULT_RATE_LIMIT_PER_DAY == 30
    assert AccessGate(config).is_allowed(5)


def test_webhook_auto_with_no_policy_fails_closed() -> None:
    config = load_access_config(webhook=True)
    gate = AccessGate(config)
    assert config.enforced is True
    assert gate.is_allowed(5) is False
    assert gate.denied_text() == CLOSED_BETA_TEXT


def test_local_allowlist_and_admin_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_ALLOWED_USER_IDS, "10, 11;12")
    monkeypatch.setenv(ENV_ADMIN_USER_IDS, "99")
    config = load_access_config(webhook=False)
    gate = AccessGate(config)
    assert config.enforced is True
    assert gate.is_allowed(10)
    assert gate.is_allowed(99)
    assert gate.is_allowed(11)
    assert gate.is_allowed(5) is False


def test_open_mode_ignores_webhook_and_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_ACCESS_MODE, "open")
    monkeypatch.setenv(ENV_ALLOWED_USER_IDS, "10")
    config = load_access_config(webhook=True)
    assert config.enforced is False
    assert AccessGate(config).is_allowed(5)


def test_closed_mode_blocks_local_even_without_a_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_ACCESS_MODE, "closed")
    config = load_access_config(webhook=False)
    assert config.enforced is True
    assert AccessGate(config).is_allowed(1) is False


def test_invalid_rate_limit_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_RATE_LIMIT_PER_MINUTE, "many")
    monkeypatch.setenv(ENV_RATE_LIMIT_PER_DAY, "-3")
    config = load_access_config(webhook=False)
    assert config.rate_limit_per_minute == DEFAULT_RATE_LIMIT_PER_MINUTE
    assert config.rate_limit_per_day == DEFAULT_RATE_LIMIT_PER_DAY


def test_zero_rate_limit_disables_that_bucket() -> None:
    limiter = RateLimiter(0, 0)
    for _ in range(5):
        assert limiter.consume(1).allowed


def test_invite_grant_is_memory_only_and_lost_on_restart() -> None:
    config = AccessConfig(
        enforced=True,
        allowed_user_ids=frozenset(),
        admin_user_ids=frozenset(),
        invite_code="pilot-code",
        rate_limit_per_minute=4,
        rate_limit_per_day=30,
    )
    gate = AccessGate(config)
    assert gate.redeem_invite(7, "nope") is False
    assert gate.redeem_invite(7, "pilot") is False
    assert gate.is_allowed(7) is False
    assert gate.redeem_invite(7, "pilot-code") is True
    assert gate.redeem_invite(7, "Pilot-code") is False
    assert gate.is_allowed(7) is True

    restarted = AccessGate(config)
    assert restarted.invited_user_ids == set()
    assert restarted.is_allowed(7) is False
    assert restarted.redeem_invite(7, "pilot-code") is True
    assert restarted.is_allowed(7) is True


def test_allowlist_survives_a_new_gate() -> None:
    config = AccessConfig(
        enforced=True,
        allowed_user_ids=frozenset({15}),
        admin_user_ids=frozenset({1}),
        invite_code="",
        rate_limit_per_minute=4,
        rate_limit_per_day=30,
    )
    assert AccessGate(config).is_allowed(15)
    assert AccessGate(config).is_allowed(1)
    assert AccessGate(config).is_allowed(99) is False


def test_admin_bypasses_allowlist_but_not_the_rate_limit() -> None:
    gate, _clock = _gate(admins=(7,), allowed=(8,), per_minute=1)
    assert gate.is_allowed(7)
    assert gate.is_allowed(8)
    assert gate.is_allowed(9) is False
    assert gate.consume_rate(7).allowed
    denied = gate.consume_rate(7)
    assert denied.allowed is False
    assert denied.reason == "minute"
    assert denied.user_text == RATE_LIMIT_MINUTE_TEXT


def test_minute_window_blocks_then_resets() -> None:
    clock = _Clock(datetime(2026, 10, 4, 12, 0, 30, tzinfo=UTC))
    limiter = RateLimiter(2, 30, now=clock)
    assert limiter.consume(1).allowed
    assert limiter.consume(1).allowed
    blocked = limiter.consume(1)
    assert blocked.allowed is False
    assert blocked.reason == "minute"
    assert blocked.user_text == RATE_LIMIT_MINUTE_TEXT
    assert limiter.consume(1).allowed is False

    clock.moment = datetime(2026, 10, 4, 12, 1, tzinfo=UTC)
    reset = limiter.consume(1)
    assert reset.allowed
    assert reset.reason == ""


def test_daily_cap_blocks_then_resets_next_utc_day() -> None:
    clock = _Clock(datetime(2026, 10, 4, 23, 59, tzinfo=UTC))
    limiter = RateLimiter(10, 2, now=clock)
    assert limiter.consume(1).allowed
    assert limiter.consume(1).allowed
    blocked = limiter.consume(1)
    assert blocked.allowed is False
    assert blocked.reason == "day"
    assert blocked.user_text == RATE_LIMIT_DAY_TEXT

    clock.moment = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    assert limiter.consume(1).allowed


def test_rate_limit_is_per_user() -> None:
    limiter = RateLimiter(1, 30)
    assert limiter.consume(1).allowed
    assert limiter.consume(1).allowed is False
    assert limiter.consume(2).allowed


@pytest.mark.asyncio
async def test_allowed_user_message_calls_agent(
    agent_calls: list[tuple[int, str]],
) -> None:
    gate, _clock = _gate(allowed=(5,))
    update, message = _text_update(5, "лунцзин")
    await on_text(update, _context(gate))
    assert agent_calls == [(5, "лунцзин")]
    assert message.replies == ["ответ сомелье"]


@pytest.mark.asyncio
async def test_blocked_user_gets_closed_beta_and_agent_is_not_called(
    agent_calls: list[tuple[int, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate, _clock = _gate()
    update, message = _text_update(4, "мягкий улун")
    with caplog.at_level("INFO", logger="telegram_integration"):
        await on_text(update, _context(gate))
    assert agent_calls == []
    assert message.replies == [CLOSED_BETA_TEXT]
    assert "blocked non-allowlisted telegram user 4" in caplog.text
    assert "мягкий улун" not in caplog.text


@pytest.mark.asyncio
async def test_missing_gate_fails_closed(agent_calls: list[tuple[int, str]]) -> None:
    update, message = _text_update(4, "привет")
    await on_text(update, _context(None))
    assert agent_calls == []
    assert message.replies == [CLOSED_BETA_TEXT]
    assert isinstance(get_access_gate({}), AccessGate)


@pytest.mark.asyncio
async def test_admin_message_calls_agent(agent_calls: list[tuple[int, str]]) -> None:
    gate, _clock = _gate(admins=(7,))
    update, message = _text_update(7, "шен")
    await on_text(update, _context(gate))
    assert agent_calls == [(7, "шен")]
    assert message.replies == ["ответ сомелье"]


@pytest.mark.asyncio
async def test_invite_code_on_start_then_message_calls_agent(
    agent_calls: list[tuple[int, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate, _clock = _gate(invite="beta-2026")
    start_update, start_message = _text_update(4, "/start beta-2026")
    with caplog.at_level("INFO", logger="telegram_integration"):
        await start_cmd(start_update, _context(gate, args=["beta-2026"]))
    assert agent_calls == []
    assert start_message.replies == [INVITE_ACCEPTED_TEXT + START_TEXT]
    assert "invite redeemed by telegram user 4" in caplog.text
    assert "beta-2026" not in caplog.text
    assert gate.is_allowed(4)

    update, message = _text_update(4, "бай му дань")
    await on_text(update, _context(gate))
    assert agent_calls == [(4, "бай му дань")]
    assert message.replies == ["ответ сомелье"]


@pytest.mark.asyncio
async def test_wrong_invite_code_does_not_call_agent(
    agent_calls: list[tuple[int, str]],
) -> None:
    gate, _clock = _gate(invite="beta-2026")
    update, message = _text_update(4, "/start nope")
    await start_cmd(update, _context(gate, args=["nope"]))
    assert agent_calls == []
    assert message.replies == [INVITE_REJECTED_TEXT]
    assert gate.is_allowed(4) is False

    text_update, text_message = _text_update(4, "beta-2026")
    await on_text(text_update, _context(gate))
    assert agent_calls == []
    assert text_message.replies == [CLOSED_BETA_WITH_INVITE_TEXT]
    assert gate.is_allowed(4) is False


@pytest.mark.asyncio
async def test_start_without_code_tells_stranger_how_to_join(
    agent_calls: list[tuple[int, str]],
) -> None:
    gate, _clock = _gate(invite="beta-2026")
    update, message = _text_update(4, "/start")
    await start_cmd(update, _context(gate))
    assert agent_calls == []
    assert message.replies == [CLOSED_BETA_WITH_INVITE_TEXT]


@pytest.mark.asyncio
async def test_allowed_start_is_the_greeting(
    agent_calls: list[tuple[int, str]],
) -> None:
    gate, _clock = _gate(allowed=(5,))
    update, message = _text_update(5, "/start")
    await start_cmd(update, _context(gate))
    assert agent_calls == []
    assert message.replies == [START_TEXT]


@pytest.mark.asyncio
async def test_invite_guesses_are_rate_limited_until_the_window_resets(
    agent_calls: list[tuple[int, str]],
) -> None:
    gate, clock = _gate(invite="beta-2026", per_minute=1)
    update, message = _text_update(4, "/start")
    await start_cmd(update, _context(gate, args=["wrong"]))
    assert message.replies == [INVITE_REJECTED_TEXT]

    await start_cmd(update, _context(gate, args=["beta-2026"]))
    assert message.replies[-1] == RATE_LIMIT_MINUTE_TEXT
    assert gate.is_allowed(4) is False
    assert agent_calls == []

    clock.moment = datetime(2026, 10, 4, 12, 1, tzinfo=UTC)
    await start_cmd(update, _context(gate, args=["beta-2026"]))
    assert gate.is_allowed(4)
    assert message.replies[-1] == INVITE_ACCEPTED_TEXT + START_TEXT
    assert agent_calls == []


@pytest.mark.asyncio
async def test_text_rate_limit_blocks_agent_then_resets(
    agent_calls: list[tuple[int, str]],
) -> None:
    gate, clock = _gate(allowed=(5,), per_minute=1, per_day=30)
    update, message = _text_update(5, "лунцзин")
    context = _context(gate)
    await on_text(update, context)
    await on_text(update, context)
    assert agent_calls == [(5, "лунцзин")]
    assert message.replies[-1] == RATE_LIMIT_MINUTE_TEXT

    clock.moment = datetime(2026, 10, 4, 12, 1, tzinfo=UTC)
    await on_text(update, context)
    assert agent_calls == [(5, "лунцзин"), (5, "лунцзин")]
    assert message.replies[-1] == "ответ сомелье"


@pytest.mark.asyncio
async def test_daily_cap_on_text_uses_the_day_message(
    agent_calls: list[tuple[int, str]],
) -> None:
    gate, clock = _gate(allowed=(5,), per_minute=10, per_day=1)
    update, message = _text_update(5, "шу")
    context = _context(gate)
    await on_text(update, context)
    await on_text(update, context)
    assert agent_calls == [(5, "шу")]
    assert message.replies[-1] == RATE_LIMIT_DAY_TEXT

    clock.moment = datetime(2026, 10, 5, 0, 1, tzinfo=UTC)
    await on_text(update, context)
    assert len(agent_calls) == 2


@pytest.mark.asyncio
async def test_allowed_callback_calls_agent(
    agent_calls: list[tuple[int, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        sent.append(text)

    monkeypatch.setattr(Message, "reply_text", reply_text)
    gate, _clock = _gate(allowed=(5,))
    query = _FakeQuery(action_callback_data("мягче"), _telegram_message())
    update = SimpleNamespace(
        message=None,
        effective_user=SimpleNamespace(id=5),
        callback_query=query,
    )
    await on_callback(update, _context(gate))
    assert query.answered == 1
    assert agent_calls == [(5, "мягче")]
    assert sent == ["ответ сомелье"]


@pytest.mark.asyncio
async def test_blocked_callback_does_not_call_agent(
    agent_calls: list[tuple[int, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        sent.append(text)

    monkeypatch.setattr(Message, "reply_text", reply_text)
    gate, _clock = _gate(invite="beta-2026")
    query = _FakeQuery(action_callback_data("дешевле"), _telegram_message())
    update = SimpleNamespace(
        message=None,
        effective_user=SimpleNamespace(id=4),
        callback_query=query,
    )
    await on_callback(update, _context(gate))
    assert query.answered == 1
    assert agent_calls == []
    assert sent == [CLOSED_BETA_WITH_INVITE_TEXT]


@pytest.mark.asyncio
async def test_callback_rate_limit_blocks_agent_then_resets(
    agent_calls: list[tuple[int, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        sent.append(text)

    monkeypatch.setattr(Message, "reply_text", reply_text)
    gate, clock = _gate(allowed=(5,), per_minute=1)
    query = _FakeQuery(action_callback_data("подробнее"), _telegram_message())
    update = SimpleNamespace(
        message=None,
        effective_user=SimpleNamespace(id=5),
        callback_query=query,
    )
    context = _context(gate)
    await on_callback(update, context)
    await on_callback(update, context)
    assert agent_calls == [(5, "подробнее")]
    assert sent[-1] == RATE_LIMIT_MINUTE_TEXT
    assert query.answered == 2

    clock.moment = datetime(2026, 10, 4, 12, 1, tzinfo=UTC)
    await on_callback(update, context)
    assert agent_calls == [(5, "подробнее"), (5, "подробнее")]
    assert sent[-1] == "ответ сомелье"


@pytest.mark.asyncio
async def test_invalid_callback_does_not_call_agent(
    agent_calls: list[tuple[int, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        sent.append(text)

    monkeypatch.setattr(Message, "reply_text", reply_text)
    gate, _clock = _gate()
    query = _FakeQuery("tea:a:not-a-real-action", _telegram_message())
    update = SimpleNamespace(
        message=None,
        effective_user=SimpleNamespace(id=4),
        callback_query=query,
    )
    await on_callback(update, _context(gate))
    assert query.answered == 1
    assert agent_calls == []
    assert sent == []


def test_attach_access_matches_webhook_versus_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram_integration import main as bot

    webhook_app = SimpleNamespace(bot_data={})
    bot._attach_access(webhook_app, webhook=True)
    assert webhook_app.bot_data["access_gate"].config.enforced is True
    assert webhook_app.bot_data["access_gate"].is_allowed(3) is False

    monkeypatch.setenv(ENV_ALLOWED_USER_IDS, "3")
    local_app = SimpleNamespace(bot_data={})
    bot._attach_access(local_app, webhook=False)
    assert local_app.bot_data["access_gate"].config.enforced is True
    assert local_app.bot_data["access_gate"].is_allowed(3) is True
    assert local_app.bot_data["access_gate"].is_allowed(4) is False
