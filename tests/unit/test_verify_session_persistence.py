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
