"""Cloud Run two-service deploy spec (TEA-13).

Codelab pattern: tea-agent + telegram-integration. Telegram is deployed twice
because SERVICE_URL is unknown until the first revision exists
(placeholder https://google.com → real Cloud Run URL).
"""

from __future__ import annotations

from tea_agent.app_utils.session_uri import normalize_cloud_sql_instance

DEFAULT_PROJECT = "gen-lang-client-0393777014"
CLOUD_RUN_REGION = "europe-central2"
AGENT_SERVICE = "tea-agent"
TELEGRAM_SERVICE = "telegram-integration"
ADK_APP_NAME = "tea_agent"
PLACEHOLDER_SERVICE_URL = "https://google.com"
TELEGRAM_COMMAND = "uv"
TELEGRAM_ARGS = "run,python,-m,telegram_integration"
AGENT_MEMORY = "1Gi"
TELEGRAM_MEMORY = "512Mi"
REQUEST_TIMEOUT = "300"
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
    parts = [
        "GOOGLE_GENAI_USE_VERTEXAI=false",
        f"GOOGLE_CLOUD_PROJECT={project}",
        f"GOOGLE_CLOUD_LOCATION={location}",
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


def agent_secret_bindings(*, cloud_sql_instance: str | None = None) -> str:
    secrets = ["GOOGLE_API_KEY=GOOGLE_API_KEY:latest"]
    if (cloud_sql_instance or "").strip():
        secrets.append("SESSION_DB_PASSWORD=SESSION_DB_PASSWORD:latest")
    return ",".join(secrets)


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
    allow_unauthenticated: bool = True,
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
    ]
    if allow_unauthenticated:
        args.append("--allow-unauthenticated")
    else:
        args.append("--no-allow-unauthenticated")
    args.extend(
        [
            "--port=8080",
            "--execution-environment=gen2",
            f"--memory={AGENT_MEMORY}",
            f"--timeout={REQUEST_TIMEOUT}",
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
    )
    if instance:
        args.append(f"--set-cloudsql-instances={instance}")
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
        "--set-secrets=TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest",
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
