"""TEA-14: Cloud Run entrypoint keeps a Telegram profile across process restart."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from telegram_integration.keyboard import telegram_session_id, telegram_user_key

ROOT = Path(__file__).resolve().parents[2]
APP_NAME = "tea_agent"
CLOUD_SQL_LIKE_ROOT = Path("/tmp/cloudsql")
CLOUD_SQL_LIKE_INSTANCE = "demo-proj:europe-central2:tea-sessions"
PROFILE = {
    "experience": "новичок",
    "user:experience": "новичок",
    "user:taste_profile": "мягкий без горечи",
    "user:profile_complete": True,
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env["OTEL_TO_CLOUD"] = "false"
    env.pop("SESSION_SERVICE_URI", None)
    env.pop("K_SERVICE", None)
    env.pop("CLOUD_SQL_INSTANCE", None)
    env.pop("CLOUD_SQL_SOCKET_DIR", None)
    env.pop("SESSION_DB_PASSWORD", None)
    env.pop("GOOGLE_CLOUD_AGENT_ENGINE_ID", None)
    return env


def _start_server(port: int, extra_env: dict[str, str]) -> subprocess.Popen[str]:
    env = _base_env()
    env.update(extra_env)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tea_agent.fast_api_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_ready(base: str, proc: subprocess.Popen[str], timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    url = f"{base}/apps/{APP_NAME}/users/tg-warmup/sessions/tg-sess-warmup"
    last_error = "not started"
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(
                f"tea-agent exited {proc.returncode} before ready: {stderr[-1500:]}"
            )
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code in {404, 200}:
                return
            last_error = f"{response.status_code} {response.text[:200]}"
        except httpx.HTTPError as err:
            last_error = str(err)
        time.sleep(0.2)
    raise AssertionError(f"tea-agent HTTP not ready: {last_error}")


def _stop(proc: subprocess.Popen[str]) -> str:
    stderr = ""
    if proc.poll() is None:
        proc.terminate()
        try:
            _, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate(timeout=5)
    elif proc.stderr is not None:
        stderr = proc.stderr.read()
    return stderr


def _require_cloud_sql_like_socket() -> None:
    socket_path = CLOUD_SQL_LIKE_ROOT / CLOUD_SQL_LIKE_INSTANCE / ".s.PGSQL.5432"
    if not socket_path.exists():
        pytest.skip("Cloud SQL-like Postgres unix socket is not running")


@pytest.fixture
def sqlite_uri(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'sessions.db').resolve().as_posix()}"


@pytest.fixture
def server_port() -> Iterator[int]:
    yield _free_port()


def _roundtrip_profile(
    port: int, extra_env: dict[str, str], telegram_id: int = 4242
) -> None:
    user_id = telegram_user_key(telegram_id)
    session_id = telegram_session_id(telegram_id)
    base = f"http://127.0.0.1:{port}"
    session_url = f"{base}/apps/{APP_NAME}/users/{user_id}/sessions/{session_id}"

    first = _start_server(port, extra_env)
    try:
        _wait_ready(base, first)
        created = httpx.post(session_url, json=PROFILE, timeout=15.0)
        if created.status_code == 409:
            created = httpx.get(session_url, timeout=15.0)
        assert created.status_code in {200, 201}, created.text
        body = created.json()
        assert body["id"] == session_id
        assert body["userId"] == user_id
        assert (body.get("state") or {}).get("user:experience") == "новичок"
        duplicate = httpx.post(session_url, json={}, timeout=15.0)
        assert duplicate.status_code == 409
    except Exception:
        _stop(first)
        raise
    stderr_first = _stop(first)
    assert first.returncode is not None

    restarted = _start_server(port, extra_env)
    try:
        _wait_ready(base, restarted)
        loaded = httpx.get(session_url, timeout=15.0)
        assert loaded.status_code == 200, loaded.text + stderr_first
        state = loaded.json().get("state") or {}
        assert loaded.json()["id"] == session_id
        assert state.get("user:experience") == "новичок"
        assert state.get("experience") == "новичок"
        assert state.get("user:taste_profile") == "мягкий без горечи"
        assert state.get("user:profile_complete") is True
        other = httpx.get(
            f"{base}/apps/{APP_NAME}/users/{telegram_user_key(telegram_id + 1)}"
            f"/sessions/{telegram_session_id(telegram_id + 1)}",
            timeout=15.0,
        )
        assert other.status_code == 404
    finally:
        _stop(restarted)


def test_http_session_profile_survives_process_restart(
    sqlite_uri: str, server_port: int
) -> None:
    _roundtrip_profile(server_port, {"SESSION_SERVICE_URI": sqlite_uri})


def test_cloud_run_env_postgres_profile_survives_process_restart(server_port: int) -> None:
    """K_SERVICE + CLOUD_SQL_INSTANCE + password — the Cloud Run tea-agent env shape."""
    _require_cloud_sql_like_socket()
    _roundtrip_profile(
        server_port,
        {
            "K_SERVICE": "tea-agent",
            "CLOUD_SQL_INSTANCE": CLOUD_SQL_LIKE_INSTANCE,
            "CLOUD_SQL_SOCKET_DIR": str(CLOUD_SQL_LIKE_ROOT),
            "SESSION_DB_USER": "tea_agent",
            "SESSION_DB_NAME": "tea_sessions",
            "SESSION_DB_PASSWORD": "tea_test_pass",
        },
        telegram_id=4242014,
    )


def test_cloud_run_entrypoint_refuses_sqlite(sqlite_uri: str, server_port: int) -> None:
    proc = _start_server(
        server_port,
        {"K_SERVICE": "tea-agent", "SESSION_SERVICE_URI": sqlite_uri},
    )
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        stderr = _stop(proc)
        raise AssertionError(f"Cloud Run sqlite backend should fail fast: {stderr[-1500:]}")
    stderr = proc.stderr.read() if proc.stderr else ""
    combined = stderr + (proc.stdout.read() if proc.stdout else "")
    assert proc.returncode != 0
    assert "sqlite is ephemeral" in combined or "persistent ADK session backend" in combined
