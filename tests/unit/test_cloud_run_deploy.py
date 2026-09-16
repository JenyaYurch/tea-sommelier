"""Webhook vs polling mode and Cloud Run two-stage command shape (TEA-13)."""

from __future__ import annotations

from pathlib import Path

from telegram_integration.deploy_spec import (
    ADK_APP_NAME,
    AGENT_SERVICE,
    PLACEHOLDER_SERVICE_URL,
    TELEGRAM_ARGS,
    TELEGRAM_COMMAND,
    TELEGRAM_SERVICE,
    agent_deploy_args,
    is_webhook_mode,
    telegram_deploy_args,
    telegram_update_env_args,
    webhook_url,
)

ROOT = Path(__file__).resolve().parents[2]


def test_webhook_mode_needs_port_and_service_url() -> None:
    assert is_webhook_mode(port="8080", service_url="https://svc.run.app")
    assert not is_webhook_mode(port="8080", service_url="")
    assert not is_webhook_mode(port="", service_url="https://svc.run.app")
    assert not is_webhook_mode(port=None, service_url=None)


def test_webhook_url_hides_nothing_but_uses_token_path() -> None:
    url = webhook_url("svc.run.app", "bot-token-value")
    assert url == "https://svc.run.app/bot-token-value"


def test_two_stage_telegram_uses_placeholder_then_real_url() -> None:
    agent = "https://tea-agent-xyz.run.app"
    stage1 = telegram_deploy_args(
        project="demo-proj",
        adk_server_url=agent,
        service_url=PLACEHOLDER_SERVICE_URL,
    )
    env1 = next(item for item in stage1 if item.startswith("--set-env-vars="))
    assert "SERVICE_URL=https://google.com" in env1
    assert f"ADK_SERVER_URL={agent}" in env1
    assert f"ADK_APP_NAME={ADK_APP_NAME}" in env1
    assert TELEGRAM_SERVICE in stage1
    assert f"--command={TELEGRAM_COMMAND}" in stage1
    assert f"--args={TELEGRAM_ARGS}" in stage1
    assert any("TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest" in item for item in stage1)

    real = "https://telegram-integration-xyz.run.app"
    stage2 = telegram_update_env_args(
        project="demo-proj",
        adk_server_url=agent,
        service_url=real,
    )
    env2 = next(item for item in stage2 if item.startswith("--set-env-vars="))
    assert f"SERVICE_URL={real}" in env2
    assert "google.com" not in env2


def test_agent_deploy_uses_secret_manager_not_plaintext_key() -> None:
    args = agent_deploy_args(project="demo-proj")
    joined = " ".join(args)
    assert AGENT_SERVICE in args
    assert "--source" in args
    assert "GOOGLE_API_KEY=GOOGLE_API_KEY:latest" in joined
    assert "AIza" not in joined
    assert "--allow-unauthenticated" in args
    assert "GOOGLE_CLOUD_AGENT_ENGINE_ID=" not in joined


def test_agent_deploy_passes_memory_bank_engine_when_set() -> None:
    args = agent_deploy_args(
        project="demo-proj",
        agent_engine_id="engine-123",
        agent_engine_location="eu",
    )
    env = next(item for item in args if item.startswith("--set-env-vars="))
    assert "GOOGLE_CLOUD_AGENT_ENGINE_ID=engine-123" in env
    assert "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION=eu" in env


def test_agent_dockerfile_copies_catalog_data() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY ./data ./data" in text
    assert "COPY ./telegram_integration ./telegram_integration" in text


def test_dockerignore_keeps_env_out_of_image() -> None:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert ".venv" in text
