"""TEA-14 session URI building (no secrets printed)."""

from __future__ import annotations

import os

import pytest

from tea_agent.app_utils.session_uri import (
    CLOUD_RUN_SERVICE_ENV,
    LOCAL_SQLITE_URI,
    apply_local_sqlite_default,
    missing_persistent_backend_error,
    postgres_unix_uri,
    resolve_session_service_uri,
)


def test_postgres_unix_uri_uses_cloudsql_socket_and_quotes_password() -> None:
    uri = postgres_unix_uri(
        user="tea_agent",
        password="p@ss/word:1",
        database="tea_sessions",
        instance_connection_name="demo-proj:europe-central2:tea-sessions",
    )
    assert uri.startswith("postgresql+asyncpg://tea_agent:")
    assert "@/tea_sessions?host=/cloudsql/demo-proj:europe-central2:tea-sessions" in uri
    assert "p@ss/word:1" not in uri
    assert "p%40ss%2Fword%3A1" in uri


def test_resolve_prefers_explicit_session_service_uri(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_SERVICE_URI", LOCAL_SQLITE_URI)
    monkeypatch.setenv("CLOUD_SQL_INSTANCE", "demo:europe-central2:tea-sessions")
    monkeypatch.setenv("SESSION_DB_PASSWORD", "secret")
    assert resolve_session_service_uri() == LOCAL_SQLITE_URI


def test_resolve_builds_cloud_sql_uri_from_parts(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.setenv("CLOUD_SQL_INSTANCE", "demo:europe-central2:tea-sessions")
    monkeypatch.setenv("SESSION_DB_USER", "tea_agent")
    monkeypatch.setenv("SESSION_DB_NAME", "tea_sessions")
    monkeypatch.setenv("SESSION_DB_PASSWORD", "p@ss/w")
    uri = resolve_session_service_uri()
    assert uri is not None
    assert "p@ss/w" not in uri
    assert "tea_agent" in uri
    assert "host=/cloudsql/demo:europe-central2:tea-sessions" in uri


def test_resolve_requires_password_when_instance_set(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.setenv("CLOUD_SQL_INSTANCE", "demo:europe-central2:tea-sessions")
    monkeypatch.delenv("SESSION_DB_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="SESSION_DB_PASSWORD"):
        resolve_session_service_uri()


def test_resolve_none_without_backend(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.delenv("CLOUD_SQL_INSTANCE", raising=False)
    monkeypatch.delenv("SESSION_DB_PASSWORD", raising=False)
    assert resolve_session_service_uri() is None


def test_apply_local_sqlite_default_sets_uri_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.delenv("CLOUD_SQL_INSTANCE", raising=False)
    monkeypatch.delenv("SESSION_DB_PASSWORD", raising=False)
    assert apply_local_sqlite_default() == LOCAL_SQLITE_URI
    assert os.environ["SESSION_SERVICE_URI"] == LOCAL_SQLITE_URI
    assert resolve_session_service_uri() == LOCAL_SQLITE_URI


def test_apply_local_sqlite_default_keeps_explicit_uri(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_SERVICE_URI", "sqlite+aiosqlite:///./other.db")
    assert apply_local_sqlite_default() == "sqlite+aiosqlite:///./other.db"


def test_apply_local_sqlite_default_keeps_cloud_sql(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.setenv("CLOUD_SQL_INSTANCE", "demo:europe-central2:tea-sessions")
    monkeypatch.setenv("SESSION_DB_PASSWORD", "p@ss/w")
    uri = apply_local_sqlite_default()
    assert uri is not None
    assert uri.startswith("postgresql+asyncpg://")
    assert os.environ.get("SESSION_SERVICE_URI") in (None, "")


def test_missing_persistent_backend_error_mentions_cloud_run() -> None:
    err = missing_persistent_backend_error()
    assert CLOUD_RUN_SERVICE_ENV == "K_SERVICE"
    assert "CLOUD_SQL_INSTANCE" in str(err)
    assert "GOOGLE_CLOUD_AGENT_ENGINE_ID" in str(err)
