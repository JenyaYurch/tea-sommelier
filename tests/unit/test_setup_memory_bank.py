"""Memory Bank setup script dry-run (TEA-9)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_setup_module():
    path = ROOT / "scripts" / "setup_memory_bank.py"
    spec = importlib.util.spec_from_file_location("setup_memory_bank", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_memory_bank_plan_mentions_eu_and_topics(capsys) -> None:
    mod = _load_setup_module()
    mod._print_plan("demo-proj", mod.DEFAULT_LOCATION, mod.DEFAULT_DISPLAY_NAME)
    out = capsys.readouterr().out
    assert "demo-proj" in out
    assert "eu" in out
    assert "TEA_TASTE_PROFILE" in out
    assert "--execute" in out
    assert "Dry-run" in out


def test_setup_memory_bank_env_lines_use_resource_name(capsys) -> None:
    mod = _load_setup_module()
    mod._print_env_lines(
        "projects/demo/locations/eu/reasoningEngines/engine-123",
        "eu",
    )
    out = capsys.readouterr().out
    assert "GOOGLE_CLOUD_AGENT_ENGINE_ID=engine-123" in out
    assert "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION=eu" in out
    assert (
        "MEMORY_SERVICE_URI=agentengine://projects/demo/locations/eu/reasoningEngines/engine-123"
        in out
    )
