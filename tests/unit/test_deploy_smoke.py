"""Post-deploy tea-agent smoke test (TEA-38)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from tea_agent.app_utils.agent_auth import AUTH_HEADER

ROOT = Path(__file__).resolve().parents[2]
SECRET = "smoke-secret-do-not-print"
AGENT = "https://tea-agent.example"


def _load():
    spec = importlib.util.spec_from_file_location(
        "deploy_cloud_run_smoke", ROOT / "scripts" / "deploy_cloud_run.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Proc:
    def __init__(self, code: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


def _answer() -> list[dict]:
    return [
        {
            "author": "tea_agent",
            "content": {"parts": [{"text": "Здравствуйте"}]},
        }
    ]


def _script(mod, handler):
    transport = httpx.MockTransport(handler)
    mod._run_post_deploy_smoke(
        "demo-proj",
        AGENT,
        transport=transport,
        secret=SECRET,
    )


def _paths(seen: list[httpx.Request]) -> list[tuple[str, str]]:
    return [(request.method, request.url.path) for request in seen]


def test_smoke_user_is_not_a_telegram_id() -> None:
    mod = _load()
    assert mod.SMOKE_USER_ID == "tea-smoke"
    assert not mod.SMOKE_USER_ID.startswith("tg-")
    assert not mod.SMOKE_USER_ID.isdigit()
    urls = mod.smoke_urls(AGENT + "/run")
    assert urls["health"] == AGENT + "/health"
    assert urls["run"] == AGENT + "/run"
    assert urls["session"].endswith("/apps/tea_agent/users/tea-smoke/sessions/tea-smoke-1")


def test_smoke_happy_path_then_deletes_session(capsys) -> None:
    mod = _load()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "tea-agent.example"
        path = request.url.path
        if path == "/health":
            assert AUTH_HEADER not in request.headers
            return httpx.Response(200, json={"status": "ok"})
        if path == "/run" and AUTH_HEADER not in request.headers:
            return httpx.Response(401, json={"detail": "unauthorized"})
        if path.endswith("/sessions/tea-smoke-1") and request.method == "GET":
            assert request.headers[AUTH_HEADER] == SECRET
            return httpx.Response(404)
        if path.endswith("/sessions/tea-smoke-1") and request.method == "POST":
            assert request.headers[AUTH_HEADER] == SECRET
            return httpx.Response(201, json={"id": "tea-smoke-1"})
        if path == "/run" and request.method == "POST":
            assert request.headers[AUTH_HEADER] == SECRET
            body = json.loads(request.content.decode())
            assert body["userId"] == "tea-smoke"
            assert body["sessionId"] == "tea-smoke-1"
            assert body["newMessage"]["parts"][0]["text"] == mod.SMOKE_MESSAGE
            return httpx.Response(200, json=_answer())
        if request.method == "DELETE":
            assert request.headers[AUTH_HEADER] == SECRET
            return httpx.Response(204)
        raise AssertionError(request.method + " " + path)

    _script(mod, handler)
    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert "Smoke passed" in captured.out
    assert "telegram-integration was not called" in captured.out
    assert _paths(seen) == [
        ("GET", "/health"),
        ("POST", "/run"),
        ("GET", "/apps/tea_agent/users/tea-smoke/sessions/tea-smoke-1"),
        ("POST", "/apps/tea_agent/users/tea-smoke/sessions/tea-smoke-1"),
        ("POST", "/run"),
        ("DELETE", "/apps/tea_agent/users/tea-smoke/sessions/tea-smoke-1"),
    ]


def test_smoke_fails_when_health_is_not_200(capsys) -> None:
    mod = _load()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(500, text="boom")

    with pytest.raises(SystemExit) as exc:
        _script(mod, handler)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "GET /health returned 500" in err
    assert "expected 200" in err
    assert SECRET not in err
    assert seen == ["/health"]


def test_smoke_fails_when_unauthenticated_run_is_not_401(capsys) -> None:
    mod = _load()
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json=_answer())

    with pytest.raises(SystemExit) as exc:
        _script(mod, handler)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "401" in err
    assert AUTH_HEADER in err
    assert seen == [("GET", "/health"), ("POST", "/run")]


def test_smoke_fails_on_empty_answer_and_still_deletes(capsys) -> None:
    mod = _load()
    deleted = {"yes": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/run" and AUTH_HEADER not in request.headers:
            return httpx.Response(401)
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "POST" and request.url.path.endswith("/tea-smoke-1"):
            return httpx.Response(201, json={"id": "tea-smoke-1"})
        if request.url.path == "/run":
            return httpx.Response(
                200,
                json=[{"author": "user", "content": {"parts": [{"text": "Привет"}]}}],
            )
        if request.method == "DELETE":
            deleted["yes"] = True
            return httpx.Response(204)
        raise AssertionError(request.method + " " + request.url.path)

    with pytest.raises(SystemExit) as exc:
        _script(mod, handler)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "answer text was empty" in err
    assert SECRET not in err
    assert deleted["yes"] is True


def test_smoke_redacts_secret_in_run_error_body(capsys) -> None:
    mod = _load()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        if AUTH_HEADER not in request.headers:
            return httpx.Response(401)
        if request.method == "GET":
            return httpx.Response(200, json={"id": "tea-smoke-1"})
        if request.url.path == "/run":
            return httpx.Response(500, text=f"upstream said {SECRET}")
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(request.method)

    with pytest.raises(SystemExit):
        _script(mod, handler)
    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert "[redacted]" in captured.err
    assert "returned 500" in captured.err


def test_smoke_retries_transient_health(monkeypatch) -> None:
    mod = _load()
    monkeypatch.setattr(mod.time, "sleep", lambda _sec: None)
    calls = {"health": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            calls["health"] += 1
            if calls["health"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/run" and AUTH_HEADER not in request.headers:
            return httpx.Response(401)
        if request.method == "GET":
            return httpx.Response(200, json={"id": "tea-smoke-1"})
        if request.url.path == "/run":
            return httpx.Response(200, json=_answer())
        if request.method == "DELETE":
            return httpx.Response(405)
        raise AssertionError(request.method + " " + request.url.path)

    _script(mod, handler)
    assert calls["health"] == 3


def test_access_auth_secret_never_prints(capsys, monkeypatch) -> None:
    mod = _load()

    def fake(args):
        assert args[:4] == ["secrets", "versions", "access", "latest"]
        assert f"--secret={mod.AUTH_SECRET_ENV}" in args
        return _Proc(0, stdout=SECRET + "\n", stderr="harmless warning")

    monkeypatch.setattr(mod, "_gcloud", fake)
    assert mod._access_auth_secret("demo-proj") == SECRET
    captured = capsys.readouterr()
    assert captured.out == ""
    assert SECRET not in captured.err


def test_access_auth_secret_failure_hides_stdout(capsys, monkeypatch) -> None:
    mod = _load()

    def fake(args):
        return _Proc(1, stdout=SECRET, stderr="PERMISSION_DENIED")

    monkeypatch.setattr(mod, "_gcloud", fake)
    with pytest.raises(SystemExit):
        mod._access_auth_secret("demo-proj")
    err = capsys.readouterr().err
    assert SECRET not in err
    assert "secretAccessor" in err
    assert "PERMISSION_DENIED" in err


def test_smoke_only_does_not_deploy(monkeypatch) -> None:
    mod = _load()
    called = {}

    def no_execute(*args, **kwargs):
        raise AssertionError("deploy")

    monkeypatch.setattr(mod, "_execute", no_execute)
    monkeypatch.setattr(
        mod, "_service_url", lambda project, region, service: "https://tea-agent.example"
    )
    monkeypatch.setattr(
        mod,
        "_run_post_deploy_smoke",
        lambda project, url: called.update(project=project, url=url),
    )
    monkeypatch.setattr(
        sys, "argv", ["deploy_cloud_run.py", "--project=demo-proj", "--smoke-only"]
    )
    mod.main()
    assert called == {"project": "demo-proj", "url": "https://tea-agent.example"}


def test_smoke_only_rejects_execute(monkeypatch) -> None:
    mod = _load()
    monkeypatch.setattr(
        sys,
        "argv",
        ["deploy_cloud_run.py", "--project=demo-proj", "--smoke-only", "--execute"],
    )
    with pytest.raises(SystemExit):
        mod.main()


def test_skip_smoke_skips_the_check(monkeypatch) -> None:
    mod = _load()

    class Proc:
        returncode = 0
        stdout = "https://tea-agent.example"
        stderr = ""

    def no_smoke(*args, **kwargs):
        raise AssertionError("smoke")

    monkeypatch.setattr(mod, "_gcloud", lambda args: Proc())
    monkeypatch.setattr(mod, "_enable_apis", lambda project: None)
    monkeypatch.setattr(mod, "_grant_builder_role", lambda project: None)
    monkeypatch.setattr(
        mod,
        "_ensure_session_backend",
        lambda project, region, *, provision: (None, None, None, "postgres", "tea_sessions"),
    )
    monkeypatch.setattr(mod, "_grant_secret_access", lambda project, extra=(): None)
    monkeypatch.setattr(mod, "_run_step", lambda label, args: Proc())
    monkeypatch.setattr(
        mod, "_service_url", lambda project, region, service: f"https://{service}.example"
    )
    monkeypatch.setattr(mod, "_run_post_deploy_smoke", no_smoke)
    mod._execute("demo-proj", "europe-central2", skip_smoke=True)


def test_dry_run_mentions_smoke_and_env_replacement(capsys, monkeypatch) -> None:
    mod = _load()
    monkeypatch.setattr(mod, "_agent_engine_env", lambda: (None, None))
    monkeypatch.setattr(mod, "_cloud_sql_env", lambda: (None, "postgres", "tea_sessions"))
    mod._print_plan("demo-proj", "europe-central2")
    out = capsys.readouterr().out
    assert "tea-smoke" in out
    assert "GET /health -> 200" in out
    assert "401" in out
    assert "--smoke-only" in out
    assert "--skip-smoke" in out
    assert "TELEGRAM_ALLOWED_USER_IDS" in out
    assert "TELEGRAM_ADMIN_USER_IDS" in out
    assert "--set-env-vars" in out
