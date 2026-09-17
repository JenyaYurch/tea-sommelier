"""TEA-14 DatabaseSessionService survives a new process-equivalent instance."""

from __future__ import annotations

import pytest
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.sessions.in_memory_session_service import InMemorySessionService

from tea_agent.app_utils import services


def _reset() -> None:
    services.get_session_service.cache_clear()


def test_default_session_service_is_in_memory(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.delenv("CLOUD_SQL_INSTANCE", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "engine-should-not-switch-sessions")
    _reset()
    try:
        service = services.get_session_service()
        assert isinstance(service, InMemorySessionService)
        assert services.get_session_service() is service
    finally:
        _reset()


def test_session_service_uses_uri_factory(monkeypatch) -> None:
    sentinel = object()

    def fake_create(*, base_dir, session_service_uri):
        assert session_service_uri == "sqlite+aiosqlite:///./sessions.db"
        return sentinel

    monkeypatch.setenv("SESSION_SERVICE_URI", "sqlite+aiosqlite:///./sessions.db")
    monkeypatch.setattr(services, "create_session_service_from_options", fake_create)
    _reset()
    try:
        assert services.get_session_service() is sentinel
    finally:
        _reset()


@pytest.mark.asyncio
async def test_sqlite_session_survives_new_service_instance(tmp_path) -> None:
    db = tmp_path / "sessions.db"
    uri = f"sqlite+aiosqlite:///{db.resolve().as_posix()}"
    first = DatabaseSessionService(db_url=uri)
    created = await first.create_session(
        app_name="tea_agent",
        user_id="tg_4242",
        session_id="tg_sess_4242",
        state={
            "experience": "новичок",
            "user:experience": "новичок",
            "user:profile_complete": True,
        },
    )
    assert created.id == "tg_sess_4242"

    restarted = DatabaseSessionService(db_url=uri)
    loaded = await restarted.get_session(
        app_name="tea_agent",
        user_id="tg_4242",
        session_id="tg_sess_4242",
    )
    assert loaded is not None
    assert loaded.id == "tg_sess_4242"
    assert loaded.state.get("user:experience") == "новичок"
    assert loaded.state.get("experience") == "новичок"
    assert loaded.state.get("user:profile_complete") is True
