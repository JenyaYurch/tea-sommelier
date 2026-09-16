"""TEA-12: real secrets must not be tracked by git."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_SUFFIXES = (
    ".env",
    ".pem",
    "-key.json",
    "credentials.json",
    "application_default_credentials.json",
)
ALLOWED_TRACKED = {".env.example"}


def _tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [name for name in proc.stdout.decode().split("\0") if name]


def test_secret_files_are_not_tracked() -> None:
    tracked = _tracked_files()
    leaked = []
    for path in tracked:
        name = Path(path).name
        if path.replace("\\", "/") in ALLOWED_TRACKED or name in ALLOWED_TRACKED:
            continue
        if name.endswith(FORBIDDEN_SUFFIXES) or name in {".env", "credentials.json"}:
            leaked.append(path)
        if name.startswith("service-account") and name.endswith(".json"):
            leaked.append(path)
    assert leaked == []


def test_env_example_has_placeholders_only() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "your-api-key-here" in text
    assert "your-telegram-bot-token" in text
    assert "GEMINI_API_KEY=AIza" not in text
    assert "TELEGRAM_BOT_TOKEN=" + "1" * 20 not in text
    assert "deploy_cloud_run.py --execute" in text
    assert "setup_memory_bank.py" in text
    assert "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION=eu" in text
