"""TEA-14 live-verify helper (no secrets)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = ROOT / "scripts" / "verify_session_persistence.py"
    spec = importlib.util.spec_from_file_location("verify_session_persistence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_session_url_uses_hyphenated_telegram_ids() -> None:
    mod = _load()
    url = mod.session_url("https://tea-agent.example/run", 140014)
    assert url == (
        "https://tea-agent.example/apps/tea_agent/users/tg-140014/sessions/tg-sess-140014"
    )
    assert "tg-140014" in url
    assert "tg-sess-140014" in url
    assert "tg_sess_" not in url
    assert "tg_140014" not in url


def test_auth_headers_use_env_token_without_printing(monkeypatch) -> None:
    mod = _load()
    monkeypatch.delenv("CLOUD_RUN_ID_TOKEN", raising=False)
    assert mod.auth_headers() == {}
    monkeypatch.setenv("CLOUD_RUN_ID_TOKEN", "id-token")
    assert mod.auth_headers() == {"Authorization": "Bearer id-token"}


def test_check_profile_rejects_missing_user_experience(monkeypatch) -> None:
    mod = _load()

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "tg-sess-140014", "state": {"experience": "only-unprefixed"}}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(mod.httpx, "Client", FakeClient)
    with pytest.raises(SystemExit, match="user:experience not restored"):
        mod.check_profile("https://tea-agent.example/apps/x")


def test_write_profile_patches_existing_empty_session(monkeypatch) -> None:
    """Telegram ensure_session POSTs {} first; --write must still store user: keys."""
    mod = _load()
    calls: list[tuple[str, object]] = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = ""

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            calls.append(("GET", None))
            return FakeResponse(200, {"id": "tg-sess-140014", "state": {}})

        def patch(self, url, json=None):
            calls.append(("PATCH", json))
            return FakeResponse(
                200,
                {
                    "id": "tg-sess-140014",
                    "userId": "tg-140014",
                    "state": json["state_delta"],
                },
            )

        def post(self, url, json=None):
            calls.append(("POST", json))
            raise AssertionError("must not recreate an existing session")

    monkeypatch.setattr(mod.httpx, "Client", FakeClient)
    body = mod.write_profile("https://tea-agent.example/apps/x")
    assert calls[0][0] == "GET"
    assert calls[1] == ("PATCH", {"state_delta": mod.PROFILE})
    assert body["state"]["user:experience"] == "новичок"
    assert not any(method == "POST" for method, _ in calls)
