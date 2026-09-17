"""Resolve the ADK session backend for TEA-14.

Cloud Run disk is ephemeral. Production sessions go to Cloud SQL Postgres via
``DatabaseSessionService`` (ADK Cloud SQL codelab). Local/dev may use SQLite.

Agent Engine (Memory Bank) is a different store: ``GOOGLE_CLOUD_AGENT_ENGINE_ID``
does **not** imply Vertex sessions. Telegram ids ``tg_sess_<n>`` contain
underscores, which Agent Platform custom session ids reject (``[a-z0-9-]``).
"""

from __future__ import annotations

import os
from urllib.parse import quote

LOCAL_SQLITE_URI = "sqlite+aiosqlite:///./sessions.db"
DEFAULT_DB_USER = "tea_agent"
DEFAULT_DB_NAME = "tea_sessions"


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


def resolve_session_service_uri() -> str | None:
    """Return a DatabaseSessionService / factory URI, or None for in-memory.

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
