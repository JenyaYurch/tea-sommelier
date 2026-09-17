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
    assert "--execution-environment=gen2" in args
    assert "GOOGLE_CLOUD_AGENT_ENGINE_ID=" not in joined
    assert "--add-cloudsql-instances" not in joined
    assert "--set-cloudsql-instances" not in joined
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
    assert "--set-cloudsql-instances=demo-proj:europe-central2:tea-sessions" in args
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
    assert "--set-cloudsql-instances=demo-proj:europe-central2:tea-sessions" in args
    env = next(item for item in args if item.startswith("--set-env-vars="))
    assert "CLOUD_SQL_INSTANCE=demo-proj:europe-central2:tea-sessions" in env
    assert "CLOUD_SQL_INSTANCE=tea-sessions," not in env
    assert "SESSION_DB_PASSWORD=SESSION_DB_PASSWORD:latest" in joined


def test_agent_deploy_defaults_session_user_to_postgres() -> None:
    args = agent_deploy_args(
        project="demo-proj",
        cloud_sql_instance="demo-proj:europe-central2:tea-sessions",
    )
    env = next(item for item in args if item.startswith("--set-env-vars="))
    assert "SESSION_DB_USER=postgres" in env
    assert "--set-cloudsql-instances=demo-proj:europe-central2:tea-sessions" in args


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


def test_agent_fast_api_auto_creates_named_telegram_sessions() -> None:
    text = (ROOT / "tea_agent" / "fast_api_app.py").read_text(encoding="utf-8")
    assert "auto_create_session=True" in text
    assert "session_service_uri=services.SESSION_SERVICE_URI" in text


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


def test_ensure_provisions_cloud_sql_when_backend_missing(monkeypatch) -> None:
    mod = _load_deploy_module()
    called: dict[str, tuple] = {}

    class FakeSetup:
        CLOUD_SQL_INSTANCE_NAME = "tea-sessions"

        @staticmethod
        def instance_connection_name(project, region, instance):
            return f"{project}:{region}:{instance}"

        @staticmethod
        def _execute(project, region, instance, user, database):
            called["args"] = (project, region, instance, user, database)

    monkeypatch.setattr(mod, "_agent_engine_env", lambda: (None, None))
    monkeypatch.setattr(
        mod, "_cloud_sql_env", lambda: (None, "tea_agent", "tea_sessions")
    )
    monkeypatch.setattr(mod, "_load_setup_cloud_sql", lambda: FakeSetup)
    engine_id, location, sql, user, database = mod._ensure_session_backend(
        "demo-proj", "europe-central2", provision=True
    )
    assert engine_id is None
    assert location is None
    assert sql == "demo-proj:europe-central2:tea-sessions"
    assert user == "tea_agent"
    assert database == "tea_sessions"
    assert called["args"] == (
        "demo-proj",
        "europe-central2",
        "tea-sessions",
        "tea_agent",
        "tea_sessions",
    )


def test_ensure_does_not_provision_when_engine_or_sql_set(monkeypatch) -> None:
    mod = _load_deploy_module()

    def _boom():
        raise AssertionError("must not provision Cloud SQL")

    monkeypatch.setattr(mod, "_load_setup_cloud_sql", _boom)
    monkeypatch.setattr(mod, "_agent_engine_env", lambda: (None, None))
    monkeypatch.setattr(
        mod,
        "_cloud_sql_env",
        lambda: ("proj:europe-central2:tea-sessions", "tea_agent", "tea_sessions"),
    )
    engine_id, _location, sql, _user, _database = mod._ensure_session_backend(
        "proj", "europe-central2", provision=True
    )
    assert engine_id is None
    assert sql == "proj:europe-central2:tea-sessions"
    monkeypatch.setattr(mod, "_cloud_sql_env", lambda: (None, "tea_agent", "tea_sessions"))
    monkeypatch.setattr(mod, "_agent_engine_env", lambda: ("engine-123", "eu"))
    engine_id, location, sql, _user, _database = mod._ensure_session_backend(
        "proj", "europe-central2", provision=True
    )
    assert engine_id == "engine-123"
    assert location == "eu"
    assert sql is None


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


def test_dry_run_plan_says_execute_will_create_cloud_sql(capsys, monkeypatch) -> None:
    mod = _load_deploy_module()
    monkeypatch.setattr(mod, "_agent_engine_env", lambda: (None, None))
    monkeypatch.setattr(
        mod, "_cloud_sql_env", lambda: (None, "tea_agent", "tea_sessions")
    )
    mod._print_plan("demo-proj", "europe-central2")
    out = capsys.readouterr().out
    assert "will CREATE Cloud SQL tea-sessions" in out
    assert "demo-proj:europe-central2:tea-sessions" in out
    assert "--set-cloudsql-instances=demo-proj:europe-central2:tea-sessions" in out
    assert "--write" in out


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


def test_dry_run_plan_verifies_tea_agent_before_telegram(capsys, monkeypatch) -> None:
    mod = _load_deploy_module()
    monkeypatch.setattr(mod, "_agent_engine_env", lambda: (None, None))
    monkeypatch.setattr(
        mod, "_cloud_sql_env", lambda: ("tea-sessions", "tea_agent", "tea_sessions")
    )
    mod._print_plan("demo-proj", "europe-central2")
    out = capsys.readouterr().out
    assert out.index("verify_session_persistence.py --write") < out.index(
        "gcloud run deploy telegram-integration"
    )
    assert out.index("verify_session_persistence.py --check") < out.index(
        "gcloud run deploy telegram-integration"
    )


def test_execute_verifies_tea_agent_before_telegram(monkeypatch) -> None:
    mod = _load_deploy_module()
    order: list[str] = []

    class Proc:
        returncode = 0
        stdout = "https://tea-agent.example"
        stderr = ""

    monkeypatch.setattr(mod, "_gcloud", lambda args: Proc())
    monkeypatch.setattr(mod, "_enable_apis", lambda project: order.append("apis"))
    monkeypatch.setattr(mod, "_grant_builder_role", lambda project: order.append("builder"))
    monkeypatch.setattr(
        mod,
        "_ensure_session_backend",
        lambda project, region, *, provision: (
            None,
            None,
            "demo-proj:europe-central2:tea-sessions",
            "tea_agent",
            "tea_sessions",
        ),
    )
    monkeypatch.setattr(
        mod, "_grant_secret_access", lambda project, extra=(): order.append("secrets")
    )
    monkeypatch.setattr(mod, "_grant_cloudsql_client", lambda project: order.append("sql"))
    monkeypatch.setattr(mod, "_wait_for_iam", lambda: order.append("iam-wait"))
    monkeypatch.setattr(mod, "_run_step", lambda label, args: order.append(label) or Proc())
    monkeypatch.setattr(
        mod, "_service_url", lambda project, region, service: f"https://{service}.example"
    )
    monkeypatch.setattr(
        mod, "_verify_after_deploy", lambda *args, **kwargs: order.append("verify")
    )
    mod._execute("demo-proj", "europe-central2")
    assert order.index("iam-wait") < order.index("tea-agent")
    assert order.index("tea-agent") < order.index("verify")
    assert order.index("verify") < order.index(
        "telegram-integration stage 1 (placeholder SERVICE_URL)"
    )


def test_verify_cli_retries_transient_failure(monkeypatch) -> None:
    mod = _load_deploy_module()
    attempts = {"n": 0}

    class Proc:
        def __init__(self, code: int) -> None:
            self.returncode = code
            self.stdout = "wrote session" if code == 0 else ""
            self.stderr = "connection reset" if code else ""

    def fake_run(*args, **kwargs):
        attempts["n"] += 1
        return Proc(1 if attempts["n"] < 3 else 0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda _sec: None)
    mod._verify_cli("https://tea-agent.example", "--write")
    assert attempts["n"] == 3
