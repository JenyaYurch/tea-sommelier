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
from telegram_integration.access import (
    DEFAULT_RATE_LIMIT_PER_DAY,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    ENV_ACCESS_MODE,
    ENV_ADMIN_USER_IDS,
    ENV_ALLOWED_USER_IDS,
    ENV_INVITE_CODE,
    ENV_RATE_LIMIT_PER_DAY,
    ENV_RATE_LIMIT_PER_MINUTE,
    parse_user_ids,
)

# TEA-36 deploy-shell flags. They are not read by the bot process.
# 1 / true mounts the named Secret Manager secret as that env var.
ENV_ALLOWLIST_SECRET = "TELEGRAM_ALLOWLIST_SECRET"
ENV_INVITE_CODE_SECRET = "TELEGRAM_INVITE_CODE_SECRET"
_ACCESS_MODES = frozenset(
    {"auto", "open", "closed", "enforce", "on", "off", "disabled"}
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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _rate_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw or not raw.isdigit():
        return default
    return int(raw)


def _beta_id_list(env_name: str) -> str | None:
    """Semicolon-joined ids. gcloud splits --set-env-vars on commas."""
    ids = sorted(parse_user_ids(os.environ.get(env_name, "")))
    if not ids:
        return None
    return ";".join(str(user_id) for user_id in ids)


def telegram_beta_env_parts() -> list[str]:
    """TEA-36 env appended to telegram-integration --set-env-vars.

    Cloud Run defaults to ``TELEGRAM_ACCESS_MODE=closed`` (fail closed).
    Tester ids and a plaintext invite code are forwarded only when the deploy
    shell set them, so a dry-run does not invent an allowlist. Id lists use
    semicolons because gcloud splits this string on commas.
    """
    mode = os.environ.get(ENV_ACCESS_MODE, "").strip().lower() or "closed"
    if mode not in _ACCESS_MODES:
        mode = "closed"
    parts = [
        f"{ENV_ACCESS_MODE}={mode}",
        f"{ENV_RATE_LIMIT_PER_MINUTE}="
        f"{_rate_env(ENV_RATE_LIMIT_PER_MINUTE, DEFAULT_RATE_LIMIT_PER_MINUTE)}",
        f"{ENV_RATE_LIMIT_PER_DAY}="
        f"{_rate_env(ENV_RATE_LIMIT_PER_DAY, DEFAULT_RATE_LIMIT_PER_DAY)}",
    ]
    if not _env_flag(ENV_ALLOWLIST_SECRET):
        allowed = _beta_id_list(ENV_ALLOWED_USER_IDS)
        if allowed:
            parts.append(f"{ENV_ALLOWED_USER_IDS}={allowed}")
    admins = _beta_id_list(ENV_ADMIN_USER_IDS)
    if admins:
        parts.append(f"{ENV_ADMIN_USER_IDS}={admins}")
    if not _env_flag(ENV_INVITE_CODE_SECRET):
        invite = os.environ.get(ENV_INVITE_CODE, "").strip()
        if invite:
            if any(char in invite for char in ", \t\r\n"):
                raise SystemExit(
                    "TELEGRAM_INVITE_CODE cannot contain commas or spaces in "
                    "--set-env-vars. Set TELEGRAM_INVITE_CODE_SECRET=1 and "
                    "mount the Secret Manager secret instead."
                )
            parts.append(f"{ENV_INVITE_CODE}={invite}")
    return parts


def telegram_secret_bindings() -> str:
    """Secret Manager env bindings for telegram-integration.

    Always mounted: ``TELEGRAM_BOT_TOKEN`` and ``TEA_AGENT_AUTH_SECRET``.
    TEA-36 opt-in flags in the deploy shell (not bot runtime config):

    * ``TELEGRAM_ALLOWLIST_SECRET=1`` also mounts ``TELEGRAM_ALLOWED_USER_IDS``
    * ``TELEGRAM_INVITE_CODE_SECRET=1`` also mounts ``TELEGRAM_INVITE_CODE``
    """
    bindings = [
        "TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest",
        auth_secret_binding(),
    ]
    if _env_flag(ENV_ALLOWLIST_SECRET):
        bindings.append("TELEGRAM_ALLOWED_USER_IDS=TELEGRAM_ALLOWED_USER_IDS:latest")
    if _env_flag(ENV_INVITE_CODE_SECRET):
        bindings.append("TELEGRAM_INVITE_CODE=TELEGRAM_INVITE_CODE:latest")
    return ",".join(bindings)


def telegram_env_vars(
    *,
    adk_server_url: str,
    service_url: str,
    app_name: str = ADK_APP_NAME,
) -> str:
    parts = [
        f"ADK_SERVER_URL={normalize_service_url(adk_server_url)}",
        f"ADK_APP_NAME={app_name}",
        f"SERVICE_URL={normalize_service_url(service_url)}",
    ]
    # TEA-36: same beta env on the placeholder deploy and the SERVICE_URL update.
    parts.extend(telegram_beta_env_parts())
    return ",".join(parts)


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
