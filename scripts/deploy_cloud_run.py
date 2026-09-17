"""Two-stage Cloud Run deploy for tea-agent + telegram-integration (TEA-13).

Does not deploy unless you pass --execute (explicit approval).

Usage:
    uv run python scripts/deploy_cloud_run.py
    uv run python scripts/deploy_cloud_run.py --execute
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from telegram_integration.deploy_spec import (
    ADK_APP_NAME,
    AGENT_SERVICE,
    CLOUD_RUN_REGION,
    DEFAULT_PROJECT,
    DEFAULT_SESSION_DB_NAME,
    DEFAULT_SESSION_DB_USER,
    PLACEHOLDER_SERVICE_URL,
    TELEGRAM_SERVICE,
    agent_deploy_args,
    telegram_deploy_args,
    telegram_update_env_args,
)

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_APIS = (
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "storage.googleapis.com",
    "sqladmin.googleapis.com",
)
SECRETS = ("GOOGLE_API_KEY", "TELEGRAM_BOT_TOKEN")
CLOUD_SQL_CLIENT_ROLE = "roles/cloudsql.client"
BUILDER_ROLE = "roles/run.builder"


def _gcloud_bin() -> str:
    for name in ("gcloud.cmd", "gcloud"):
        found = shutil.which(name)
        if found:
            return found
    print("gcloud is not on PATH. Install Google Cloud SDK and retry.", file=sys.stderr)
    raise SystemExit(1)


def _gcloud(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_gcloud_bin(), *args],
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
    )


def _fail(message: str, proc: subprocess.CompletedProcess[str] | None = None) -> None:
    print(message, file=sys.stderr)
    if proc is not None and proc.stderr:
        print(proc.stderr.strip(), file=sys.stderr)
    raise SystemExit(1)


def _read_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        values[key.strip()] = raw.strip().strip("'").strip('"')
    return values


def _project_id(cli_project: str | None) -> str:
    env = _read_dotenv()
    return (
        cli_project
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or env.get("GOOGLE_CLOUD_PROJECT")
        or DEFAULT_PROJECT
    ).strip()


def _agent_engine_env() -> tuple[str | None, str | None]:
    env = _read_dotenv()
    engine_id = (
        os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
        or env.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
        or ""
    ).strip()
    engine_location = (
        os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
        or os.environ.get("MEMORY_BANK_LOCATION")
        or env.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
        or env.get("MEMORY_BANK_LOCATION")
        or ""
    ).strip()
    return engine_id or None, engine_location or None


def _cloud_sql_env() -> tuple[str | None, str, str]:
    env = _read_dotenv()
    instance = (
        os.environ.get("CLOUD_SQL_INSTANCE") or env.get("CLOUD_SQL_INSTANCE") or ""
    ).strip()
    user = (
        os.environ.get("SESSION_DB_USER")
        or env.get("SESSION_DB_USER")
        or DEFAULT_SESSION_DB_USER
    ).strip()
    database = (
        os.environ.get("SESSION_DB_NAME")
        or env.get("SESSION_DB_NAME")
        or DEFAULT_SESSION_DB_NAME
    ).strip()
    return instance or None, user, database


def _print_plan(project: str, region: str) -> None:
    engine_id, engine_location = _agent_engine_env()
    cloud_sql, session_user, session_db = _cloud_sql_env()
    print("Cloud Run two-stage plan (no secrets printed)")
    print(f"  project:  {project}")
    print(f"  region:   {region}")
    print(f"  services: {AGENT_SERVICE}, {TELEGRAM_SERVICE}")
    print(f"  ADK app:  {ADK_APP_NAME}")
    print("  webhook:  <SERVICE_URL>/<TELEGRAM_BOT_TOKEN>")
    if engine_id:
        print(f"  Memory Bank engine: {engine_id}")
        if engine_location:
            print(f"  Memory Bank location: {engine_location}")
    if cloud_sql:
        print(f"  Cloud SQL sessions: {cloud_sql}")
        print(f"  Session DB: {session_user}@{session_db}")
        print("  SESSION_DB_PASSWORD from Secret Manager (not printed)")
    elif engine_id:
        print("  Sessions: Agent Engine (GOOGLE_CLOUD_AGENT_ENGINE_ID)")
    else:
        print(
            "  Sessions: Cloud Run will refuse in-memory; "
            "set CLOUD_SQL_INSTANCE or GOOGLE_CLOUD_AGENT_ENGINE_ID"
        )
        print("  Provision: uv run python scripts/setup_cloud_sql.py")
    print()
    print(
        "1. gcloud",
        *agent_deploy_args(
            project=project,
            region=region,
            agent_engine_id=engine_id,
            agent_engine_location=engine_location,
            cloud_sql_instance=cloud_sql,
            session_db_user=session_user,
            session_db_name=session_db,
        ),
    )
    print()
    print(
        "2. gcloud",
        *telegram_deploy_args(
            project=project,
            region=region,
            adk_server_url="https://<tea-agent-url>",
            service_url=PLACEHOLDER_SERVICE_URL,
        ),
    )
    print()
    print(
        "3. gcloud",
        *telegram_update_env_args(
            project=project,
            region=region,
            adk_server_url="https://<tea-agent-url>",
            service_url="https://<telegram-integration-url>",
        ),
    )
    print()
    print("Dry-run only. Pass --execute after explicit approval to deploy.")


def _enable_apis(project: str) -> None:
    proc = _gcloud(
        [
            "services",
            "enable",
            *REQUIRED_APIS,
            f"--project={project}",
            "--quiet",
        ]
    )
    if proc.returncode != 0:
        _fail("Failed to enable Cloud Run / build APIs", proc)


def _project_number(project: str) -> str:
    proc = _gcloud(
        ["projects", "describe", project, "--format=value(projectNumber)"]
    )
    if proc.returncode != 0:
        _fail("Failed to resolve project number", proc)
    number = proc.stdout.strip()
    if not number:
        _fail("Empty project number")
    return number


def _compute_sa(project: str) -> str:
    return f"{_project_number(project)}-compute@developer.gserviceaccount.com"


def _grant_builder_role(project: str) -> None:
    """Cloud Run --source builds as the Compute Engine default SA."""
    member = f"serviceAccount:{_compute_sa(project)}"
    proc = _gcloud(
        [
            "projects",
            "add-iam-policy-binding",
            project,
            f"--member={member}",
            f"--role={BUILDER_ROLE}",
            "--quiet",
        ]
    )
    if proc.returncode != 0:
        _fail(f"Failed to grant {BUILDER_ROLE}", proc)
    print(f"Granted {BUILDER_ROLE} to compute default SA")


def _grant_secret_access(project: str, extra: tuple[str, ...] = ()) -> None:
    member = f"serviceAccount:{_compute_sa(project)}"
    for secret in (*SECRETS, *extra):
        proc = _gcloud(
            [
                "secrets",
                "add-iam-policy-binding",
                secret,
                f"--project={project}",
                f"--member={member}",
                "--role=roles/secretmanager.secretAccessor",
                "--quiet",
            ]
        )
        if proc.returncode != 0:
            _fail(f"Failed to grant secretAccessor on {secret}", proc)


def _grant_cloudsql_client(project: str) -> None:
    member = f"serviceAccount:{_compute_sa(project)}"
    proc = _gcloud(
        [
            "projects",
            "add-iam-policy-binding",
            project,
            f"--member={member}",
            f"--role={CLOUD_SQL_CLIENT_ROLE}",
            "--quiet",
        ]
    )
    if proc.returncode != 0:
        _fail(f"Failed to grant {CLOUD_SQL_CLIENT_ROLE}", proc)
    print(f"Granted {CLOUD_SQL_CLIENT_ROLE} to compute default SA")


def _run_step(label: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    print(f"{label}: gcloud {' '.join(args)}")
    proc = _gcloud(args)
    if proc.returncode != 0:
        _fail(f"{label} failed", proc)
    if proc.stdout.strip():
        print(proc.stdout.strip())
    return proc


def _service_url(project: str, region: str, service: str) -> str:
    proc = _gcloud(
        [
            "run",
            "services",
            "describe",
            service,
            f"--project={project}",
            f"--region={region}",
            "--format=value(status.url)",
        ]
    )
    if proc.returncode != 0:
        _fail(f"Failed to describe {service}", proc)
    url = proc.stdout.strip()
    if not url:
        _fail(f"Empty URL for {service}")
    return url


def _execute(project: str, region: str) -> None:
    print(f"Using GCP project {project}")
    print(f"Cloud Run region {region}")
    _gcloud(["config", "set", "project", project])
    _gcloud(["config", "set", "run/region", region])
    _enable_apis(project)
    _grant_builder_role(project)
    engine_id, engine_location = _agent_engine_env()
    cloud_sql, session_user, session_db = _cloud_sql_env()
    extra_secrets = ("SESSION_DB_PASSWORD",) if cloud_sql else ()
    _grant_secret_access(project, extra_secrets)
    if cloud_sql:
        _grant_cloudsql_client(project)

    _run_step(
        "tea-agent",
        agent_deploy_args(
            project=project,
            region=region,
            agent_engine_id=engine_id,
            agent_engine_location=engine_location,
            cloud_sql_instance=cloud_sql,
            session_db_user=session_user,
            session_db_name=session_db,
        ),
    )
    agent_url = _service_url(project, region, AGENT_SERVICE)
    print(f"tea-agent URL: {agent_url}")

    _run_step(
        "telegram-integration stage 1 (placeholder SERVICE_URL)",
        telegram_deploy_args(
            project=project,
            region=region,
            adk_server_url=agent_url,
            service_url=PLACEHOLDER_SERVICE_URL,
        ),
    )
    telegram_url = _service_url(project, region, TELEGRAM_SERVICE)
    print(f"telegram-integration URL: {telegram_url}")

    _run_step(
        "telegram-integration stage 2 (real SERVICE_URL)",
        telegram_update_env_args(
            project=project,
            region=region,
            adk_server_url=agent_url,
            service_url=telegram_url,
        ),
    )
    print("Deploy finished. Webhook path is <SERVICE_URL>/<TELEGRAM_BOT_TOKEN>.")
    print("Send /start in Telegram. Do not run local polling at the same time.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=None, help="GCP project id")
    parser.add_argument("--region", default=CLOUD_RUN_REGION)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually deploy. Default is dry-run.",
    )
    args = parser.parse_args()
    project = _project_id(args.project)
    if not args.execute:
        _print_plan(project, args.region)
        return
    _execute(project, args.region)


if __name__ == "__main__":
    main()
