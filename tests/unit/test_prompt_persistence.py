# ruff: noqa: RUF001
"""Prompt must not promise profile persistence the configured backend cannot keep."""

from __future__ import annotations

import pytest

from tea_agent.agent import build_instruction, instruction_text, root_agent
from tea_agent.app_utils.session_uri import LOCAL_SQLITE_URI

_FALSE_SERVICE_RESTART = "переживает рестарт сервиса"
_KEPT_RULES = (
    "{user:experience?}",
    "{user:taste_profile?}",
    "ровно 3 сорта",
    "ask_sommelier — ТОЛЬКО",
    "медицинских обещаний",
    "### Что дальше",
    "save_taste_profile",
    "find_local_shops",
    "save_user_location",
    "{user:city?}",
    "магазины рядом",
    "{user:currency?}",
    "price_display",
    "### На витрине",
    "ExchangeRate-API",
    "NBRB",
)


def _clear_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SESSION_SERVICE_URI",
        "CLOUD_SQL_INSTANCE",
        "SESSION_DB_PASSWORD",
        "GOOGLE_CLOUD_AGENT_ENGINE_ID",
        "K_SERVICE",
    ):
        monkeypatch.delenv(name, raising=False)


class _Session:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.app_name = "tea_agent"
        self.user_id = "tg-1"
        self.id = "tg-sess-1"


class _Invocation:
    def __init__(self, state: dict) -> None:
        self.session = _Session(state)
        self.artifact_service = None


class _Ctx:
    def __init__(self, state: dict) -> None:
        self._invocation_context = _Invocation(state)


def test_default_instruction_does_not_promise_restart_survival(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_backend_env(monkeypatch)
    text = instruction_text()
    assert "___PROFILE_PERSISTENCE_LINE___" not in text
    assert _FALSE_SERVICE_RESTART not in text
    assert "не обещай, что он сохранится" in text
    assert "только память этого процесса" in text
    for rule in _KEPT_RULES:
        assert rule in text
    assert root_agent.instruction is build_instruction


def test_local_sqlite_instruction_limits_survival_to_this_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_backend_env(monkeypatch)
    monkeypatch.setenv("SESSION_SERVICE_URI", LOCAL_SQLITE_URI)
    text = instruction_text()
    assert _FALSE_SERVICE_RESTART not in text
    assert "локальный файл сессий" in text
    assert "не деплой и не замену" in text
    for rule in _KEPT_RULES:
        assert rule in text


def test_persistent_backend_instruction_may_say_restart_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_backend_env(monkeypatch)
    monkeypatch.setenv("CLOUD_SQL_INSTANCE", "demo:europe-central2:tea-sessions")
    monkeypatch.setenv("SESSION_DB_PASSWORD", "secret")
    text = instruction_text()
    assert "постоянное хранилище сессий" in text
    assert _FALSE_SERVICE_RESTART in text
    assert "не обещай, что он сохранится" not in text


def test_cloud_run_sqlite_instruction_does_not_promise_survival(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_backend_env(monkeypatch)
    monkeypatch.setenv("K_SERVICE", "tea-agent")
    monkeypatch.setenv("SESSION_SERVICE_URI", LOCAL_SQLITE_URI)
    monkeypatch.setenv("TEA_ALLOW_EPHEMERAL_SESSIONS", "true")
    text = instruction_text()
    assert _FALSE_SERVICE_RESTART not in text
    assert "не обещай, что он сохранится" in text


@pytest.mark.asyncio
async def test_build_instruction_injects_profile_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_backend_env(monkeypatch)
    ctx = _Ctx({"user:experience": "новичок", "user:vessel": "кружка"})
    text, bypass = await root_agent.canonical_instruction(ctx)  # type: ignore[arg-type]
    assert bypass is True
    assert "новичок" in text
    assert "кружка" in text
    assert "{user:" not in text
    assert _FALSE_SERVICE_RESTART not in text
    assert "ровно 3 сорта" in text
