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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_env(db_uri: str) -> dict[str, str]:
    env = os.environ.copy()
    env["SESSION_SERVICE_URI"] = db_uri
    env["OTEL_TO_CLOUD"] = "false"
    env.pop("K_SERVICE", None)
    env.pop("CLOUD_SQL_INSTANCE", None)
    env.pop("SESSION_DB_PASSWORD", None)
    env.pop("GOOGLE_CLOUD_AGENT_ENGINE_ID", None)
    return env


def _start_server(port: int, db_uri: str) -> subprocess.Popen[str]:
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
        env=_server_env(db_uri),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_ready(base: str, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    url = f"{base}/apps/{APP_NAME}/users/tg-warmup/sessions/tg-sess-warmup"
    last_error = "not started"
    while time.time() < deadline:
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


@pytest.fixture
def sqlite_uri(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'sessions.db').resolve().as_posix()}"


@pytest.fixture
def server_port() -> Iterator[int]:
    yield _free_port()


def test_http_session_profile_survives_process_restart(
    sqlite_uri: str, server_port: int
) -> None:
    user_id = telegram_user_key(4242)
    session_id = telegram_session_id(4242)
    base = f"http://127.0.0.1:{server_port}"
    session_url = f"{base}/apps/{APP_NAME}/users/{user_id}/sessions/{session_id}"
    profile = {
        "experience": "новичок",
        "user:experience": "новичок",
        "user:taste_profile": "мягкий без горечи",
        "user:profile_complete": True,
    }

    first = _start_server(server_port, sqlite_uri)
    try:
        _wait_ready(base)
        created = httpx.post(session_url, json=profile, timeout=15.0)
        assert created.status_code in {200, 201}, created.text
        body = created.json()
        assert body["id"] == session_id
        assert body["userId"] == user_id
        state = body.get("state") or {}
        assert state.get("user:experience") == "новичок"
        duplicate = httpx.post(session_url, json={}, timeout=15.0)
        assert duplicate.status_code == 409
    except Exception:
        _stop(first)
        raise
    stderr_first = _stop(first)
    assert first.returncode is not None

    restarted = _start_server(server_port, sqlite_uri)
    try:
        _wait_ready(base)
        loaded = httpx.get(session_url, timeout=15.0)
        assert loaded.status_code == 200, loaded.text + stderr_first
        state = loaded.json().get("state") or {}
        assert loaded.json()["id"] == session_id
        assert state.get("user:experience") == "новичок"
        assert state.get("experience") == "новичок"
        assert state.get("user:taste_profile") == "мягкий без горечи"
        assert state.get("user:profile_complete") is True
        other = httpx.get(
            f"{base}/apps/{APP_NAME}/users/{telegram_user_key(7)}"
            f"/sessions/{telegram_session_id(7)}",
            timeout=15.0,
        )
        assert other.status_code == 404
    finally:
        _stop(restarted)
