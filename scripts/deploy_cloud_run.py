"""Two-stage Cloud Run deploy for tea-agent + telegram-integration (TEA-13/TEA-14).

Does not deploy unless you pass --execute (explicit approval).
If Cloud SQL and Agent Engine are unset, --execute creates Cloud SQL
``tea-sessions``, deploys, then write/restart/check so taste profiles survive.

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
import time
from pathlib import Path

from telegram_integration.deploy_spec import (
    ADK_APP_NAME,
    AGENT_SERVICE,
    CLOUD_RUN_REGION,
    CLOUD_SQL_INSTANCE_NAME,
    DEFAULT_PROJECT,
    DEFAULT_SESSION_DB_NAME,
    DEFAULT_SESSION_DB_USER,
    PLACEHOLDER_SERVICE_URL,
    TELEGRAM_SERVICE,
    agent_deploy_args,
    agent_restart_args,
    telegram_deploy_args,
    telegram_update_env_args,
)
from tea_agent.app_utils.session_uri import normalize_cloud_sql_instance

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
RUN_INVOKER_ROLE = "roles/run.invoker"
IAM_SETTLE_SEC = 30
VERIFY_ATTEMPTS = 5
UNAUTHENTICATED_DENIED_MARKERS = (
    "allusers",
    "allow-unauthenticated",
    "allowedpolicymemberdomains",
    "permitted customer",
    "unauthenticated invocations",
    "domain restriction",
)


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


def _normalized_cloud_sql(project: str, region: str) -> tuple[str | None, str, str]:
    instance, user, database = _cloud_sql_env()
    if instance:
        instance = normalize_cloud_sql_instance(
            instance, project=project, region=region
        )
    return instance or None, user, database


def _load_setup_cloud_sql():
    import importlib.util

    path = ROOT / "scripts" / "setup_cloud_sql.py"
    spec = importlib.util.spec_from_file_location("setup_cloud_sql", path)
    if spec is None or spec.loader is None:
        raise SystemExit("scripts/setup_cloud_sql.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ensure_session_backend(
    project: str, region: str, *, provision: bool
) -> tuple[str | None, str | None, str | None, str, str]:
    """Return engine_id, engine_location, cloud_sql, db user, db name.

    --execute with no backend creates Cloud SQL ``tea-sessions`` so a restart
    cannot silently drop Telegram taste profiles.
    """
    engine_id, engine_location = _agent_engine_env()
    cloud_sql, user, database = _normalized_cloud_sql(project, region)
    if cloud_sql or engine_id:
        return engine_id, engine_location, cloud_sql, user, database
    if not provision:
        return None, None, None, user, database
    setup = _load_setup_cloud_sql()
    instance_name = setup.CLOUD_SQL_INSTANCE_NAME
    print(
        "No CLOUD_SQL_INSTANCE or GOOGLE_CLOUD_AGENT_ENGINE_ID in env; "
        f"provisioning Cloud SQL {instance_name} for DatabaseSessionService."
    )
    setup._execute(project, region, instance_name, user, database)
    conn = setup.instance_connection_name(project, region, instance_name)
    return engine_id, engine_location, conn, user, database


def _print_plan(project: str, region: str) -> None:
    engine_id, engine_location = _agent_engine_env()
    cloud_sql, session_user, session_db = _normalized_cloud_sql(project, region)
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
        cloud_sql = f"{project}:{region}:{CLOUD_SQL_INSTANCE_NAME}"
        print(
            f"  Sessions: --execute will CREATE Cloud SQL {CLOUD_SQL_INSTANCE_NAME} "
            f"({cloud_sql}, POSTGRES_17, db-f1-micro, zonal), then deploy and verify"
        )
        print(f"  Session DB: {session_user}@{session_db}")
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
    print("2. verify_session_persistence.py --write against tea-agent URL")
    print("3. gcloud", *agent_restart_args(project=project, region=region, probe="<ts>"))
    print("4. verify_session_persistence.py --check after the new revision")
    print()
    print(
        "5. gcloud",
        *telegram_deploy_args(
            project=project,
            region=region,
            adk_server_url="https://<tea-agent-url>",
            service_url=PLACEHOLDER_SERVICE_URL,
        ),
    )
    print()
    print(
        "6. gcloud",
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


def _wait_for_iam() -> None:
    """IAM bindings are eventually consistent; the Cloud SQL proxy needs cloudsql.client."""
    print(f"Waiting {IAM_SETTLE_SEC}s for IAM to propagate")
    time.sleep(IAM_SETTLE_SEC)


def _run_step(label: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    print(f"{label}: gcloud {' '.join(args)}")
    proc = _gcloud(args)
    if proc.returncode != 0:
        _fail(f"{label} failed", proc)
    if proc.stdout.strip():
        print(proc.stdout.strip())
    return proc


def _is_unauthenticated_denied(proc: subprocess.CompletedProcess[str]) -> bool:
    """True when org policy / IAM forbids binding allUsers as run.invoker."""
    text = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
    return any(marker in text for marker in UNAUTHENTICATED_DENIED_MARKERS)


def _active_gcloud_account() -> str:
    proc = _gcloud(["config", "get-value", "account"])
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _member_for_account(account: str) -> str:
    value = (account or "").strip()
    if not value or value in {"(unset)", "none"}:
        return ""
    if value.startswith("user:") or value.startswith("serviceAccount:"):
        return value
    if value.endswith(".gserviceaccount.com"):
        return f"serviceAccount:{value}"
    return f"user:{value}"


def _invoker_members(project: str) -> list[str]:
    members: list[str] = []
    seen: set[str] = set()
    for member in (
        f"serviceAccount:{_compute_sa(project)}",
        _member_for_account(_active_gcloud_account()),
    ):
        if not member or member in seen:
            continue
        seen.add(member)
        members.append(member)
    return members


def _grant_run_invoker(project: str, region: str, service: str) -> None:
    """telegram-integration and --check need run.invoker when allUsers is denied."""
    for member in _invoker_members(project):
        proc = _gcloud(
            [
                "run",
                "services",
                "add-iam-policy-binding",
                service,
                f"--project={project}",
                f"--region={region}",
                f"--member={member}",
                f"--role={RUN_INVOKER_ROLE}",
                "--quiet",
            ]
        )
        if proc.returncode != 0:
            _fail(f"Failed to grant {RUN_INVOKER_ROLE} to {member} on {service}", proc)
        print(f"Granted {RUN_INVOKER_ROLE} to {member} on {service}")


def _deploy_tea_agent(
    project: str,
    region: str,
    *,
    engine_id: str | None,
    engine_location: str | None,
    cloud_sql: str | None,
    session_user: str,
    session_db: str,
) -> None:
    """Public first; retry authenticated if the org policy rejects allUsers."""
    kwargs = {
        "project": project,
        "region": region,
        "agent_engine_id": engine_id,
        "agent_engine_location": engine_location,
        "cloud_sql_instance": cloud_sql,
        "session_db_user": session_user,
        "session_db_name": session_db,
    }
    public_args = agent_deploy_args(**kwargs, allow_unauthenticated=True)
    print(f"tea-agent: gcloud {' '.join(public_args)}")
    proc = _gcloud(public_args)
    if proc.returncode == 0:
        if proc.stdout.strip():
            print(proc.stdout.strip())
        return
    if not _is_unauthenticated_denied(proc):
        _fail("tea-agent failed", proc)
    print("Organization policy denied allUsers on tea-agent; retrying authenticated")
    if proc.stderr:
        print(proc.stderr.strip())
    _run_step(
        "tea-agent (authenticated)",
        agent_deploy_args(**kwargs, allow_unauthenticated=False),
    )


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


def _identity_token(audience: str) -> str:
    """Cloud Run IAM token for --check when allUsers is denied. Token is not printed."""
    proc = _gcloud(
        ["auth", "print-identity-token", f"--audiences={audience.strip().rstrip('/')}"]
    )
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _verify_cli(agent_url: str, *flags: str) -> None:
    last: subprocess.CompletedProcess[str] | None = None
    env = os.environ.copy()
    token = _identity_token(agent_url)
    if token:
        env["CLOUD_RUN_ID_TOKEN"] = token
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "verify_session_persistence.py"),
                "--base-url",
                agent_url,
                *flags,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        if proc.stdout.strip():
            print(proc.stdout.strip())
        if proc.returncode == 0:
            return
        last = proc
        print(
            f"Session persistence {flags} attempt {attempt}/{VERIFY_ATTEMPTS} failed; retrying"
        )
        time.sleep(2 * attempt)
    _fail("Session persistence verify failed", last)


def _verify_after_deploy(project: str, region: str, agent_url: str) -> None:
    """Write a Telegram-shaped profile, replace the revision, then check it."""
    print("TEA-14: writing probe session")
    _verify_cli(agent_url, "--write")
    probe = str(int(time.time()))
    _run_step(
        "restart tea-agent (new revision)",
        agent_restart_args(project=project, region=region, probe=probe),
    )
    print("TEA-14: checking probe session after revision")
    _verify_cli(agent_url, "--check")
    print("TEA-14: taste profile survived Cloud Run revision restart")


def _execute(project: str, region: str, *, skip_verify: bool = False) -> None:
    print(f"Using GCP project {project}")
    print(f"Cloud Run region {region}")
    _gcloud(["config", "set", "project", project])
    _gcloud(["config", "set", "run/region", region])
    _enable_apis(project)
    _grant_builder_role(project)
    engine_id, engine_location, cloud_sql, session_user, session_db = (
        _ensure_session_backend(project, region, provision=True)
    )
    if not cloud_sql and not engine_id:
        _fail("Session backend missing after Cloud SQL provision")
    extra_secrets = ("SESSION_DB_PASSWORD",) if cloud_sql else ()
    _grant_secret_access(project, extra_secrets)
    if cloud_sql:
        _grant_cloudsql_client(project)
        _wait_for_iam()

    _deploy_tea_agent(
        project,
        region,
        engine_id=engine_id,
        engine_location=engine_location,
        cloud_sql=cloud_sql,
        session_user=session_user,
        session_db=session_db,
    )
    _grant_run_invoker(project, region, AGENT_SERVICE)
    agent_url = _service_url(project, region, AGENT_SERVICE)
    print(f"tea-agent URL: {agent_url}")
    if skip_verify:
        print("Skipping TEA-14 session verify (--skip-verify).")
    else:
        # Prove Cloud SQL/Agent Engine before Telegram deploy, so a missing
        # TELEGRAM_BOT_TOKEN cannot skip the restart check.
        _verify_after_deploy(project, region, agent_url)

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
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Do not write/restart/check a probe session after --execute.",
    )
    args = parser.parse_args()
    project = _project_id(args.project)
    if not args.execute:
        _print_plan(project, args.region)
        return
    _execute(project, args.region, skip_verify=args.skip_verify)


if __name__ == "__main__":
    main()
