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
