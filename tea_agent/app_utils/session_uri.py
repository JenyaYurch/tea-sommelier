"""Resolve the ADK session backend for TEA-14.

Cloud Run disk is ephemeral. Production sessions go to Cloud SQL Postgres via
``DatabaseSessionService`` (ADK Cloud SQL codelab) or to Agent Engine sessions
when ``GOOGLE_CLOUD_AGENT_ENGINE_ID`` is set. Local/dev may use SQLite.

Telegram ids are hyphenated (``tg-{id}`` / ``tg-sess-{id}``) so they match
Agent Platform custom session ids (``[a-z0-9-]``, start with a letter).
Cloud Run (``K_SERVICE``) refuses in-memory so a restart cannot silently drop
taste profiles.
"""

from __future__ import annotations

import os
from urllib.parse import quote, urlparse

LOCAL_SQLITE_URI = "sqlite+aiosqlite:///./sessions.db"
DEFAULT_DB_USER = "tea_agent"
DEFAULT_DB_NAME = "tea_sessions"
CLOUD_RUN_SERVICE_ENV = "K_SERVICE"


def postgres_unix_uri(
    *,
    user: str,
    password: str,
    database: str,
    instance_connection_name: str,
) -> str:
    """Unix-socket URI for Cloud Run ``--add-cloudsql-instances``.

    ``postgresql+asyncpg://user:pass@/db?host=/cloudsql/PROJECT:REGION:INSTANCE``
    """
    user_q = quote(user, safe="")
    password_q = quote(password, safe="")
    db_q = quote(database, safe="")
    host = instance_connection_name.strip().lstrip("/")
    if not host.startswith("cloudsql/"):
        host = f"/cloudsql/{host}"
    elif not host.startswith("/"):
        host = f"/{host}"
    return f"postgresql+asyncpg://{user_q}:{password_q}@/{db_q}?host={host}"


def cloud_sql_instance_from_env() -> str:
    return (os.environ.get("CLOUD_SQL_INSTANCE") or "").strip()


def agent_engine_id_from_env() -> str:
    return (os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID") or "").strip()


def is_ephemeral_session_uri(uri: str) -> bool:
    """True if this URI cannot survive Cloud Run instance replacement.

    sqlite/file live on container disk, which Cloud Run throws away. Postgres
    unix-socket URIs and ``agentengine://`` are durable.
    """
    scheme = (urlparse(uri).scheme or "").lower()
    if scheme.startswith("postgresql"):
        return False
    if scheme in {"agentengine", "agent-engine"}:
        return False
    return True


def missing_persistent_backend_error() -> RuntimeError:
    return RuntimeError(
        "Cloud Run requires a persistent ADK session backend so Telegram taste "
        "profiles survive restarts. Set CLOUD_SQL_INSTANCE + SESSION_DB_PASSWORD "
        "(Secret Manager) or GOOGLE_CLOUD_AGENT_ENGINE_ID. "
        "SESSION_SERVICE_URI must be postgresql+asyncpg:// or agentengine://; "
        "sqlite is ephemeral on Cloud Run."
    )


def apply_local_sqlite_default() -> str:
    """Persist local Telegram polling to sqlite unless a DB URI/Cloud SQL is set.

    Does not override an explicit ``SESSION_SERVICE_URI`` or Cloud SQL instance.
    Agent Engine Memory Bank id is not a local session store: polling still uses
    sqlite so profiles survive process restarts without Vertex credentials.
    """
    if uri := resolve_session_service_uri():
        return uri
    os.environ["SESSION_SERVICE_URI"] = LOCAL_SQLITE_URI
    return LOCAL_SQLITE_URI


def resolve_session_service_uri() -> str | None:
    """Return a DatabaseSessionService / factory URI, or None for the next backend.

    Precedence:
    1. ``SESSION_SERVICE_URI`` (sqlite, postgres, or ``agentengine://``)
    2. ``CLOUD_SQL_INSTANCE`` + ``SESSION_DB_PASSWORD`` → Cloud SQL unix socket
    """
    if uri := (os.environ.get("SESSION_SERVICE_URI") or "").strip():
        return uri
    instance = cloud_sql_instance_from_env()
    if not instance:
        return None
    password = (os.environ.get("SESSION_DB_PASSWORD") or "").strip()
    if not password:
        raise RuntimeError(
            "SESSION_DB_PASSWORD is required when CLOUD_SQL_INSTANCE is set. "
            "Inject it from Secret Manager; do not put it in git."
        )
    user = (os.environ.get("SESSION_DB_USER") or DEFAULT_DB_USER).strip()
    database = (os.environ.get("SESSION_DB_NAME") or DEFAULT_DB_NAME).strip()
    return postgres_unix_uri(
        user=user,
        password=password,
        database=database,
        instance_connection_name=instance,
    )
