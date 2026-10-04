"""Memory Bank callback, gating, and config (TEA-9, TEA-44)."""

from __future__ import annotations

import logging

import pytest
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from tea_agent.agent import (
    ask_sommelier,
    build_root_agent,
    compare_teas,
    find_in_shop,
    find_local_shops,
    get_tea_card,
    instruction_text,
    resolve_tea,
    save_taste_profile,
    save_user_location,
    search_teas,
    similar_teas,
)
from tea_agent.memory import (
    generate_memories_callback,
    log_memory_bank_status,
    memory_bank_enabled,
)
from tea_agent.next_steps import attach_next_steps_to_response, collect_turn_hits

_CATALOG_TOOLS = [
    resolve_tea,
    search_teas,
    get_tea_card,
    similar_teas,
    compare_teas,
    find_in_shop,
    find_local_shops,
    save_user_location,
    save_taste_profile,
    ask_sommelier,
]
_PAST_SESSION_SENTENCE = "Если в контексте есть факты из прошлых сессий"
_NO_LONG_TERM_MEMORY = "Долгосрочной памяти между сессиями нет"
_OFF_LOG = (
    "Memory Bank: off (GOOGLE_CLOUD_AGENT_ENGINE_ID unset); "
    "PreloadMemoryTool and generate_memories_callback are not attached"
)
_ON_LOG = (
    "Memory Bank: on (GOOGLE_CLOUD_AGENT_ENGINE_ID is set); "
    "PreloadMemoryTool and generate_memories_callback are attached"
)


class _FakeContext:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls = 0
        self._fail = fail

    async def add_session_to_memory(self) -> None:
        self.calls += 1
        if self._fail is not None:
            raise self._fail


def _disable_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)


def _enable_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "engine-123")


def _walk(agent):
    yield agent
    for child in agent.sub_agents or []:
        yield from _walk(child)


def _preload_tools(agent) -> list:
    return [tool for tool in agent.tools if isinstance(tool, PreloadMemoryTool)]


def _reset_startup_log() -> None:
    import tea_agent.memory as memory

    memory._status_logged = False


@pytest.mark.asyncio
async def test_generate_memories_sends_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_memory(monkeypatch)
    ctx = _FakeContext()
    result = await generate_memories_callback(ctx)  # type: ignore[arg-type]
    assert result is None
    assert ctx.calls == 1


@pytest.mark.asyncio
async def test_generate_memories_skips_when_service_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_memory(monkeypatch)
    ctx = _FakeContext(fail=ValueError("memory service is not available"))
    result = await generate_memories_callback(ctx)  # type: ignore[arg-type]
    assert result is None
    assert ctx.calls == 1


@pytest.mark.asyncio
async def test_generate_memories_does_not_raise_backend_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_memory(monkeypatch)
    ctx = _FakeContext(fail=RuntimeError("vertex unavailable"))
    result = await generate_memories_callback(ctx)  # type: ignore[arg-type]
    assert result is None
    assert ctx.calls == 1


@pytest.mark.asyncio
async def test_generate_memories_does_not_run_when_memory_bank_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_memory(monkeypatch)
    ctx = _FakeContext()
    result = await generate_memories_callback(ctx)  # type: ignore[arg-type]
    assert result is None
    assert ctx.calls == 0


def test_process_root_agent_follows_engine_id() -> None:
    """The agent imported at startup uses the same gate as ``build_root_agent``."""
    from tea_agent.agent import root_agent

    preloads = _preload_tools(root_agent)
    callbacks = root_agent.canonical_after_agent_callbacks
    if memory_bank_enabled():
        assert len(preloads) == 1
        assert generate_memories_callback in callbacks
    else:
        assert preloads == []
        assert generate_memories_callback not in callbacks


def test_blank_engine_id_disables_memory_bank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "   ")
    assert memory_bank_enabled() is False


def test_memory_service_uri_alone_does_not_enable_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_memory(monkeypatch)
    monkeypatch.setenv(
        "MEMORY_SERVICE_URI",
        "agentengine://projects/p/locations/eu/reasoningEngines/1",
    )
    assert memory_bank_enabled() is False
    agent = build_root_agent()
    assert _preload_tools(agent) == []
    assert generate_memories_callback not in agent.canonical_after_agent_callbacks


def test_root_agent_omits_memory_hooks_when_engine_id_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Built agent: no preload tool and no memory callback, so neither runs.

    A Cloud Trace of a later turn should show this as a smaller
    ``gen_ai.usage.input_tokens`` on the ``generate_content <model>`` span
    and no ``<PAST_CONVERSATIONS>`` block in ``gen_ai.input.messages``.
    ADK does not emit ``execute_tool preload_memory``; the tool only rewrites
    the model request.
    """
    _disable_memory(monkeypatch)
    agent = build_root_agent()
    assert agent.tools == _CATALOG_TOOLS
    assert agent.canonical_after_agent_callbacks == []
    assert agent.after_tool_callback is collect_turn_hits
    assert agent.after_model_callback is attach_next_steps_to_response
    for node in _walk(agent):
        assert _preload_tools(node) == []
        assert generate_memories_callback not in node.canonical_after_agent_callbacks
    assert [tool.__name__ for tool in agent.sub_agents[0].tools] == [
        "save_taste_profile"
    ]
    assert [tool.__name__ for tool in agent.sub_agents[1].tools] == [
        "resolve_tea",
        "get_tea_card",
    ]
    text = instruction_text()
    assert "___MEMORY_BANK_LINE___" not in text
    assert _NO_LONG_TERM_MEMORY in text
    assert _PAST_SESSION_SENTENCE not in text
    assert "{user:taste_profile?}" in text
    assert "{user:city?}" in text
    assert "{user:currency?}" in text


def test_root_agent_keeps_memory_bank_wiring_when_engine_id_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_memory(monkeypatch)
    agent = build_root_agent()
    assert agent.tools[:-1] == _CATALOG_TOOLS
    assert isinstance(agent.tools[-1], PreloadMemoryTool)
    assert agent.tools[-1].name == "preload_memory"
    assert agent.canonical_after_agent_callbacks == [generate_memories_callback]
    assert agent.after_tool_callback is collect_turn_hits
    assert agent.after_model_callback is attach_next_steps_to_response
    assert [sub.name for sub in agent.sub_agents] == [
        "onboarding_agent",
        "brewing_agent",
    ]
    for child in agent.sub_agents:
        assert _preload_tools(child) == []
        assert generate_memories_callback not in child.canonical_after_agent_callbacks
    text = instruction_text()
    assert _PAST_SESSION_SENTENCE in text
    assert _NO_LONG_TERM_MEMORY not in text
    assert "{user:taste_profile?}" in text
    assert "{user:city?}" in text
    assert "{user:currency?}" in text


def test_cloud_sql_without_engine_id_does_not_claim_past_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_memory(monkeypatch)
    monkeypatch.setenv("CLOUD_SQL_INSTANCE", "demo:europe-central2:tea-sessions")
    monkeypatch.setenv("SESSION_DB_PASSWORD", "secret")
    text = instruction_text()
    assert "постоянное хранилище сессий" in text
    assert _NO_LONG_TERM_MEMORY in text
    assert _PAST_SESSION_SENTENCE not in text


@pytest.mark.asyncio
async def test_off_agent_does_not_run_memory_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_memory(monkeypatch)
    agent = build_root_agent()
    ctx = _FakeContext()
    for node in _walk(agent):
        for callback in node.canonical_after_agent_callbacks:
            await callback(ctx)  # type: ignore[operator]
    assert ctx.calls == 0


@pytest.mark.asyncio
async def test_on_agent_callback_sends_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_memory(monkeypatch)
    agent = build_root_agent()
    ctx = _FakeContext()
    for callback in agent.canonical_after_agent_callbacks:
        await callback(ctx)  # type: ignore[operator]
    assert ctx.calls == 1


def test_startup_log_reuses_an_existing_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    import tea_agent.memory as memory

    _disable_memory(monkeypatch)
    _reset_startup_log()
    saved = list(memory.logger.handlers)
    memory.logger.handlers.clear()
    root = logging.getLogger()
    sink = logging.StreamHandler(io.StringIO())
    root.addHandler(sink)
    try:
        log_memory_bank_status()
        assert memory.logger.handlers == []
    finally:
        root.removeHandler(sink)
        memory.logger.handlers[:] = saved


def test_startup_log_reports_memory_off_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _disable_memory(monkeypatch)
    _reset_startup_log()
    with caplog.at_level(logging.INFO, logger="tea_agent.memory"):
        log_memory_bank_status()
        log_memory_bank_status()
    messages = [record.message for record in caplog.records if "Memory Bank:" in record.message]
    assert messages == [_OFF_LOG]


def test_startup_log_reports_memory_on_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _enable_memory(monkeypatch)
    _reset_startup_log()
    with caplog.at_level(logging.INFO, logger="tea_agent.memory"):
        build_root_agent()
        build_root_agent()
    messages = [record.message for record in caplog.records if "Memory Bank:" in record.message]
    assert messages == [_ON_LOG]


def test_memory_bank_config_has_tea_topic_and_few_shots() -> None:
    from tea_agent.app_utils.memory_config import TEA_TASTE_TOPIC, memory_bank_config

    custom = memory_bank_config.customization_configs[0]
    labels = [
        topic.custom_memory_topic.label
        for topic in custom.memory_topics
        if topic.custom_memory_topic is not None
    ]
    assert TEA_TASTE_TOPIC in labels
    assert custom.generate_memories_examples
    assert custom.consolidation_config.revisions_per_candidate_count == 10
    assert memory_bank_config.ttl_config.memory_revision_default_ttl.endswith("s")
