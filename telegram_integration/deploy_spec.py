"""Cloud Run two-service deploy spec (TEA-13).

Codelab pattern: tea-agent + telegram-integration. Telegram is deployed twice
because SERVICE_URL is unknown until the first revision exists
(placeholder https://google.com → real Cloud Run URL).
"""

from __future__ import annotations

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


def agent_env_vars(*, project: str, location: str = "global") -> str:
    return ",".join(
        [
            "GOOGLE_GENAI_USE_VERTEXAI=false",
            f"GOOGLE_CLOUD_PROJECT={project}",
            f"GOOGLE_CLOUD_LOCATION={location}",
        ]
    )


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


def agent_deploy_args(
    *,
    project: str,
    region: str = CLOUD_RUN_REGION,
    service: str = AGENT_SERVICE,
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
        f"--memory={AGENT_MEMORY}",
        f"--timeout={REQUEST_TIMEOUT}",
        "--set-secrets=GOOGLE_API_KEY=GOOGLE_API_KEY:latest",
        f"--set-env-vars={agent_env_vars(project=project)}",
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
