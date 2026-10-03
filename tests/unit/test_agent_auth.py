"""TEA-34: tea-agent shared-secret gate and prod dev-UI switch."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tea_agent.app_utils.agent_auth import (
    AUTH_HEADER,
    AgentAuthMiddleware,
    auth_required,
    dev_ui_enabled,
    secret_matches,
)

ROOT = Path(__file__).resolve().parents[2]
_SECRET = "correct-secret"


def _gated_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/run")
    def run() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
    def session(app_name: str, user_id: str, session_id: str) -> dict[str, str]:
        return {"app": app_name, "user": user_id, "session": session_id}

    app.add_middleware(AgentAuthMiddleware)
    return app


def _clear_auth_env(monkeypatch) -> None:
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("TEA_AGENT_AUTH_SECRET", raising=False)
    monkeypatch.delenv("TEA_AGENT_DEV_UI", raising=False)


def test_dev_mode_allows_requests_without_a_secret(monkeypatch) -> None:
    _clear_auth_env(monkeypatch)
    assert auth_required() is False
    assert dev_ui_enabled() is True
    response = TestClient(_gated_app()).post("/run")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_missing_secret_is_rejected_when_configured(monkeypatch) -> None:
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("TEA_AGENT_AUTH_SECRET", _SECRET)
    response = TestClient(_gated_app()).post("/run")
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"
    assert _SECRET not in response.text


def test_wrong_secret_is_rejected(monkeypatch) -> None:
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("TEA_AGENT_AUTH_SECRET", _SECRET)
    response = TestClient(_gated_app()).post(
        "/run",
        headers={AUTH_HEADER: "wrong-secret"},
    )
    assert response.status_code == 401
    assert secret_matches("wrong-secret") is False
    assert secret_matches("correct-secre") is False
    assert secret_matches(_SECRET + " ") is True


def test_correct_secret_allows_run_and_session_read(monkeypatch) -> None:
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("TEA_AGENT_AUTH_SECRET", _SECRET)
    client = TestClient(_gated_app())
    headers = {AUTH_HEADER: _SECRET}
    run = client.post("/run", headers=headers)
    assert run.status_code == 200
    session = client.get(
        "/apps/tea_agent/users/tg-1/sessions/tg-sess-1",
        headers=headers,
    )
    assert session.status_code == 200
    assert session.json()["session"] == "tg-sess-1"
    assert secret_matches(_SECRET) is True


def test_cloud_run_without_secret_fails_closed_except_health(monkeypatch) -> None:
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("K_SERVICE", "tea-agent")
    assert auth_required() is True
    assert dev_ui_enabled() is False
    assert secret_matches("anything") is False
    client = TestClient(_gated_app())
    assert client.get("/health").status_code == 200
    assert client.get("/health/").status_code == 200
    denied = client.get("/apps/tea_agent/users/tg-1/sessions/tg-sess-1")
    assert denied.status_code == 401
    also_denied = client.post("/run", headers={AUTH_HEADER: "not-configured"})
    assert also_denied.status_code == 401


def test_reject_log_does_not_include_the_secret(monkeypatch, caplog) -> None:
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("TEA_AGENT_AUTH_SECRET", _SECRET)
    with caplog.at_level("WARNING"):
        TestClient(_gated_app()).post("/run", headers={AUTH_HEADER: _SECRET + "-no"})
    assert _SECRET not in caplog.text
    assert "rejected tea-agent request" in caplog.text


def test_dev_ui_flag_overrides_cloud_run_detection(monkeypatch) -> None:
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("K_SERVICE", "tea-agent")
    monkeypatch.setenv("TEA_AGENT_DEV_UI", "true")
    assert dev_ui_enabled() is True
    monkeypatch.setenv("TEA_AGENT_DEV_UI", "false")
    assert dev_ui_enabled() is False
    monkeypatch.delenv("K_SERVICE")
    monkeypatch.setenv("TEA_AGENT_DEV_UI", "0")
    assert dev_ui_enabled() is False


def _app_routes(extra: dict[str, str]) -> str:
    env = os.environ.copy()
    env["OTEL_TO_CLOUD"] = "false"
    env["TEA_ALLOW_EPHEMERAL_SESSIONS"] = "true"
    env["CLOUD_SQL_INSTANCE"] = ""
    env["SESSION_SERVICE_URI"] = ""
    env["GOOGLE_CLOUD_AGENT_ENGINE_ID"] = ""
    env.update(extra)
    script = """
from tea_agent.fast_api_app import app
from tea_agent.app_utils.agent_auth import AgentAuthMiddleware
paths = {getattr(route, "path", None) for route in app.routes}
print("dev_ui=yes" if "/dev-ui" in paths else "dev_ui=no")
print("health=yes" if "/health" in paths else "health=no")
print("run=yes" if "/run" in paths else "run=no")
mounted = any(
    getattr(middleware, "cls", None) is AgentAuthMiddleware
    for middleware in app.user_middleware
)
print("middleware=yes" if mounted else "middleware=no")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_prod_app_hides_dev_ui_and_mounts_auth() -> None:
    out = _app_routes(
        {
            "K_SERVICE": "tea-agent",
            "TEA_AGENT_DEV_UI": "false",
            "TEA_AGENT_AUTH_SECRET": "",
        }
    )
    assert "dev_ui=no" in out
    assert "health=yes" in out
    assert "run=yes" in out
    assert "middleware=yes" in out


def test_local_app_serves_dev_ui() -> None:
    out = _app_routes(
        {
            "K_SERVICE": "",
            "TEA_AGENT_DEV_UI": "",
            "TEA_AGENT_AUTH_SECRET": "",
        }
    )
    assert "dev_ui=yes" in out
    assert "middleware=yes" in out
