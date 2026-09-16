"""Memory service selection (TEA-9)."""

from __future__ import annotations

from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

from tea_agent.app_utils import services


def _reset_memory_cache() -> None:
    services.get_memory_service.cache_clear()
    services.get_session_service.cache_clear()


def test_default_memory_service_is_in_memory(monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_SERVICE_URI", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)
    _reset_memory_cache()
    try:
        service = services.get_memory_service()
        assert isinstance(service, InMemoryMemoryService)
        assert services.get_memory_service() is service
    finally:
        _reset_memory_cache()


def test_memory_service_uses_agent_engine_id(monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_SERVICE_URI", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "engine-123")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-proj")
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", "eu")
    captured: dict[str, str | None] = {}

    class FakeMemory:
        def __init__(self, *, project, location, agent_engine_id) -> None:
            captured["project"] = project
            captured["location"] = location
            captured["agent_engine_id"] = agent_engine_id

    monkeypatch.setattr(
        "google.adk.memory.vertex_ai_memory_bank_service.VertexAiMemoryBankService",
        FakeMemory,
    )
    _reset_memory_cache()
    try:
        service = services.get_memory_service()
        assert isinstance(service, FakeMemory)
        assert captured == {
            "project": "demo-proj",
            "location": "eu",
            "agent_engine_id": "engine-123",
        }
    finally:
        _reset_memory_cache()


def test_memory_service_uri_is_preferred(monkeypatch) -> None:
    sentinel = object()

    def fake_create(*, base_dir, memory_service_uri):
        assert memory_service_uri == "memory://"
        return sentinel

    monkeypatch.setenv("MEMORY_SERVICE_URI", "memory://")
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "should-not-use")
    monkeypatch.setattr(
        services, "create_memory_service_from_options", fake_create
    )
    _reset_memory_cache()
    try:
        assert services.get_memory_service() is sentinel
    finally:
        _reset_memory_cache()
