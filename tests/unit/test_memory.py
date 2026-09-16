"""Memory Bank callback and config (TEA-9)."""

from __future__ import annotations

import pytest

from tea_agent.memory import generate_memories_callback


class _FakeContext:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls = 0
        self._fail = fail

    async def add_session_to_memory(self) -> None:
        self.calls += 1
        if self._fail is not None:
            raise self._fail


@pytest.mark.asyncio
async def test_generate_memories_sends_session() -> None:
    ctx = _FakeContext()
    result = await generate_memories_callback(ctx)  # type: ignore[arg-type]
    assert result is None
    assert ctx.calls == 1


@pytest.mark.asyncio
async def test_generate_memories_skips_when_service_missing() -> None:
    ctx = _FakeContext(fail=ValueError("memory service is not available"))
    result = await generate_memories_callback(ctx)  # type: ignore[arg-type]
    assert result is None
    assert ctx.calls == 1


@pytest.mark.asyncio
async def test_generate_memories_does_not_raise_backend_errors() -> None:
    ctx = _FakeContext(fail=RuntimeError("vertex unavailable"))
    result = await generate_memories_callback(ctx)  # type: ignore[arg-type]
    assert result is None
    assert ctx.calls == 1


def test_root_agent_has_preload_memory_and_callback() -> None:
    from google.adk.tools.preload_memory_tool import PreloadMemoryTool

    from tea_agent.agent import root_agent
    from tea_agent.memory import generate_memories_callback as callback

    assert any(isinstance(tool, PreloadMemoryTool) for tool in root_agent.tools)
    cb = root_agent.after_agent_callback
    if isinstance(cb, (list, tuple)):
        assert callback in cb
    else:
        assert cb is callback


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
