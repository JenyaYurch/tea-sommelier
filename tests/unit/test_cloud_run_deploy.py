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
    agent_restart_args,
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
    assert "--add-cloudsql-instances" not in joined
    assert "SESSION_DB_PASSWORD" not in joined


def test_agent_deploy_passes_memory_bank_engine_when_set() -> None:
    args = agent_deploy_args(
        project="demo-proj",
        agent_engine_id="engine-123",
        agent_engine_location="eu",
    )
    env = next(item for item in args if item.startswith("--set-env-vars="))
    assert "GOOGLE_CLOUD_AGENT_ENGINE_ID=engine-123" in env
    assert "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION=eu" in env


def test_agent_deploy_adds_cloud_sql_socket_without_db_password() -> None:
    args = agent_deploy_args(
        project="demo-proj",
        cloud_sql_instance="demo-proj:europe-central2:tea-sessions",
        session_db_user="tea_agent",
        session_db_name="tea_sessions",
    )
    joined = " ".join(args)
    assert "--add-cloudsql-instances=demo-proj:europe-central2:tea-sessions" in args
    env = next(item for item in args if item.startswith("--set-env-vars="))
    secrets = next(item for item in args if item.startswith("--set-secrets="))
    assert "CLOUD_SQL_INSTANCE=demo-proj:europe-central2:tea-sessions" in env
    assert "SESSION_DB_USER=tea_agent" in env
    assert "SESSION_DB_NAME=tea_sessions" in env
    assert "SESSION_DB_PASSWORD=SESSION_DB_PASSWORD:latest" in secrets
    assert "postgresql+" not in joined
    assert "s3cret" not in joined
    assert "@/" not in joined


def test_agent_deploy_expands_short_cloud_sql_instance_name() -> None:
    args = agent_deploy_args(
        project="demo-proj",
        cloud_sql_instance="tea-sessions",
        session_db_user="tea_agent",
        session_db_name="tea_sessions",
    )
    joined = " ".join(args)
    assert "--add-cloudsql-instances=demo-proj:europe-central2:tea-sessions" in args
    env = next(item for item in args if item.startswith("--set-env-vars="))
    assert "CLOUD_SQL_INSTANCE=demo-proj:europe-central2:tea-sessions" in env
    assert "CLOUD_SQL_INSTANCE=tea-sessions," not in env
    assert "SESSION_DB_PASSWORD=SESSION_DB_PASSWORD:latest" in joined


def test_agent_restart_args_force_new_revision_without_secrets() -> None:
    args = agent_restart_args(
        project="demo-proj",
        probe="1700000000",
    )
    joined = " ".join(args)
    assert AGENT_SERVICE in args
    assert "--update-env-vars=TEA14_SESSION_PROBE=1700000000" in args
    assert "PASSWORD" not in joined
    assert "postgresql+" not in joined


def test_agent_dockerfile_copies_catalog_data() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY ./data ./data" in text
    assert "COPY ./telegram_integration ./telegram_integration" in text


def test_dockerignore_keeps_env_out_of_image() -> None:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert ".venv" in text


def _load_deploy_module():
    import importlib.util

    path = ROOT / "scripts" / "deploy_cloud_run.py"
    spec = importlib.util.spec_from_file_location("deploy_cloud_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_execute_refuses_cloud_run_without_session_backend(monkeypatch) -> None:
    mod = _load_deploy_module()
    monkeypatch.setattr(mod, "_agent_engine_env", lambda: (None, None))
    monkeypatch.setattr(mod, "_cloud_sql_env", lambda: (None, "tea_agent", "tea_sessions"))
    try:
        mod._require_session_backend()
        raised = False
    except SystemExit as err:
        raised = True
        assert err.code == 2
    assert raised


def test_execute_allows_cloud_sql_or_agent_engine(monkeypatch) -> None:
    mod = _load_deploy_module()
    monkeypatch.setattr(mod, "_agent_engine_env", lambda: (None, None))
    monkeypatch.setattr(
        mod, "_cloud_sql_env", lambda: ("proj:europe-central2:tea-sessions", "tea_agent", "tea_sessions")
    )
    mod._require_session_backend()
    monkeypatch.setattr(mod, "_cloud_sql_env", lambda: (None, "tea_agent", "tea_sessions"))
    monkeypatch.setattr(mod, "_agent_engine_env", lambda: ("engine-123", "eu"))
    mod._require_session_backend()


def test_dry_run_plan_includes_write_restart_check(capsys, monkeypatch) -> None:
    mod = _load_deploy_module()
    monkeypatch.setattr(mod, "_agent_engine_env", lambda: (None, None))
    monkeypatch.setattr(
        mod, "_cloud_sql_env", lambda: ("tea-sessions", "tea_agent", "tea_sessions")
    )
    mod._print_plan("demo-proj", "europe-central2")
    out = capsys.readouterr().out
    assert "demo-proj:europe-central2:tea-sessions" in out
    assert "--write" in out
    assert "TEA14_SESSION_PROBE" in out
    assert "--check" in out


def test_verify_after_deploy_writes_restarts_and_checks(monkeypatch) -> None:
    mod = _load_deploy_module()
    calls: list[object] = []
    monkeypatch.setattr(
        mod, "_verify_cli", lambda url, *flags: calls.append((url, flags))
    )
    monkeypatch.setattr(
        mod, "_run_step", lambda label, args: calls.append((label, args))
    )
    monkeypatch.setattr(mod.time, "time", lambda: 1700000000)
    mod._verify_after_deploy(
        "demo-proj", "europe-central2", "https://tea-agent.example"
    )
    assert calls[0] == ("https://tea-agent.example", ("--write",))
    assert calls[1][0] == "restart tea-agent (new revision)"
    assert "--update-env-vars=TEA14_SESSION_PROBE=1700000000" in calls[1][1]
    assert calls[2] == ("https://tea-agent.example", ("--check",))
