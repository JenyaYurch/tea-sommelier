"""Cloud Run two-service deploy spec (TEA-13).

Codelab pattern: tea-agent + telegram-integration. Telegram is deployed twice
because SERVICE_URL is unknown until the first revision exists
(placeholder https://google.com → real Cloud Run URL).
"""

from __future__ import annotations

import os

from tea_agent.app_utils.agent_auth import AUTH_SECRET_ENV
from tea_agent.app_utils.session_uri import (
    EPHEMERAL_SESSIONS_ENV,
    normalize_cloud_sql_instance,
)

DEFAULT_PROJECT = "gen-lang-client-0393777014"
CLOUD_RUN_REGION = "europe-central2"
AGENT_SERVICE = "tea-agent"
TELEGRAM_SERVICE = "telegram-integration"
ADK_APP_NAME = "tea_agent"
DEFAULT_AGENT_MODEL = "gemini-3.1-flash-lite"
PLACEHOLDER_SERVICE_URL = "https://google.com"
TELEGRAM_COMMAND = "uv"
TELEGRAM_ARGS = "run,python,-m,telegram_integration"
AGENT_MEMORY = "1Gi"
TELEGRAM_MEMORY = "512Mi"
REQUEST_TIMEOUT = "300"
# One instance while sessions are in-memory (TEA-35). Also caps Gemini fan-out.
AGENT_MAX_INSTANCES = "1"
AGENT_MIN_INSTANCES_DEFAULT = "1"
# Cloud Run's default concurrency is 80. That is too many parallel /run calls
# for one 1Gi instance waiting on Gemini. 8 covers a few beta testers.
AGENT_CONCURRENCY = "8"
DEFAULT_SESSION_DB_USER = "postgres"
DEFAULT_SESSION_DB_NAME = "tea_sessions"
CLOUD_SQL_INSTANCE_NAME = "tea-sessions"


def normalize_service_url(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if value and not value.startswith("http"):
        value = f"https://{value}"
    return value


def webhook_url(service_url: str, token: str) -> str:
    """Telegram webhook target: <SERVICE_URL>/<TELEGRAM_BOT_TOKEN>."""
    return f"{normalize_service_url(service_url)}/{token}"


def is_webhook_mode(*, port: str | None, service_url: str | None) -> bool:
    return bool((port or "").strip() and (service_url or "").strip())


def agent_env_vars(
    *,
    project: str,
    location: str = "global",
    region: str = CLOUD_RUN_REGION,
    agent_engine_id: str | None = None,
    agent_engine_location: str | None = None,
    cloud_sql_instance: str | None = None,
    session_db_user: str | None = None,
    session_db_name: str | None = None,
) -> str:
    model = (os.environ.get("TEA_AGENT_MODEL") or DEFAULT_AGENT_MODEL).strip()
    model = model or DEFAULT_AGENT_MODEL
    parts = [
        "GOOGLE_GENAI_USE_VERTEXAI=false",
        f"GOOGLE_CLOUD_PROJECT={project}",
        f"GOOGLE_CLOUD_LOCATION={location}",
        f"TEA_AGENT_MODEL={model}",
    ]
    engine_id = (agent_engine_id or "").strip()
    if engine_id:
        parts.append(f"GOOGLE_CLOUD_AGENT_ENGINE_ID={engine_id}")
    engine_location = (agent_engine_location or "").strip()
    if engine_location:
        parts.append(f"GOOGLE_CLOUD_AGENT_ENGINE_LOCATION={engine_location}")
    instance = normalize_cloud_sql_instance(
        cloud_sql_instance or "", project=project, region=region
    )
    if instance:
        parts.append(f"CLOUD_SQL_INSTANCE={instance}")
        parts.append(
            f"SESSION_DB_USER={(session_db_user or DEFAULT_SESSION_DB_USER).strip()}"
        )
        parts.append(
            f"SESSION_DB_NAME={(session_db_name or DEFAULT_SESSION_DB_NAME).strip()}"
        )
    elif not engine_id:
        parts.append(f"{EPHEMERAL_SESSIONS_ENV}=true")
    # Explicit so prod does not depend only on K_SERVICE detection.
    parts.append("TEA_AGENT_DEV_UI=false")
    return ",".join(parts)


def telegram_env_vars(
    *,
    adk_server_url: str,
    service_url: str,
    app_name: str = ADK_APP_NAME,
) -> str:
    return ",".join(
        [
            f"ADK_SERVER_URL={normalize_service_url(adk_server_url)}",
            f"ADK_APP_NAME={app_name}",
            f"SERVICE_URL={normalize_service_url(service_url)}",
        ]
    )


def auth_secret_binding() -> str:
    return f"{AUTH_SECRET_ENV}={AUTH_SECRET_ENV}:latest"


def agent_min_instances() -> str:
    """Cloud Run ``--min-instances``. Default 1 keeps memory across idle time.

    ``TEA_AGENT_MIN_INSTANCES=0`` turns the floor off. Values above
    ``AGENT_MAX_INSTANCES`` are rejected: max stays 1 while sessions are in memory.
    """
    raw = os.environ.get("TEA_AGENT_MIN_INSTANCES")
    if raw is None or not raw.strip():
        return AGENT_MIN_INSTANCES_DEFAULT
    value = raw.strip()
    if not value.isdigit():
        raise ValueError(
            f"TEA_AGENT_MIN_INSTANCES must be a non-negative integer, got {value!r}"
        )
    number = int(value)
    if number > int(AGENT_MAX_INSTANCES):
        raise ValueError(
            "TEA_AGENT_MIN_INSTANCES cannot exceed tea-agent max instances "
            f"({AGENT_MAX_INSTANCES}) while sessions are in-memory"
        )
    return str(number)


def agent_scaling_args() -> list[str]:
    """Pin tea-agent to one warm instance with a small concurrency cap.

    ``--cpu-throttling`` keeps request-based billing: idle min-instances are
    charged at the lower idle rate, not as a full-time vCPU.
    """
    return [
        f"--max-instances={AGENT_MAX_INSTANCES}",
        f"--min-instances={agent_min_instances()}",
        f"--concurrency={AGENT_CONCURRENCY}",
        "--cpu-throttling",
    ]


def agent_secret_bindings(*, cloud_sql_instance: str | None = None) -> str:
    secrets = [
        "GOOGLE_API_KEY=GOOGLE_API_KEY:latest",
        auth_secret_binding(),
    ]
    if (cloud_sql_instance or "").strip():
        secrets.append("SESSION_DB_PASSWORD=SESSION_DB_PASSWORD:latest")
    return ",".join(secrets)


def telegram_secret_bindings() -> str:
    return ",".join(
        [
            "TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest",
            auth_secret_binding(),
        ]
    )


def agent_deploy_args(
    *,
    project: str,
    region: str = CLOUD_RUN_REGION,
    service: str = AGENT_SERVICE,
    agent_engine_id: str | None = None,
    agent_engine_location: str | None = None,
    cloud_sql_instance: str | None = None,
    session_db_user: str | None = None,
    session_db_name: str | None = None,
) -> list[str]:
    instance = normalize_cloud_sql_instance(
        cloud_sql_instance or "", project=project, region=region
    )
    args = [
        "run",
        "deploy",
        service,
        "--source",
        ".",
        f"--project={project}",
        f"--region={region}",
        "--allow-unauthenticated",
        "--port=8080",
        "--execution-environment=gen2",
        f"--memory={AGENT_MEMORY}",
        f"--timeout={REQUEST_TIMEOUT}",
        *agent_scaling_args(),
        "--set-secrets=" + agent_secret_bindings(cloud_sql_instance=instance),
        "--set-env-vars="
        + agent_env_vars(
            project=project,
            region=region,
            agent_engine_id=agent_engine_id,
            agent_engine_location=agent_engine_location,
            cloud_sql_instance=instance,
            session_db_user=session_db_user,
            session_db_name=session_db_name,
        ),
    ]
    if instance:
        args.append(f"--set-cloudsql-instances={instance}")
    else:
        args.append("--clear-cloudsql-instances")
    args.append("--quiet")
    return args


def agent_restart_args(
    *,
    project: str,
    region: str = CLOUD_RUN_REGION,
    service: str = AGENT_SERVICE,
    probe: str,
) -> list[str]:
    """Force a new tea-agent revision so TEA-14 can check Cloud SQL after restart."""
    return [
        "run",
        "services",
        "update",
        service,
        f"--project={project}",
        f"--region={region}",
        f"--update-env-vars=TEA14_SESSION_PROBE={probe}",
        "--quiet",
    ]


def telegram_deploy_args(
    *,
    project: str,
    adk_server_url: str,
    service_url: str,
    region: str = CLOUD_RUN_REGION,
    service: str = TELEGRAM_SERVICE,
    app_name: str = ADK_APP_NAME,
) -> list[str]:
    return [
        "run",
        "deploy",
        service,
        "--source",
        ".",
        f"--project={project}",
        f"--region={region}",
        "--allow-unauthenticated",
        "--port=8080",
        f"--memory={TELEGRAM_MEMORY}",
        f"--timeout={REQUEST_TIMEOUT}",
        f"--command={TELEGRAM_COMMAND}",
        f"--args={TELEGRAM_ARGS}",
        "--set-secrets=" + telegram_secret_bindings(),
        "--set-env-vars="
        + telegram_env_vars(
            adk_server_url=adk_server_url,
            service_url=service_url,
            app_name=app_name,
        ),
        "--quiet",
    ]


def telegram_update_env_args(
    *,
    project: str,
    adk_server_url: str,
    service_url: str,
    region: str = CLOUD_RUN_REGION,
    service: str = TELEGRAM_SERVICE,
    app_name: str = ADK_APP_NAME,
) -> list[str]:
    return [
        "run",
        "services",
        "update",
        service,
        f"--project={project}",
        f"--region={region}",
        "--set-env-vars="
        + telegram_env_vars(
            adk_server_url=adk_server_url,
            service_url=service_url,
            app_name=app_name,
        ),
        "--quiet",
    ]
