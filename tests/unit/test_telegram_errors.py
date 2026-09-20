# ruff: noqa: RUF001
"""Telegram user-facing texts and error_code logging (TEA-22)."""

from __future__ import annotations

import logging

import pytest

from telegram_integration.adk_client import (
    TEA_AGENT_ERROR,
    TEA_COLD_START,
    TEA_EMPTY_REPLY,
    TEA_QUOTA,
    TEA_SESSION_FAILED,
    TEA_TIMEOUT_RUN,
    TEA_UNAVAILABLE,
    TEA_UNKNOWN,
    AdkClientError,
    AdkHttpClient,
    AdkQuotaError,
    AdkTimeoutError,
    AdkUnavailableError,
    classify_adk_error,
)
from telegram_integration.main import (
    AGENT_ERROR_TEXT,
    COLD_START_TEXT,
    EMPTY_REPLY_TEXT,
    QUOTA_TEXT,
    SESSION_FAILED_TEXT,
    TIMEOUT_RUN_TEXT,
    UNAVAILABLE_TEXT,
    _run_agent_and_reply,
    user_text_for_error_code,
)

_V1_CODES = (
    TEA_COLD_START,
    TEA_TIMEOUT_RUN,
    TEA_UNAVAILABLE,
    TEA_QUOTA,
    TEA_AGENT_ERROR,
    TEA_SESSION_FAILED,
    TEA_EMPTY_REPLY,
    TEA_UNKNOWN,
)


@pytest.mark.parametrize(
    ("err", "code"),
    [
        (AdkTimeoutError("session", error_code=TEA_COLD_START), TEA_COLD_START),
        (AdkTimeoutError("run", error_code=TEA_TIMEOUT_RUN), TEA_TIMEOUT_RUN),
        (AdkUnavailableError("down", status_code=503), TEA_UNAVAILABLE),
        (AdkQuotaError("429", status_code=429), TEA_QUOTA),
        (
            AdkClientError("boom", error_code=TEA_AGENT_ERROR, status_code=500),
            TEA_AGENT_ERROR,
        ),
        (
            AdkClientError("sess", error_code=TEA_SESSION_FAILED, status_code=500),
            TEA_SESSION_FAILED,
        ),
        (RuntimeError("unexpected"), TEA_UNKNOWN),
    ],
)
def test_classify_exception_to_code(err: BaseException, code: str) -> None:
    assert classify_adk_error(err) == code


@pytest.mark.parametrize(
    ("code", "snippet"),
    [
        (TEA_COLD_START, "запускается"),
        (TEA_TIMEOUT_RUN, "слишком долгий"),
        (TEA_UNAVAILABLE, "временно недоступен"),
        (TEA_QUOTA, "лимит бесплатного Gemini"),
        (TEA_AGENT_ERROR, "Сбой на стороне сомелье"),
        (TEA_SESSION_FAILED, "Не удалось открыть сессию"),
        (TEA_EMPTY_REPLY, "Не получилось собрать ответ"),
        (TEA_UNKNOWN, "временно недоступен"),
    ],
)
def test_each_v1_code_has_human_text_without_code(code: str, snippet: str) -> None:
    text = user_text_for_error_code(code)
    assert snippet in text
    assert code not in text
    assert "http" not in text.lower()
    assert "traceback" not in text.lower()


def test_v1_table_is_fully_mapped() -> None:
    for code in _V1_CODES:
        assert user_text_for_error_code(code)
    assert user_text_for_error_code(TEA_QUOTA) == QUOTA_TEXT
    assert user_text_for_error_code(TEA_UNAVAILABLE) == UNAVAILABLE_TEXT
    assert user_text_for_error_code(TEA_COLD_START) == COLD_START_TEXT
    assert user_text_for_error_code(TEA_TIMEOUT_RUN) == TIMEOUT_RUN_TEXT
    assert user_text_for_error_code(TEA_AGENT_ERROR) == AGENT_ERROR_TEXT
    assert user_text_for_error_code(TEA_SESSION_FAILED) == SESSION_FAILED_TEXT
    assert user_text_for_error_code(TEA_EMPTY_REPLY) == EMPTY_REPLY_TEXT
    assert user_text_for_error_code(TEA_UNKNOWN) == UNAVAILABLE_TEXT


class _FakeBot:
    async def send_chat_action(self, **kwargs) -> None:
        return None


class _FakeMessage:
    chat_id = 1

    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


class _FakeClient(AdkHttpClient):
    def __init__(self, result: str | BaseException) -> None:
        super().__init__("https://tea-agent.example")
        self._result = result

    async def ask(self, user_id: str, session_id: str, text: str) -> str:
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


async def _reply(result: str | BaseException, caplog) -> tuple[str, str]:
    target = _FakeMessage()
    with caplog.at_level(logging.WARNING, logger="telegram_integration"):
        await _run_agent_and_reply(
            target=target,  # type: ignore[arg-type]
            telegram_user_id=495970882,
            text="мягкий зелёный",
            bot=_FakeBot(),
            bot_data={"adk_client": _FakeClient(result)},
        )
    assert len(target.replies) == 1
    return target.replies[0], caplog.text


@pytest.mark.asyncio
async def test_cold_start_sends_startup_text(caplog: pytest.LogCaptureFixture) -> None:
    text, logs = await _reply(
        AdkTimeoutError("session GET", error_code=TEA_COLD_START), caplog
    )
    assert text == COLD_START_TEXT
    assert "error_code=TEA_COLD_START" in logs
    assert "временно недоступен" not in text


@pytest.mark.asyncio
async def test_quota_keeps_existing_text(caplog: pytest.LogCaptureFixture) -> None:
    text, logs = await _reply(AdkQuotaError("429", status_code=429), caplog)
    assert text == QUOTA_TEXT
    assert "error_code=TEA_QUOTA" in logs


@pytest.mark.asyncio
async def test_run_500_sends_agent_error(caplog: pytest.LogCaptureFixture) -> None:
    text, logs = await _reply(
        AdkClientError("ADK /run failed", error_code=TEA_AGENT_ERROR, status_code=500),
        caplog,
    )
    assert text == AGENT_ERROR_TEXT
    assert "error_code=TEA_AGENT_ERROR" in logs
    assert "status=500" in logs
    assert "запускается" not in text


@pytest.mark.asyncio
async def test_empty_reply_is_not_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    text, logs = await _reply("", caplog)
    assert text == EMPTY_REPLY_TEXT
    assert "error_code=TEA_EMPTY_REPLY" in logs
    assert "временно недоступен" not in text


@pytest.mark.asyncio
async def test_successful_not_found_text_is_passed_through(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent_text = "Не нашёл такой сорт китайского зелёного чая. Уточните название."
    text, logs = await _reply(agent_text, caplog)
    assert text == agent_text
    assert "error_code=" not in logs


@pytest.mark.asyncio
async def test_timeout_run_and_session_failed_texts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text, logs = await _reply(
        AdkTimeoutError("POST /run", error_code=TEA_TIMEOUT_RUN), caplog
    )
    assert text == TIMEOUT_RUN_TEXT
    assert "error_code=TEA_TIMEOUT_RUN" in logs

    caplog.clear()
    text, logs = await _reply(
        AdkClientError("session", error_code=TEA_SESSION_FAILED, status_code=422),
        caplog,
    )
    assert text == SESSION_FAILED_TEXT
    assert "error_code=TEA_SESSION_FAILED" in logs
