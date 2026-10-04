"""Log-based metrics and alert policies for the TeaBot pilot (TEA-38).

Dry-run prints the plan and leaves GCP unchanged. ``--execute`` creates or
updates the metrics, one email notification channel, and the alert policies.
It does not deploy Cloud Run and does not read secret values.

``telegram_integration.main._log_agent_failure`` writes this stdlib line
(``basicConfig`` format ``%(asctime)s %(levelname)s %(name)s: %(message)s``):

    agent failed for telegram user <id> error_code=TEA_AGENT_ERROR status=<code>
    agent failed for telegram user <id> error_code=TEA_QUOTA status=<code>

``TEA_QUOTA`` is ``logger.warning``. ``TEA_AGENT_ERROR`` is ``logger.exception``.
Cloud Run stores that stdout line in ``textPayload``. A JSON stdout line or a
``log_struct`` record stores the same words in ``jsonPayload.message`` and may
set ``jsonPayload.error_code``. The log filters match both shapes.

Thresholds for a 5-person pilot, alignment window 5 minutes:

* ``TEA_AGENT_ERROR``: any matching log (5-minute sum > 0)
* ``TEA_QUOTA``: any matching log (5-minute sum > 0)
* HTTP 5xx on ``tea-agent`` request logs: more than 2 (5-minute sum > 2)
* HTTP 5xx on ``telegram-integration`` request logs: more than 2

Usage:
    uv run python scripts/setup_alerting.py --project=gen-lang-client-0393777014
    uv run python scripts/setup_alerting.py --project=gen-lang-client-0393777014 \\
        --notify-email=you@example.com --execute
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from telegram_integration.adk_client import TEA_AGENT_ERROR, TEA_QUOTA
from telegram_integration.deploy_spec import (
    AGENT_SERVICE,
    CLOUD_RUN_REGION,
    DEFAULT_PROJECT,
    TELEGRAM_SERVICE,
)

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_APIS = ("logging.googleapis.com", "monitoring.googleapis.com")
ALERT_EMAIL_ENV = "TEA_ALERT_EMAIL"
CHANNEL_DISPLAY_NAME = "TeaBot email"
ALIGNMENT_PERIOD = "300s"
ANY_COUNT_THRESHOLD = 0
HTTP_5XX_THRESHOLD = 2
REQUEST_LOG_ID = "run.googleapis.com/requests"

# Kept in lockstep with telegram_integration/main.py (_log_agent_failure).
EMITTED_LOG_MESSAGE = "agent failed for telegram user %s error_code=%s%s"
FORCE_TEXT_PAYLOAD = EMITTED_LOG_MESSAGE % (0, TEA_AGENT_ERROR, " status=500")

_TOKEN = re.compile(
    r"(?P<ws>\s+)"
    r"|(?P<kw>AND|OR|NOT)\b"
    r"|(?P<paren>[()])"
    r"|(?P<op>>=|<=|!=|=|:)"
    r'|(?P<str>"(?:\\.|[^"\\])*")'
    r"|(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<id>[A-Za-z_][\w.]*)"
    r"|(?P<cmp>[<>])"
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class AlertSpec:
    metric_name: str
    display_name: str
    description: str
    log_filter: str
    threshold: float
    condition_name: str
    documentation: str


def error_marker(code: str) -> str:
    return f"error_code={code}"


def error_log_filter(code: str, *, service: str, region: str) -> str:
    """Logging filter for one TEA_* marker on one Cloud Run service."""
    marker = error_marker(code)
    return (
        'resource.type="cloud_run_revision" '
        f'resource.labels.service_name="{service}" '
        f'resource.labels.location="{region}" '
        f'(textPayload:"{marker}" OR '
        f'jsonPayload.message:"{marker}" OR '
        f'jsonPayload.error_code="{code}")'
    )


def http_5xx_log_filter(service: str, *, region: str) -> str:
    """Cloud Run request logs with HTTP status 500-599."""
    return (
        'resource.type="cloud_run_revision" '
        f'resource.labels.service_name="{service}" '
        f'resource.labels.location="{region}" '
        "httpRequest.status>=500 httpRequest.status<600 "
        f'log_id("{REQUEST_LOG_ID}")'
    )


def alert_specs(region: str) -> list[AlertSpec]:
    agent_error = error_log_filter(
        TEA_AGENT_ERROR, service=TELEGRAM_SERVICE, region=region
    )
    quota = error_log_filter(TEA_QUOTA, service=TELEGRAM_SERVICE, region=region)
    agent_5xx = http_5xx_log_filter(AGENT_SERVICE, region=region)
    telegram_5xx = http_5xx_log_filter(TELEGRAM_SERVICE, region=region)
    return [
        AlertSpec(
            metric_name="tea_agent_error_count",
            display_name="TeaBot TEA_AGENT_ERROR",
            description=(
                "telegram-integration lines with error_code=TEA_AGENT_ERROR "
                "in textPayload or jsonPayload. Pilot page: any event in 5 minutes."
            ),
            log_filter=agent_error,
            threshold=ANY_COUNT_THRESHOLD,
            condition_name="Any TEA_AGENT_ERROR in 5 minutes",
            documentation=(
                "telegram-integration logged error_code=TEA_AGENT_ERROR "
                "(stdout textPayload, or jsonPayload.message / jsonPayload.error_code). "
                "Threshold: any matching log in 5 minutes. "
                "Force and verify steps are in docs/HOW_TO.md.\n\n"
                f"Log filter: {agent_error}"
            ),
        ),
        AlertSpec(
            metric_name="tea_quota_count",
            display_name="TeaBot TEA_QUOTA",
            description=(
                "telegram-integration lines with error_code=TEA_QUOTA "
                "in textPayload or jsonPayload. Pilot page: any event in 5 minutes."
            ),
            log_filter=quota,
            threshold=ANY_COUNT_THRESHOLD,
            condition_name="Any TEA_QUOTA in 5 minutes",
            documentation=(
                "telegram-integration logged error_code=TEA_QUOTA. "
                "On a 5-person free-tier pilot one Gemini 429 is enough to email. "
                "Threshold: any matching log in 5 minutes.\n\n"
                f"Log filter: {quota}"
            ),
        ),
        AlertSpec(
            metric_name="tea_agent_5xx_count",
            display_name="TeaBot tea-agent 5xx",
            description=(
                "tea-agent Cloud Run request logs with HTTP status 500-599. "
                "Pilot page: more than 2 in 5 minutes."
            ),
            log_filter=agent_5xx,
            threshold=HTTP_5XX_THRESHOLD,
            condition_name="tea-agent 5xx more than 2 in 5 minutes",
            documentation=(
                "tea-agent returned more than 2 HTTP 5xx responses in 5 minutes "
                "(request log run.googleapis.com/requests). "
                "One cold-start 503 does not page.\n\n"
                f"Log filter: {agent_5xx}"
            ),
        ),
        AlertSpec(
            metric_name="telegram_integration_5xx_count",
            display_name="TeaBot telegram-integration 5xx",
            description=(
                "telegram-integration Cloud Run request logs with HTTP status 500-599. "
                "Pilot page: more than 2 in 5 minutes."
            ),
            log_filter=telegram_5xx,
            threshold=HTTP_5XX_THRESHOLD,
            condition_name="telegram-integration 5xx more than 2 in 5 minutes",
            documentation=(
                "telegram-integration returned more than 2 HTTP 5xx responses "
                "in 5 minutes (request log run.googleapis.com/requests).\n\n"
                f"Log filter: {telegram_5xx}"
            ),
        ),
    ]


def policy_body(spec: AlertSpec, channel: str) -> dict:
    return {
        "displayName": spec.display_name,
        "combiner": "OR",
        "enabled": True,
        "conditions": [
            {
                "displayName": spec.condition_name,
                "conditionThreshold": {
                    "filter": (
                        f'metric.type="logging.googleapis.com/user/{spec.metric_name}" '
                        'AND resource.type="cloud_run_revision"'
                    ),
                    "comparison": "COMPARISON_GT",
                    "thresholdValue": spec.threshold,
                    "duration": "0s",
                    "aggregations": [
                        {
                            "alignmentPeriod": ALIGNMENT_PERIOD,
                            "perSeriesAligner": "ALIGN_SUM",
                            "crossSeriesReducer": "REDUCE_SUM",
                        }
                    ],
                    "trigger": {"count": 1},
                    "evaluationMissingData": "EVALUATION_MISSING_DATA_INACTIVE",
                },
            }
        ],
        "notificationChannels": [channel],
        "documentation": {"content": spec.documentation, "mimeType": "text/markdown"},
        "alertStrategy": {"autoClose": "1800s"},
        "userLabels": {"app": "teabot", "ticket": "tea-38"},
    }


def policy_signature(policy: dict) -> str:
    """Stable comparison so a second --execute skips a no-op policy update."""
    conditions = []
    for condition in policy.get("conditions") or []:
        threshold = condition.get("conditionThreshold") or {}
        aggregations = []
        for agg in threshold.get("aggregations") or []:
            aggregations.append(
                {
                    "alignmentPeriod": agg.get("alignmentPeriod"),
                    "perSeriesAligner": agg.get("perSeriesAligner"),
                    "crossSeriesReducer": agg.get("crossSeriesReducer"),
                }
            )
        conditions.append(
            {
                "displayName": condition.get("displayName"),
                "filter": " ".join(str(threshold.get("filter") or "").split()),
                "comparison": threshold.get("comparison"),
                "thresholdValue": float(threshold.get("thresholdValue")),
                "duration": str(threshold.get("duration") or ""),
                "aggregations": aggregations,
                "evaluationMissingData": threshold.get("evaluationMissingData"),
            }
        )
    slim = {
        "displayName": policy.get("displayName"),
        "combiner": policy.get("combiner"),
        "enabled": policy.get("enabled", True),
        "conditions": conditions,
        "notificationChannels": sorted(policy.get("notificationChannels") or []),
        "documentation": policy.get("documentation"),
        "alertStrategy": policy.get("alertStrategy"),
    }
    return json.dumps(slim, sort_keys=True)


def logging_filter_matches(log_filter: str, entry: dict) -> bool:
    """Evaluate the subset of Logging query language these filters use."""
    parser = _FilterParser(_tokenize(log_filter))
    return bool(parser.parse().eval(entry))


class _Or:
    def __init__(self, left: object, right: object) -> None:
        self.left = left
        self.right = right

    def eval(self, entry: dict) -> bool:
        return self.left.eval(entry) or self.right.eval(entry)


class _And:
    def __init__(self, left: object, right: object) -> None:
        self.left = left
        self.right = right

    def eval(self, entry: dict) -> bool:
        return self.left.eval(entry) and self.right.eval(entry)


class _Not:
    def __init__(self, node: object) -> None:
        self.node = node

    def eval(self, entry: dict) -> bool:
        return not self.node.eval(entry)


class _Compare:
    def __init__(self, field: str, op: str, expected: str) -> None:
        self.field = field
        self.op = op
        self.expected = expected

    def eval(self, entry: dict) -> bool:
        actual = _lookup(entry, self.field)
        if actual is None:
            return False
        if self.op == ":":
            return self.expected.lower() in str(actual).lower()
        if self.op == "=":
            left = _as_float(actual)
            right = _as_float(self.expected)
            if left is not None and right is not None:
                return left == right
            return str(actual) == self.expected
        if self.op == "!=":
            return str(actual) != self.expected
        left = _as_float(actual)
        right = _as_float(self.expected)
        if left is None or right is None:
            return False
        if self.op == ">=":
            return left >= right
        if self.op == "<=":
            return left <= right
        if self.op == ">":
            return left > right
        if self.op == "<":
            return left < right
        return False


class _LogId:
    def __init__(self, value: str) -> None:
        self.value = value

    def eval(self, entry: dict) -> bool:
        return _log_id(entry) == self.value


class _FilterParser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.i = 0

    def _peek(self) -> tuple[str, str]:
        return self.tokens[self.i]

    def _pop(self) -> tuple[str, str]:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def parse(self) -> object:
        node = self._or()
        if self._peek()[0] != "eof":
            raise ValueError(f"trailing token in log filter: {self._peek()!r}")
        return node

    def _or(self) -> object:
        node = self._and()
        while self._peek() == ("kw", "OR"):
            self._pop()
            node = _Or(node, self._and())
        return node

    def _and(self) -> object:
        node = self._unary()
        while self._continues_and():
            if self._peek() == ("kw", "AND"):
                self._pop()
            node = _And(node, self._unary())
        return node

    def _continues_and(self) -> bool:
        kind, value = self._peek()
        if kind == "eof":
            return False
        if kind == "paren" and value == ")":
            return False
        if kind == "kw" and value == "OR":
            return False
        return True

    def _unary(self) -> object:
        if self._peek() == ("kw", "NOT"):
            self._pop()
            return _Not(self._unary())
        if self._peek() == ("paren", "("):
            self._pop()
            node = self._or()
            if self._pop() != ("paren", ")"):
                raise ValueError("log filter is missing ')'")
            return node
        return self._predicate()

    def _predicate(self) -> object:
        kind, value = self._pop()
        if kind != "id":
            raise ValueError(f"expected a field in log filter, got {value!r}")
        if self._peek() == ("paren", "("):
            self._pop()
            arg_kind, arg_val = self._pop()
            if arg_kind != "str" or self._pop() != ("paren", ")"):
                raise ValueError(f"bad function call {value}")
            if value != "log_id":
                raise ValueError(f"unsupported log filter function {value}")
            return _LogId(_unquote(arg_val))
        op_kind, op = self._pop()
        if op_kind not in {"op", "cmp"}:
            raise ValueError(f"expected an operator after {value}")
        val_kind, raw = self._pop()
        if val_kind == "str":
            expected = _unquote(raw)
        elif val_kind == "num":
            expected = raw
        else:
            raise ValueError(f"expected a value after {value} {op}")
        return _Compare(value, op, expected)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise ValueError(f"cannot tokenize log filter at {text[pos : pos + 24]!r}")
        pos = match.end()
        kind = match.lastgroup or ""
        if kind == "ws":
            continue
        tokens.append((kind, match.group(0)))
    tokens.append(("eof", ""))
    return tokens


def _unquote(raw: str) -> str:
    body = raw[1:-1]
    return body.replace(r"\"", '"').replace(r"\\", "\\")


def _lookup(entry: dict, field: str) -> object:
    current: object = entry
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _log_id(entry: dict) -> str:
    name = str(entry.get("logName") or "")
    marker = "/logs/"
    if marker not in name:
        return ""
    return unquote(name.split(marker, 1)[1])


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or (isinstance(value, str) and value.strip() == ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


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


def _json_stdout(proc: subprocess.CompletedProcess[str], label: str) -> object:
    if proc.returncode != 0:
        _fail(f"Failed to {label}", proc)
    text = (proc.stdout or "").strip() or "[]"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        _fail(f"{label} did not return JSON", proc)
        return None


def _as_list(payload: object) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("channels", "notificationChannels", "alertPolicies", "policies"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _print_plan(project: str, region: str, email: str) -> None:
    print("TeaBot alerting plan (no GCP changes)")
    print(f"  project: {project}")
    print(f"  region:  {region}")
    if email:
        print(f"  email:   {email}")
    else:
        print(f"  email:   unset (pass --notify-email or set {ALERT_EMAIL_ENV})")
    print()
    print("Thresholds for a 5-person pilot (alignment 5 minutes):")
    print("  TEA_AGENT_ERROR: any matching log (sum > 0)")
    print("  TEA_QUOTA: any matching log (sum > 0)")
    print("  tea-agent HTTP 5xx: more than 2 request-log responses (sum > 2)")
    print("  telegram-integration HTTP 5xx: more than 2 request-log responses (sum > 2)")
    print()
    print(
        "TEA_* lines are emitted by telegram-integration "
        f"({EMITTED_LOG_MESSAGE!r}). "
        "Filters match textPayload and jsonPayload.message / jsonPayload.error_code."
    )
    print(
        "5xx filters read Cloud Run request logs "
        f"(log_id {REQUEST_LOG_ID}) for {AGENT_SERVICE} and {TELEGRAM_SERVICE}."
    )
    print()
    for spec in alert_specs(region):
        print(f"Metric {spec.metric_name}")
        print(f"  policy: {spec.display_name}")
        print(f"  filter: {spec.log_filter}")
        print()
    print(
        f'Notification channel: email, display name "{CHANNEL_DISPLAY_NAME}". '
        "An existing channel with the same address is reused."
    )
    print("Alert policies are created or updated by display name.")
    print("Google emails a verification link. Alerts stay silent until you click it.")
    print()
    print("IAM for the account that passes --execute:")
    print("  roles/logging.configWriter")
    print("  roles/monitoring.alertPolicyEditor")
    print("  roles/monitoring.notificationChannelEditor")
    print("  roles/serviceusage.serviceUsageAdmin  (only if the APIs are not enabled yet)")
    print()
    print("Dry-run only. Pass --execute after explicit approval to apply.")


def _enable_apis(project: str) -> None:
    proc = _gcloud(
        ["services", "enable", *REQUIRED_APIS, f"--project={project}", "--quiet"]
    )
    if proc.returncode != 0:
        _fail("Failed to enable logging/monitoring APIs", proc)


def _describe_metric(project: str, name: str) -> dict | None:
    proc = _gcloud(
        [
            "logging",
            "metrics",
            "describe",
            name,
            f"--project={project}",
            "--format=json",
        ]
    )
    if proc.returncode != 0:
        blob = f"{proc.stderr or ''}\n{proc.stdout or ''}"
        if "NOT_FOUND" in blob or "not found" in blob.lower():
            return None
        _fail(f"Failed to describe log metric {name}", proc)
    data = json.loads(proc.stdout or "{}")
    if not isinstance(data, dict):
        _fail(f"Unexpected describe payload for log metric {name}")
    return data


def _ensure_metric(project: str, spec: AlertSpec) -> None:
    existing = _describe_metric(project, spec.metric_name)
    wanted = " ".join(spec.log_filter.split())
    if existing is None:
        proc = _gcloud(
            [
                "logging",
                "metrics",
                "create",
                spec.metric_name,
                f"--project={project}",
                f"--description={spec.description}",
                f"--log-filter={spec.log_filter}",
            ]
        )
        if proc.returncode != 0:
            _fail(f"Failed to create log metric {spec.metric_name}", proc)
        print(f"Created log metric {spec.metric_name}")
        return
    current = " ".join(str(existing.get("filter") or "").split())
    if current == wanted and (existing.get("description") or "") == spec.description:
        print(f"Log metric {spec.metric_name} already matches")
        return
    proc = _gcloud(
        [
            "logging",
            "metrics",
            "update",
            spec.metric_name,
            f"--project={project}",
            f"--description={spec.description}",
            f"--log-filter={spec.log_filter}",
        ]
    )
    if proc.returncode != 0:
        _fail(f"Failed to update log metric {spec.metric_name}", proc)
    print(f"Updated log metric {spec.metric_name}")


def _list_channels(project: str) -> list:
    proc = _gcloud(
        ["monitoring", "channels", "list", f"--project={project}", "--format=json"]
    )
    return _as_list(_json_stdout(proc, "list notification channels"))


def _channel_name(stdout: str) -> str:
    text = (stdout or "").strip()
    if text.startswith("{"):
        data = json.loads(text)
        name = str(data.get("name") or "")
        if name:
            return name
    match = re.search(r"projects/\S+/notificationChannels/\S+", text)
    if match is None:
        _fail("Notification channel was created but gcloud did not print its name")
    return match.group(0).rstrip("].,)")


def _ensure_channel(project: str, email: str) -> str:
    wanted = email.lower()
    for channel in _list_channels(project):
        if not isinstance(channel, dict) or channel.get("type") != "email":
            continue
        address = str((channel.get("labels") or {}).get("email_address") or "")
        if address.lower() != wanted:
            continue
        name = str(channel.get("name") or "")
        if not name:
            continue
        if channel.get("enabled") is False:
            proc = _gcloud(
                [
                    "monitoring",
                    "channels",
                    "update",
                    name,
                    f"--project={project}",
                    "--enabled",
                ]
            )
            if proc.returncode != 0:
                _fail(f"Failed to enable notification channel {name}", proc)
        status = channel.get("verificationStatus")
        print(f"Reusing email notification channel {name} ({status or 'status unset'})")
        if status and status != "VERIFIED":
            print(
                f"Open the Google verification mail for {email}. "
                "Alerts do not send until verificationStatus is VERIFIED."
            )
        return name
    proc = _gcloud(
        [
            "monitoring",
            "channels",
            "create",
            f"--project={project}",
            f"--display-name={CHANNEL_DISPLAY_NAME}",
            "--type=email",
            f"--channel-labels=email_address={email}",
            "--description=TeaBot pilot alerts (TEA-38)",
            "--format=json",
        ]
    )
    if proc.returncode != 0:
        _fail("Failed to create email notification channel", proc)
    name = _channel_name(proc.stdout)
    print(f"Created email notification channel {name}")
    print(
        f"Google will email {email} a verification link. "
        "Alerts stay silent until you click it."
    )
    return name


def _list_policies(project: str) -> list:
    proc = _gcloud(
        ["monitoring", "policies", "list", f"--project={project}", "--format=json"]
    )
    return _as_list(_json_stdout(proc, "list alert policies"))


def _write_policy_file(body: dict) -> str:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="teabot-alert-",
        delete=False,
        encoding="utf-8",
    )
    with handle:
        json.dump(body, handle, indent=2)
        handle.write("\n")
    return handle.name


def _ensure_policy(project: str, spec: AlertSpec, channel: str) -> None:
    desired = policy_body(spec, channel)
    matches = [
        policy
        for policy in _list_policies(project)
        if isinstance(policy, dict) and policy.get("displayName") == spec.display_name
    ]
    if len(matches) > 1:
        print(
            f"Found {len(matches)} policies named {spec.display_name}; "
            "updating the first."
        )
    existing = matches[0] if matches else None
    if existing is not None:
        try:
            unchanged = policy_signature(existing) == policy_signature(desired)
        except (TypeError, ValueError):
            unchanged = False
        if unchanged:
            print(f"Alert policy {spec.display_name} already matches")
            return
    body = desired
    command = "create"
    target: list[str] = []
    if existing is not None:
        body = {**desired, "name": existing.get("name")}
        command = "update"
        target = [str(existing.get("name"))]
    path = _write_policy_file(body)
    try:
        proc = _gcloud(
            [
                "monitoring",
                "policies",
                command,
                *target,
                f"--project={project}",
                f"--policy-from-file={path}",
            ]
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        _fail(f"Failed to {command} alert policy {spec.display_name}", proc)
    print(f"{command.title()}d alert policy {spec.display_name}")


def _execute(project: str, region: str, email: str) -> None:
    print(f"Using GCP project {project}")
    print(f"Cloud Run region {region}")
    print(f"Notification email {email}")
    _enable_apis(project)
    channel = _ensure_channel(project, email)
    specs = alert_specs(region)
    for spec in specs:
        _ensure_metric(project, spec)
    for spec in specs:
        _ensure_policy(project, spec, channel)
    print("Alerting is in place.")
    print("Verify with one forced TEA_AGENT_ERROR log (docs/HOW_TO.md).")
    print(f"Confirm the channel for {email} shows verificationStatus VERIFIED.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=None, help="GCP project id")
    parser.add_argument("--region", default=CLOUD_RUN_REGION)
    parser.add_argument(
        "--notify-email",
        default=None,
        help=f"Alert email. Defaults to {ALERT_EMAIL_ENV}. Required with --execute.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Create or update metrics, the email channel, and alert policies.",
    )
    args = parser.parse_args()
    project = _project_id(args.project)
    email = (args.notify_email or os.environ.get(ALERT_EMAIL_ENV) or "").strip()
    if email and not _EMAIL_RE.fullmatch(email):
        _fail("--notify-email must look like an email address.")
    if not args.execute:
        _print_plan(project, args.region, email)
        return
    if not email:
        _fail(
            f"Pass --notify-email or set {ALERT_EMAIL_ENV}. "
            "Dry-run does not need an address."
        )
    _execute(project, args.region, email)


if __name__ == "__main__":
    main()
