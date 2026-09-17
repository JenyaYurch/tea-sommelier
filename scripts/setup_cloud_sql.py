"""Provision Cloud SQL Postgres for ADK DatabaseSessionService (TEA-14).

Does not create anything unless you pass --execute (explicit approval).
Cloud Run stays in europe-central2; the instance is created in the same region
so the unix socket from ``--set-cloudsql-instances`` works.

Usage:
    uv run python scripts/setup_cloud_sql.py
    uv run python scripts/setup_cloud_sql.py --execute
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from telegram_integration.deploy_spec import (
    CLOUD_RUN_REGION,
    CLOUD_SQL_INSTANCE_NAME,
    DEFAULT_PROJECT,
    DEFAULT_SESSION_DB_NAME,
    DEFAULT_SESSION_DB_USER,
)

ROOT = Path(__file__).resolve().parents[1]
SECRET_NAME = "SESSION_DB_PASSWORD"
REQUIRED_APIS = ("sqladmin.googleapis.com", "secretmanager.googleapis.com")
TIER = "db-f1-micro"
POSTGRES = "POSTGRES_17"


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
        cwd=ROOT,
    )


def _fail(message: str, proc: subprocess.CompletedProcess[str] | None = None) -> None:
    print(message, file=sys.stderr)
    if proc is not None and proc.stderr:
        print(proc.stderr.strip(), file=sys.stderr)
    raise SystemExit(1)


def _project_id(cli_project: str | None) -> str:
    env = _read_dotenv()
    return (
        cli_project
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or env.get("GOOGLE_CLOUD_PROJECT")
        or DEFAULT_PROJECT
    ).strip()


def instance_connection_name(project: str, region: str, instance: str) -> str:
    return f"{project}:{region}:{instance}"


def _print_env_lines(project: str, region: str, instance: str, user: str, database: str) -> None:
    conn = instance_connection_name(project, region, instance)
    print()
    print("Put these in .env (not git) and redeploy tea-agent:")
    print(f"CLOUD_SQL_INSTANCE={conn}")
    print(f"SESSION_DB_USER={user}")
    print(f"SESSION_DB_NAME={database}")
    print("SESSION_DB_PASSWORD lives in Secret Manager (not printed).")
    print("Local without Cloud SQL:")
    print("SESSION_SERVICE_URI=sqlite+aiosqlite:///./sessions.db")


def _print_plan(
    project: str,
    region: str,
    instance: str,
    user: str,
    database: str,
) -> None:
    conn = instance_connection_name(project, region, instance)
    print("Cloud SQL session store plan (no secrets printed)")
    print(f"  project:   {project}")
    print(f"  region:    {region}")
    print(f"  instance:  {instance} ({TIER}, {POSTGRES}, zonal)")
    print(f"  database:  {database}")
    print(f"  db user:   {user}")
    print(f"  connection:{conn}")
    print(f"  secret:    {SECRET_NAME}")
    print()
    print("Creates or reuses a Cloud SQL Postgres instance for DatabaseSessionService.")
    print("Does not deploy Cloud Run. After --execute, set CLOUD_SQL_INSTANCE and redeploy.")
    print("Dry-run only. Pass --execute after explicit approval to create/update.")


def _secret_exists(project: str, name: str) -> bool:
    proc = _gcloud(
        ["secrets", "describe", name, f"--project={project}", "--format=value(name)"]
    )
    return proc.returncode == 0


def _upsert_secret(project: str, name: str, value: str) -> None:
    if _secret_exists(project, name):
        proc = _gcloud(
            ["secrets", "versions", "add", name, f"--project={project}", "--data-file=-"],
            stdin=value,
        )
        if proc.returncode != 0:
            _fail(f"Failed to add a new version of {name}", proc)
        print(f"{name}: updated (value not printed)")
        return
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
    print(f"{name}: created (value not printed)")


def _instance_exists(project: str, instance: str) -> bool:
    proc = _gcloud(
        [
            "sql",
            "instances",
            "describe",
            instance,
            f"--project={project}",
            "--format=value(name)",
        ]
    )
    return proc.returncode == 0


def _database_exists(project: str, instance: str, database: str) -> bool:
    proc = _gcloud(
        [
            "sql",
            "databases",
            "describe",
            database,
            f"--instance={instance}",
            f"--project={project}",
            "--format=value(name)",
        ]
    )
    return proc.returncode == 0


def _user_exists(project: str, instance: str, user: str) -> bool:
    proc = _gcloud(
        [
            "sql",
            "users",
            "list",
            f"--instance={instance}",
            f"--project={project}",
            "--format=value(name)",
        ]
    )
    if proc.returncode != 0:
        _fail("Failed to list Cloud SQL users", proc)
    names = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return user in names


def _execute(
    project: str,
    region: str,
    instance: str,
    user: str,
    database: str,
) -> None:
    print(f"Using GCP project {project}")
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
        _fail("Failed to enable Cloud SQL / Secret Manager APIs", proc)

    password = secrets.token_urlsafe(24)
    if not _instance_exists(project, instance):
        print(f"Creating Cloud SQL instance {instance} (this can take several minutes)")
        proc = _gcloud(
            [
                "sql",
                "instances",
                "create",
                instance,
                f"--database-version={POSTGRES}",
                "--edition=ENTERPRISE",
                f"--region={region}",
                "--availability-type=ZONAL",
                f"--project={project}",
                f"--tier={TIER}",
                f"--root-password={password}",
                "--quiet",
            ]
        )
        if proc.returncode != 0:
            _fail("Failed to create Cloud SQL instance", proc)
        print(f"Created instance {instance}")
    else:
        print(f"Reusing existing instance {instance}")

    if not _database_exists(project, instance, database):
        proc = _gcloud(
            [
                "sql",
                "databases",
                "create",
                database,
                f"--instance={instance}",
                f"--project={project}",
                "--quiet",
            ]
        )
        if proc.returncode != 0:
            _fail(f"Failed to create database {database}", proc)
        print(f"Created database {database}")
    else:
        print(f"Reusing database {database}")

    if not _user_exists(project, instance, user):
        proc = _gcloud(
            [
                "sql",
                "users",
                "create",
                user,
                f"--instance={instance}",
                f"--project={project}",
                f"--password={password}",
                "--quiet",
            ]
        )
        if proc.returncode != 0:
            _fail(f"Failed to create database user {user}", proc)
        print(f"Created database user {user}")
        _upsert_secret(project, SECRET_NAME, password)
    else:
        print(f"Reusing database user {user} (password not rotated)")
        if not _secret_exists(project, SECRET_NAME):
            proc = _gcloud(
                [
                    "sql",
                    "users",
                    "set-password",
                    user,
                    f"--instance={instance}",
                    f"--project={project}",
                    f"--password={password}",
                    "--quiet",
                ]
            )
            if proc.returncode != 0:
                _fail(f"Failed to set password for {user}", proc)
            _upsert_secret(project, SECRET_NAME, password)

    _print_env_lines(project, region, instance, user, database)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=None, help="GCP project id")
    parser.add_argument("--region", default=CLOUD_RUN_REGION)
    parser.add_argument("--instance", default=CLOUD_SQL_INSTANCE_NAME)
    parser.add_argument("--db-user", default=DEFAULT_SESSION_DB_USER)
    parser.add_argument("--db-name", default=DEFAULT_SESSION_DB_NAME)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create/update. Default is dry-run.",
    )
    args = parser.parse_args()
    project = _project_id(args.project)
    if not args.execute:
        _print_plan(project, args.region, args.instance, args.db_user, args.db_name)
        return
    _execute(project, args.region, args.instance, args.db_user, args.db_name)


if __name__ == "__main__":
    main()
