"""Two-stage Cloud Run deploy for tea-agent + telegram-integration (TEA-13/TEA-14).

Does not deploy unless you pass --execute (explicit approval).
Default is the cheap path: no Memory Bank, no Cloud SQL. Sessions are
in-memory (``TEA_ALLOW_EPHEMERAL_SESSIONS``). tea-agent is pinned to one
instance (max 1, min 1 by default) so idle scale-to-zero does not wipe them;
a new revision still does. Set ``CLOUD_SQL_INSTANCE`` or
``GOOGLE_CLOUD_AGENT_ENGINE_ID`` to attach a persistent backend instead.

tea-agent requires Secret Manager secret ``TEA_AGENT_AUTH_SECRET`` before
``--execute``. Create it once (docs/HOW_TO.md). Do not commit the value.

After ``--execute``, a smoke test calls tea-agent only: ``GET /health`` is 200,
``POST /run`` without ``X-Tea-Agent-Token`` is 401, then one short ``/run``
with the token (read from Secret Manager, never printed) returns a non-empty
answer. ``--smoke-only`` reruns that check. ``--skip-smoke`` skips it.
The smoke user is not a Telegram id, and telegram-integration is not called.

Usage:
    uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014
    uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute
    uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --smoke-only
    uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute --skip-smoke
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from tea_agent.app_utils.agent_auth import AUTH_HEADER, AUTH_SECRET_ENV, request_headers
from tea_agent.app_utils.session_uri import normalize_cloud_sql_instance
from telegram_integration.adk_client import (
    RUN_TIMEOUT_SEC,
    extract_reply_text,
    normalize_adk_base_url,
)
from telegram_integration.deploy_spec import (
    ADK_APP_NAME,
    AGENT_CONCURRENCY,
    AGENT_MAX_INSTANCES,
    AGENT_SERVICE,
    CLOUD_RUN_REGION,
    DEFAULT_PROJECT,
    DEFAULT_SESSION_DB_NAME,
    DEFAULT_SESSION_DB_USER,
    PLACEHOLDER_SERVICE_URL,
    TELEGRAM_SERVICE,
    agent_deploy_args,
    agent_min_instances,
    agent_restart_args,
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
SECRETS = ("GOOGLE_API_KEY", "TELEGRAM_BOT_TOKEN", AUTH_SECRET_ENV)
CLOUD_SQL_CLIENT_ROLE = "roles/cloudsql.client"
BUILDER_ROLE = "roles/run.builder"
IAM_SETTLE_SEC = 30
VERIFY_ATTEMPTS = 5
# Not a Telegram id. ADK user ids for real chats are tg-<digits>.
SMOKE_USER_ID = "tea-smoke"
SMOKE_SESSION_ID = "tea-smoke-1"
SMOKE_MESSAGE = "Привет"
SMOKE_RUN_TIMEOUT_SEC = RUN_TIMEOUT_SEC
SMOKE_FAST_TIMEOUT_SEC = 20.0
SMOKE_ATTEMPTS = 4
_TRANSIENT_STATUS = frozenset({502, 503, 504})


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

    Default deploy does not create Cloud SQL. Pass provision=True only when
    opting into TEA-14 persistence.
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


def _print_plan(project: str, region: str, *, skip_smoke: bool = False) -> None:
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
    else:
        print("  Memory Bank: off (no GOOGLE_CLOUD_AGENT_ENGINE_ID)")
    if cloud_sql:
        print(f"  Cloud SQL sessions: {cloud_sql}")
        print(f"  Session DB: {session_user}@{session_db}")
        print("  SESSION_DB_PASSWORD from Secret Manager (not printed)")
    elif engine_id:
        print("  Sessions: Agent Engine (GOOGLE_CLOUD_AGENT_ENGINE_ID)")
    else:
        floor = agent_min_instances()
        idle = (
            "Profiles reset on scale-to-zero and on a new revision."
            if floor == "0"
            else (
                "min-instances keeps the process up while idle. "
                "Profiles still reset on a new revision."
            )
        )
        print(
            "  Sessions: in-memory (TEA_ALLOW_EPHEMERAL_SESSIONS). "
            f"tea-agent max-instances={AGENT_MAX_INSTANCES} "
            f"min-instances={floor} concurrency={AGENT_CONCURRENCY}. "
            f"{idle} "
            "Will not create Cloud SQL tea-sessions."
        )
    print(
        "  Access: shared secret "
        f"{AUTH_SECRET_ENV} (header {AUTH_HEADER}). "
        "Create that secret before --execute (docs/HOW_TO.md). "
        "tea-agent stays --allow-unauthenticated so /health is open; "
        "the app returns 401 without the header. ADK dev UI is off."
    )
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
    step = 2
    if cloud_sql or engine_id:
        print()
        print(f"{step}. verify_session_persistence.py --write against tea-agent URL")
        step += 1
        print(
            f"{step}. gcloud",
            *agent_restart_args(project=project, region=region, probe="<ts>"),
        )
        step += 1
        print(f"{step}. verify_session_persistence.py --check after the new revision")
        step += 1
    print()
    print(
        f"{step}. gcloud",
        *telegram_deploy_args(
            project=project,
            region=region,
            adk_server_url="https://<tea-agent-url>",
            service_url=PLACEHOLDER_SERVICE_URL,
        ),
    )
    print()
    print(
        f"{step + 1}. gcloud",
        *telegram_update_env_args(
            project=project,
            region=region,
            adk_server_url="https://<tea-agent-url>",
            service_url="https://<telegram-integration-url>",
        ),
    )
    print()
    print(
        "After deploy, smoke-test tea-agent only "
        f"(user {SMOKE_USER_ID}, session {SMOKE_SESSION_ID}):"
    )
    print("  GET /health -> 200")
    print(f"  POST /run without {AUTH_HEADER} -> 401")
    print(
        f"  POST /run with {AUTH_HEADER} (Secret Manager {AUTH_SECRET_ENV}, "
        "value not printed) -> non-empty answer, then DELETE that session"
    )
    print(
        "Smoke does not call telegram-integration, does not send a Telegram "
        "message, and does not spend a per-user rate limit."
    )
    print("Rerun later with --smoke-only. Skip with --execute --skip-smoke.")
    if skip_smoke:
        print("--skip-smoke is set. --execute will not run the smoke test.")
    print()
    print(
        "--execute replaces telegram-integration's whole env block "
        "(--set-env-vars). Export TELEGRAM_ALLOWED_USER_IDS and "
        "TELEGRAM_ADMIN_USER_IDS again in this shell (semicolons; see "
        "docs/HOW_TO.md) or the new revision comes up closed with an empty allowlist."
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


def _verify_cli(agent_url: str, *flags: str) -> None:
    last: subprocess.CompletedProcess[str] | None = None
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


def _require_auth_secret(project: str) -> None:
    """Fail before deploy when the shared secret has not been created."""
    proc = _gcloud(
        [
            "secrets",
            "describe",
            AUTH_SECRET_ENV,
            f"--project={project}",
            "--format=value(name)",
        ]
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return
    _fail(
        f"Secret {AUTH_SECRET_ENV} is missing in {project}. "
        "Create it once before deploy (value is not stored in git):\n"
        f"  openssl rand -base64 32\n"
        f"  printf '%s' '<value>' | gcloud secrets create {AUTH_SECRET_ENV} "
        f"--project={project} --replication-policy=automatic --data-file=-\n"
        "Then rerun this script with --execute. It grants secretAccessor "
        "to the Compute Engine default service account. See docs/HOW_TO.md."
    )


def _execute(
    project: str, region: str, *, skip_verify: bool = False, skip_smoke: bool = False
) -> None:
    print(f"Using GCP project {project}")
    print(f"Cloud Run region {region}")
    _require_auth_secret(project)
    _gcloud(["config", "set", "project", project])
    _gcloud(["config", "set", "run/region", region])
    _enable_apis(project)
    _grant_builder_role(project)
    engine_id, engine_location, cloud_sql, session_user, session_db = (
        _ensure_session_backend(project, region, provision=False)
    )
    extra_secrets = ("SESSION_DB_PASSWORD",) if cloud_sql else ()
    _grant_secret_access(project, extra_secrets)
    if cloud_sql:
        _grant_cloudsql_client(project)
        _wait_for_iam()

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
    has_persistent = bool(cloud_sql or engine_id)
    if skip_verify or not has_persistent:
        reason = (
            "--skip-verify"
            if skip_verify
            else "in-memory sessions; profiles do not survive restarts"
        )
        print(f"Skipping TEA-14 session verify ({reason}).")
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
    if skip_smoke:
        print("Skipping post-deploy smoke (--skip-smoke).")
    else:
        _run_post_deploy_smoke(project, agent_url)
    print("Deploy finished. Webhook path is <SERVICE_URL>/<TELEGRAM_BOT_TOKEN>.")
    print("Send /start in Telegram. Do not run local polling at the same time.")


def smoke_urls(base_url: str) -> dict[str, str]:
    base = normalize_adk_base_url(base_url)
    session = (
        f"{base}/apps/{ADK_APP_NAME}/users/{SMOKE_USER_ID}/sessions/{SMOKE_SESSION_ID}"
    )
    return {"health": f"{base}/health", "run": f"{base}/run", "session": session}


def _redact(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "[redacted]")


def _snippet(response: httpx.Response, secret: str) -> str:
    text = _redact(response.text or "", secret)
    return " ".join(text.split())[:180]


def _smoke_fail(message: str) -> None:
    print(
        "Post-deploy smoke failed. tea-agent did not pass the chat check. "
        "Fix it before sending /start in Telegram.",
        file=sys.stderr,
    )
    _fail(message)


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    secret: str,
    json_body: object | None = None,
    attempts: int = SMOKE_ATTEMPTS,
    retry_timeouts: bool = True,
) -> httpx.Response:
    path = urlsplit(url).path or url
    last_error = f"{method} {path} failed"
    for attempt in range(1, attempts + 1):
        try:
            response = client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=timeout,
            )
        except httpx.TimeoutException as err:
            last_error = f"{method} {path} timed out ({type(err).__name__})"
            if not retry_timeouts or attempt == attempts:
                _smoke_fail(last_error)
            print(f"{last_error}; retrying")
            time.sleep(min(2 * attempt, 8))
            continue
        except httpx.HTTPError as err:
            detail = _redact(str(err), secret)
            last_error = f"{method} {path} failed ({type(err).__name__}: {detail})"
            if attempt == attempts:
                _smoke_fail(last_error)
            print(f"{last_error}; retrying")
            time.sleep(min(2 * attempt, 8))
            continue
        if response.status_code in _TRANSIENT_STATUS and attempt < attempts:
            print(f"{method} {path} returned {response.status_code}; retrying")
            time.sleep(min(2 * attempt, 8))
            continue
        return response
    _smoke_fail(last_error)
    raise SystemExit(1)


def _access_auth_secret(project: str) -> str:
    """Read TEA_AGENT_AUTH_SECRET the way Cloud Run does. Never print it."""
    proc = _gcloud(
        [
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={AUTH_SECRET_ENV}",
            f"--project={project}",
        ]
    )
    if proc.returncode != 0:
        _fail(
            f"Could not read Secret Manager secret {AUTH_SECRET_ENV}. "
            "The account running deploy needs roles/secretmanager.secretAccessor "
            "on that secret. The value was not printed.",
            proc,
        )
    value = (proc.stdout or "").strip()
    if not value:
        _fail(f"Secret {AUTH_SECRET_ENV} is empty. The value was not printed.")
    return value


def _expect_health(client: httpx.Client, url: str, secret: str) -> None:
    response = _request(
        client,
        "GET",
        url,
        headers={},
        timeout=SMOKE_FAST_TIMEOUT_SEC,
        secret=secret,
    )
    if response.status_code != 200:
        _smoke_fail(f"GET /health returned {response.status_code}, expected 200.")
    print("Smoke GET /health 200")


def _expect_unauthorized(client: httpx.Client, url: str, secret: str) -> None:
    response = _request(
        client,
        "POST",
        url,
        headers={},
        json_body={"appName": ADK_APP_NAME},
        timeout=SMOKE_FAST_TIMEOUT_SEC,
        secret=secret,
    )
    if response.status_code != 401:
        _smoke_fail(
            f"POST /run without {AUTH_HEADER} returned {response.status_code}, "
            f"expected 401. {_snippet(response, secret)}".rstrip()
        )
    print(f"Smoke POST /run without {AUTH_HEADER} 401")


def _ensure_smoke_session(client: httpx.Client, url: str, secret: str) -> None:
    headers = request_headers(secret)
    existing = _request(
        client,
        "GET",
        url,
        headers=headers,
        timeout=SMOKE_FAST_TIMEOUT_SEC,
        secret=secret,
    )
    if existing.status_code == 401:
        _smoke_fail(
            f"Smoke session lookup returned 401. {AUTH_SECRET_ENV} does not match "
            "the revision. The secret value was not printed."
        )
    if existing.status_code == 200:
        print(f"Smoke session {SMOKE_USER_ID}/{SMOKE_SESSION_ID} already exists")
        return
    if existing.status_code not in {404, 422}:
        _smoke_fail(
            "Smoke session lookup returned "
            f"{existing.status_code}, expected 200 or 404. "
            f"{_snippet(existing, secret)}".rstrip()
        )
    created = _request(
        client,
        "POST",
        url,
        headers=headers,
        json_body={},
        timeout=SMOKE_FAST_TIMEOUT_SEC,
        secret=secret,
    )
    if created.status_code == 401:
        _smoke_fail(
            f"Smoke session create returned 401. {AUTH_SECRET_ENV} does not match "
            "the revision. The secret value was not printed."
        )
    if created.status_code not in {200, 201, 409}:
        _smoke_fail(
            "Smoke session create returned "
            f"{created.status_code}, expected 200 or 201. "
            f"{_snippet(created, secret)}".rstrip()
        )
    print(f"Smoke session {SMOKE_USER_ID}/{SMOKE_SESSION_ID} is ready")


def _expect_answer(client: httpx.Client, url: str, secret: str) -> None:
    response = _request(
        client,
        "POST",
        url,
        headers=request_headers(secret),
        json_body={
            "appName": ADK_APP_NAME,
            "userId": SMOKE_USER_ID,
            "sessionId": SMOKE_SESSION_ID,
            "newMessage": {"role": "user", "parts": [{"text": SMOKE_MESSAGE}]},
        },
        timeout=SMOKE_RUN_TIMEOUT_SEC,
        secret=secret,
        attempts=2,
        retry_timeouts=False,
    )
    if response.status_code != 200:
        _smoke_fail(
            f"POST /run with {AUTH_HEADER} returned {response.status_code}, "
            "expected 200 with a non-empty answer. "
            f"{_snippet(response, secret)}".rstrip()
        )
    try:
        payload = response.json()
    except ValueError:
        _smoke_fail("POST /run returned 200 but the body was not JSON.")
    text = extract_reply_text(payload).strip()
    if not text:
        _smoke_fail("POST /run returned 200 but the answer text was empty.")
    preview = _redact(text.replace("\n", " "), secret)[:80]
    print(f"Smoke POST /run returned a non-empty answer ({len(text)} chars): {preview}")


def _delete_smoke_session(client: httpx.Client, url: str, secret: str) -> None:
    """Best-effort DELETE. A failure here must not hide a failed chat check."""
    try:
        response = client.request(
            "DELETE",
            url,
            headers=request_headers(secret),
            timeout=SMOKE_FAST_TIMEOUT_SEC,
        )
    except Exception as err:
        print(
            "Smoke session delete failed "
            f"({type(err).__name__}: {_redact(str(err), secret)}). "
            "The chat check already finished."
        )
        return
    if response.status_code in {200, 202, 204}:
        print(f"Deleted smoke session {SMOKE_USER_ID}/{SMOKE_SESSION_ID}.")
        return
    if response.status_code == 404:
        print(f"Smoke session {SMOKE_USER_ID}/{SMOKE_SESSION_ID} is already gone.")
        return
    print(
        f"Smoke session delete returned {response.status_code}. "
        "Left the session in place. The chat check already finished."
    )


def _smoke_chat(client: httpx.Client, agent_url: str, secret: str) -> None:
    urls = smoke_urls(agent_url)
    _expect_health(client, urls["health"], secret)
    _expect_unauthorized(client, urls["run"], secret)
    session_ready = False
    try:
        _ensure_smoke_session(client, urls["session"], secret)
        session_ready = True
        _expect_answer(client, urls["run"], secret)
    finally:
        if session_ready:
            _delete_smoke_session(client, urls["session"], secret)


def _run_post_deploy_smoke(
    project: str,
    agent_url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    secret: str | None = None,
) -> None:
    token = secret if secret is not None else _access_auth_secret(project)
    with httpx.Client(transport=transport, timeout=SMOKE_RUN_TIMEOUT_SEC) as client:
        _smoke_chat(client, agent_url, token)
    print(
        "Smoke passed. tea-agent answered. "
        f"User {SMOKE_USER_ID} is not a Telegram id. "
        "telegram-integration was not called."
    )


def _smoke_only(project: str, region: str) -> None:
    print(f"Smoke-only against {AGENT_SERVICE} in {project} ({region}). No deploy.")
    agent_url = _service_url(project, region, AGENT_SERVICE)
    print(f"tea-agent URL: {agent_url}")
    _run_post_deploy_smoke(project, agent_url)


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
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Deploy without the tea-agent /health and /run smoke test.",
    )
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Run the tea-agent smoke test against the current service. Does not deploy.",
    )
    args = parser.parse_args()
    project = _project_id(args.project)
    if args.smoke_only and args.execute:
        _fail(
            "--smoke-only checks the current tea-agent and does not deploy. "
            "Remove --execute."
        )
    if args.smoke_only and args.skip_smoke:
        _fail("Use either --smoke-only or --skip-smoke.")
    if args.smoke_only:
        _smoke_only(project, args.region)
        return
    if not args.execute:
        _print_plan(project, args.region, skip_smoke=args.skip_smoke)
        return
    _execute(
        project,
        args.region,
        skip_verify=args.skip_verify,
        skip_smoke=args.skip_smoke,
    )


if __name__ == "__main__":
    main()
