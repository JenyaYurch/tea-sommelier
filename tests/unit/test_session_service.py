"""TEA-14 DatabaseSessionService survives a new process-equivalent instance."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.state import State

from tea_agent.app_utils import services
from tea_agent.app_utils.session_uri import CLOUD_RUN_SERVICE_ENV, LOCAL_SQLITE_URI
from tea_agent.profile_tools import save_taste_profile
from telegram_integration.keyboard import telegram_session_id, telegram_user_key


def _reset() -> None:
    services.get_session_service.cache_clear()


def _clear_backends(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.delenv("CLOUD_SQL_INSTANCE", raising=False)
    monkeypatch.delenv("SESSION_DB_PASSWORD", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)
    monkeypatch.delenv(CLOUD_RUN_SERVICE_ENV, raising=False)


def test_default_session_service_is_in_memory(monkeypatch) -> None:
    _clear_backends(monkeypatch)
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


def test_session_service_uses_agent_engine(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    class FakeSessions:
        def __init__(self, *, project, location, agent_engine_id) -> None:
            captured["project"] = project
            captured["location"] = location
            captured["agent_engine_id"] = agent_engine_id

    _clear_backends(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "engine-123")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-proj")
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", "eu")
    monkeypatch.setattr(
        "google.adk.sessions.vertex_ai_session_service.VertexAiSessionService",
        FakeSessions,
    )
    _reset()
    try:
        service = services.get_session_service()
        assert isinstance(service, FakeSessions)
        assert captured == {
            "project": "demo-proj",
            "location": "eu",
            "agent_engine_id": "engine-123",
        }
    finally:
        _reset()


def test_cloud_run_without_backend_raises(monkeypatch) -> None:
    _clear_backends(monkeypatch)
    monkeypatch.setenv(CLOUD_RUN_SERVICE_ENV, "tea-agent")
    _reset()
    try:
        with pytest.raises(RuntimeError, match="persistent ADK session backend"):
            services.get_session_service()
    finally:
        _reset()


def test_cloud_run_rejects_sqlite_uri(monkeypatch) -> None:
    _clear_backends(monkeypatch)
    monkeypatch.setenv(CLOUD_RUN_SERVICE_ENV, "tea-agent")
    monkeypatch.setenv("SESSION_SERVICE_URI", LOCAL_SQLITE_URI)
    _reset()
    try:
        with pytest.raises(RuntimeError, match="sqlite is ephemeral"):
            services.get_session_service()
    finally:
        _reset()


def test_cloud_run_accepts_postgres_uri(monkeypatch) -> None:
    sentinel = object()
    uri = "postgresql+asyncpg://tea_agent:x@/tea_sessions?host=/cloudsql/p:r:i"

    def fake_create(*, base_dir, session_service_uri):
        assert session_service_uri == uri
        return sentinel

    _clear_backends(monkeypatch)
    monkeypatch.setenv(CLOUD_RUN_SERVICE_ENV, "tea-agent")
    monkeypatch.setenv("SESSION_SERVICE_URI", uri)
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
    user_id = telegram_user_key(4242)
    session_id = telegram_session_id(4242)
    first = DatabaseSessionService(db_url=uri)
    created = await first.create_session(
        app_name="tea_agent",
        user_id=user_id,
        session_id=session_id,
        state={
            "experience": "новичок",
            "user:experience": "новичок",
            "user:profile_complete": True,
        },
    )
    assert created.id == session_id
    assert user_id == "tg-4242"
    assert session_id == "tg-sess-4242"

    restarted = DatabaseSessionService(db_url=uri)
    loaded = await restarted.get_session(
        app_name="tea_agent",
        user_id=user_id,
        session_id=session_id,
    )
    assert loaded is not None
    assert loaded.id == session_id
    assert loaded.state.get("user:experience") == "новичок"
    assert loaded.state.get("experience") == "новичок"
    assert loaded.state.get("user:profile_complete") is True


@pytest.mark.asyncio
async def test_save_taste_profile_event_survives_new_service_instance(tmp_path) -> None:
    """Telegram writes the profile via tool_context.state → event state_delta."""
    db = tmp_path / "sessions.db"
    uri = f"sqlite+aiosqlite:///{db.resolve().as_posix()}"
    user_id = telegram_user_key(5150)
    session_id = telegram_session_id(5150)
    first = DatabaseSessionService(db_url=uri)
    session = await first.create_session(
        app_name="tea_agent",
        user_id=user_id,
        session_id=session_id,
    )
    delta: dict = {}
    save_taste_profile(
        experience="новичок",
        taste_profile="мягкий без горечи",
        budget="",
        caffeine_pref="низкий",
        vessel="кружка",
        liked_teas="Лунцзин",
        tool_context=SimpleNamespace(state=State(dict(session.state), delta)),  # type: ignore[arg-type]
    )
    await first.append_event(
        session,
        Event(
            invocation_id="inv-profile",
            author="tea_sommelier",
            actions=EventActions(state_delta=delta),
        ),
    )

    restarted = DatabaseSessionService(db_url=uri)
    loaded = await restarted.get_session(
        app_name="tea_agent",
        user_id=user_id,
        session_id=session_id,
    )
    assert loaded is not None
    assert loaded.id == session_id
    assert loaded.state.get("user:experience") == "новичок"
    assert loaded.state.get("experience") == "новичок"
    assert loaded.state.get("user:taste_profile") == "мягкий без горечи"
    assert loaded.state.get("user:liked_teas") == ["Лунцзин"]
    assert loaded.state.get("user:profile_complete") is True


@pytest.mark.asyncio
async def test_one_telegram_user_maps_to_one_session(tmp_path) -> None:
    db = tmp_path / "sessions.db"
    uri = f"sqlite+aiosqlite:///{db.resolve().as_posix()}"
    service = DatabaseSessionService(db_url=uri)
    alice = await service.create_session(
        app_name="tea_agent",
        user_id=telegram_user_key(1),
        session_id=telegram_session_id(1),
    )
    bob = await service.create_session(
        app_name="tea_agent",
        user_id=telegram_user_key(2),
        session_id=telegram_session_id(2),
    )
    assert alice.id == "tg-sess-1"
    assert bob.id == "tg-sess-2"
    assert alice.user_id != bob.user_id
    again = await service.get_session(
        app_name="tea_agent",
        user_id=telegram_user_key(1),
        session_id=telegram_session_id(1),
    )
    assert again is not None
    assert again.id == alice.id


_CLOUD_SQL_LIKE_SOCKET = Path("/tmp/cloudsql/demo-proj:europe-central2:tea-sessions")
_CLOUD_SQL_LIKE_URI = (
    "postgresql+asyncpg://tea_agent:tea_test_pass@/tea_sessions"
    f"?host={_CLOUD_SQL_LIKE_SOCKET}"
)


def _postgres_uri_or_skip() -> str:
    socket = _CLOUD_SQL_LIKE_SOCKET / ".s.PGSQL.5432"
    if not socket.exists() and not _CLOUD_SQL_LIKE_SOCKET.exists():
        pytest.skip("Cloud SQL-like Postgres unix socket is not running")
    return _CLOUD_SQL_LIKE_URI


@pytest.mark.asyncio
async def test_postgres_unix_socket_session_survives_new_service_instance() -> None:
    """Same driver and unix-socket query form Cloud Run uses for Cloud SQL."""
    uri = _postgres_uri_or_skip()
    user_id = telegram_user_key(880014)
    session_id = telegram_session_id(880014)
    first = DatabaseSessionService(db_url=uri)
    try:
        await first.delete_session(
            app_name="tea_agent", user_id=user_id, session_id=session_id
        )
    except Exception:
        pass
    try:
        created = await first.create_session(
            app_name="tea_agent",
            user_id=user_id,
            session_id=session_id,
            state={
                "experience": "новичок",
                "user:experience": "новичок",
                "user:taste_profile": "мягкий без горечи",
                "user:profile_complete": True,
            },
        )
    except Exception as err:
        pytest.skip(f"postgres unavailable: {err}")
    assert created.id == session_id

    restarted = DatabaseSessionService(db_url=uri)
    loaded = await restarted.get_session(
        app_name="tea_agent",
        user_id=user_id,
        session_id=session_id,
    )
    assert loaded is not None
    assert loaded.id == session_id
    assert loaded.user_id == user_id
    assert loaded.state.get("user:experience") == "новичок"
    assert loaded.state.get("experience") == "новичок"
    assert loaded.state.get("user:taste_profile") == "мягкий без горечи"
    assert loaded.state.get("user:profile_complete") is True

    missing = await restarted.get_session(
        app_name="tea_agent",
        user_id=telegram_user_key(880015),
        session_id=telegram_session_id(880015),
    )
    assert missing is None
