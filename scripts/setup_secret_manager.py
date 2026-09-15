"""Create Secret Manager secrets from local .env (TEA-12).

Never prints secret values. Uses the billed TeaBot GCP project.
Does not enable a new Free Trial / billing account.

Usage:
    uv run python scripts/setup_secret_manager.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = "gen-lang-client-0393777014"
CLOUD_RUN_REGION = "europe-central2"
SECRET_NAMES = ("TELEGRAM_BOT_TOKEN", "GOOGLE_API_KEY")


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        values[key.strip()] = raw.strip().strip("'").strip('"')
    return values


def _gcloud_bin() -> str:
    for name in ("gcloud.cmd", "gcloud"):
        found = shutil.which(name)
        if found:
            return found
    print("gcloud is not on PATH. Install Google Cloud SDK and retry.", file=sys.stderr)
    raise SystemExit(1)


def _gcloud(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_gcloud_bin(), *args],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def _fail(message: str, proc: subprocess.CompletedProcess[str] | None = None) -> None:
    print(message, file=sys.stderr)
    if proc is not None and proc.stderr:
        print(proc.stderr.strip(), file=sys.stderr)
    raise SystemExit(1)


def _secret_exists(project: str, name: str) -> bool:
    proc = _gcloud(
        ["secrets", "describe", name, f"--project={project}", "--format=value(name)"]
    )
    return proc.returncode == 0


def _upsert_secret(project: str, name: str, value: str) -> str:
    if not value:
        _fail(f"{name} is empty; put it in .env first")
    if _secret_exists(project, name):
        proc = _gcloud(
            ["secrets", "versions", "add", name, f"--project={project}", "--data-file=-"],
            stdin=value,
        )
        if proc.returncode != 0:
            _fail(f"Failed to add a new version of {name}", proc)
        return "updated"
    proc = _gcloud(
        [
            "secrets",
            "create",
            name,
            f"--project={project}",
            "--replication-policy=automatic",
            "--data-file=-",
        ],
        stdin=value,
    )
    if proc.returncode != 0:
        _fail(f"Failed to create secret {name}", proc)
    return "created"


def main() -> None:
    env = _read_dotenv(ROOT / ".env")
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or env.get("GOOGLE_CLOUD_PROJECT")
        or DEFAULT_PROJECT
    ).strip()

    telegram = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    google_key = (
        env.get("GOOGLE_API_KEY") or env.get("GEMINI_API_KEY") or ""
    ).strip()
    if not telegram or not google_key:
        _fail(
            "Local .env must contain TELEGRAM_BOT_TOKEN and "
            "GEMINI_API_KEY (or GOOGLE_API_KEY)"
        )

    print(f"Using GCP project {project}")
    print(f"Cloud Run region (later): {CLOUD_RUN_REGION}")

    proc = _gcloud(["config", "set", "project", project])
    if proc.returncode != 0:
        _fail("Failed to set gcloud project", proc)

    proc = _gcloud(["config", "set", "run/region", CLOUD_RUN_REGION])
    if proc.returncode != 0:
        _fail("Failed to set run/region", proc)

    proc = _gcloud(
        [
            "services",
            "enable",
            "secretmanager.googleapis.com",
            f"--project={project}",
            "--quiet",
        ]
    )
    if proc.returncode != 0:
        _fail("Failed to enable Secret Manager API", proc)

    mapping = {
        "TELEGRAM_BOT_TOKEN": telegram,
        "GOOGLE_API_KEY": google_key,
    }
    for name in SECRET_NAMES:
        action = _upsert_secret(project, name, mapping[name])
        print(f"{name}: {action} (value not printed)")

    listed = _gcloud(
        [
            "secrets",
            "list",
            f"--project={project}",
            "--format=value(name)",
        ]
    )
    if listed.returncode != 0:
        _fail("Failed to list secrets", listed)
    names = {line.rsplit("/", 1)[-1] for line in listed.stdout.splitlines() if line.strip()}
    missing = [name for name in SECRET_NAMES if name not in names]
    if missing:
        _fail(f"Secrets missing after upsert: {', '.join(missing)}")
    print("Secret Manager is ready. Secrets are not in git.")


if __name__ == "__main__":
    main()
