"""Cloud SQL setup script dry-run (TEA-14)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_setup_module():
    path = ROOT / "scripts" / "setup_cloud_sql.py"
    spec = importlib.util.spec_from_file_location("setup_cloud_sql", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_cloud_sql_plan_mentions_region_and_dry_run(capsys) -> None:
    mod = _load_setup_module()
    mod._print_plan(
        "demo-proj",
        "europe-central2",
        "tea-sessions",
        "tea_agent",
        "tea_sessions",
    )
    out = capsys.readouterr().out
    assert "demo-proj" in out
    assert "europe-central2" in out
    assert "tea-sessions" in out
    assert "Dry-run" in out
    assert "--execute" in out
    assert "no secrets printed" in out
    assert "CREATE tables" in out


def test_setup_cloud_sql_env_lines_do_not_print_password(capsys) -> None:
    mod = _load_setup_module()
    mod._print_env_lines(
        "demo-proj",
        "europe-central2",
        "tea-sessions",
        "tea_agent",
        "tea_sessions",
    )
    out = capsys.readouterr().out
    assert (
        "CLOUD_SQL_INSTANCE=demo-proj:europe-central2:tea-sessions" in out
    )
    assert "SESSION_DB_USER=tea_agent" in out
    assert "SESSION_DB_NAME=tea_sessions" in out
    assert "sqlite+aiosqlite:///./sessions.db" in out
    assert "SESSION_DB_PASSWORD=" not in out.split("lives in Secret Manager")[0]
    assert "not printed" in out


def test_setup_execute_skips_create_for_postgres_user(monkeypatch) -> None:
    """POSTGRES_17 public schema: Cloud Run uses the database owner, not a new login."""
    mod = _load_setup_module()
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(mod, "_gcloud", lambda args, *, stdin=None: calls.append(args) or Proc())
    monkeypatch.setattr(mod, "_instance_exists", lambda *a: False)
    monkeypatch.setattr(mod, "_database_exists", lambda *a: False)
    monkeypatch.setattr(mod, "_secret_exists", lambda *a: True)
    monkeypatch.setattr(mod, "_upsert_secret", lambda *a: calls.append(["secrets", "upsert"]))
    monkeypatch.setattr(mod, "_ensure_instance_runnable", lambda *a: None)
    monkeypatch.setattr(mod.secrets, "token_urlsafe", lambda n: "x" * n)
    mod._execute(
        "demo-proj",
        "europe-central2",
        "tea-sessions",
        "postgres",
        "tea_sessions",
    )
    assert any(args[:3] == ["sql", "instances", "create"] for args in calls)
    assert any(args[:3] == ["sql", "databases", "create"] for args in calls)
    assert ["secrets", "upsert"] in calls
    assert not any(args[:3] == ["sql", "users", "create"] for args in calls)


def test_ensure_starts_stopped_cloud_sql_instance(monkeypatch) -> None:
    mod = _load_setup_module()
    calls: list[list[str]] = []
    states = iter(["STOPPED", "RUNNABLE"])

    class Proc:
        def __init__(self, stdout: str = "") -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_gcloud(args, *, stdin=None):
        calls.append(args)
        if args[:3] == ["sql", "instances", "describe"]:
            return Proc(next(states))
        return Proc()

    monkeypatch.setattr(mod, "_gcloud", fake_gcloud)
    monkeypatch.setattr(mod.time, "sleep", lambda _sec: None)
    mod._ensure_instance_runnable("demo-proj", "tea-sessions")
    assert any(
        args[:3] == ["sql", "instances", "patch"] and "--activation-policy=ALWAYS" in args
        for args in calls
    )
