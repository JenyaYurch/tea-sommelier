"""TEA-38 log filters, thresholds, and idempotent alerting apply."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAIN_PY = ROOT / "telegram_integration" / "main.py"
HOW_TO = ROOT / "docs" / "HOW_TO.md"


def _load():
    spec = importlib.util.spec_from_file_location(
        "setup_alerting", ROOT / "scripts" / "setup_alerting.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["setup_alerting"] = module
    spec.loader.exec_module(module)
    return module


def _entry(
    *,
    service: str = "telegram-integration",
    region: str = "europe-central2",
    text: str | None = None,
    payload: dict | None = None,
    status: int | None = None,
    log_name: str | None = None,
) -> dict:
    entry: dict = {
        "resource": {
            "type": "cloud_run_revision",
            "labels": {"service_name": service, "location": region},
        }
    }
    if text is not None:
        entry["textPayload"] = text
    if payload is not None:
        entry["jsonPayload"] = payload
    if status is not None:
        entry["httpRequest"] = {"status": status}
    if log_name is not None:
        entry["logName"] = log_name
    return entry


def _formatted(mod, code: str, *, level: str, status: str) -> str:
    message = mod.EMITTED_LOG_MESSAGE % (42, code, f" status={status}")
    return f"2026-10-04 08:18:00,123 {level} telegram_integration: {message}"


def test_emitted_marker_is_what_main_logs() -> None:
    mod = _load()
    source = MAIN_PY.read_text(encoding="utf-8")
    assert f'message = "{mod.EMITTED_LOG_MESSAGE}"' in source
    assert 'format="%(asctime)s %(levelname)s %(name)s: %(message)s"' in source
    assert "logger.exception(message, *args)" in source
    assert "logger.warning(message, *args, exc_info=err is not None)" in source


def test_error_filter_matches_text_json_and_structured_code() -> None:
    mod = _load()
    log_filter = mod.error_log_filter(
        mod.TEA_AGENT_ERROR, service="telegram-integration", region="europe-central2"
    )
    assert 'textPayload:"error_code=TEA_AGENT_ERROR"' in log_filter
    assert 'jsonPayload.message:"error_code=TEA_AGENT_ERROR"' in log_filter
    assert 'jsonPayload.error_code="TEA_AGENT_ERROR"' in log_filter
    assert "TEA_QUOTA" not in log_filter
    line = _formatted(mod, mod.TEA_AGENT_ERROR, level="ERROR", status="500")
    line_with_trace = line + "\nTraceback (most recent call last):\nRuntimeError: boom"
    assert mod.logging_filter_matches(log_filter, _entry(text=line_with_trace))
    assert mod.logging_filter_matches(
        log_filter, _entry(text=mod.FORCE_TEXT_PAYLOAD)
    )
    assert mod.logging_filter_matches(
        log_filter, _entry(payload={"message": mod.FORCE_TEXT_PAYLOAD})
    )
    assert mod.logging_filter_matches(
        log_filter, _entry(payload={"error_code": "TEA_AGENT_ERROR"})
    )


def test_error_filter_rejects_other_codes_services_and_feedback() -> None:
    mod = _load()
    log_filter = mod.error_log_filter(
        mod.TEA_AGENT_ERROR, service="telegram-integration", region="europe-central2"
    )
    quota_line = _formatted(mod, mod.TEA_QUOTA, level="WARNING", status="429")
    assert not mod.logging_filter_matches(log_filter, _entry(text=quota_line))
    assert not mod.logging_filter_matches(
        log_filter,
        _entry(service="tea-agent", text=mod.FORCE_TEXT_PAYLOAD),
    )
    assert not mod.logging_filter_matches(
        log_filter,
        _entry(region="us-central1", text=mod.FORCE_TEXT_PAYLOAD),
    )
    assert not mod.logging_filter_matches(
        log_filter,
        _entry(
            payload={"log_type": "feedback", "text": "error_code=TEA_AGENT_ERROR"},
            log_name="projects/p/logs/tea-sommelier.feedback",
        ),
    )


def test_quota_filter_matches_warning_line_only() -> None:
    mod = _load()
    log_filter = mod.error_log_filter(
        mod.TEA_QUOTA, service="telegram-integration", region="europe-central2"
    )
    quota_line = _formatted(mod, mod.TEA_QUOTA, level="WARNING", status="429")
    bare = mod.EMITTED_LOG_MESSAGE % (7, mod.TEA_QUOTA, "")
    assert mod.logging_filter_matches(log_filter, _entry(text=quota_line))
    assert mod.logging_filter_matches(log_filter, _entry(payload={"message": bare}))
    assert not mod.logging_filter_matches(
        log_filter, _entry(text=mod.FORCE_TEXT_PAYLOAD)
    )


def test_5xx_filter_is_request_logs_above_a_small_count() -> None:
    mod = _load()
    log_filter = mod.http_5xx_log_filter("tea-agent", region="europe-central2")
    request_log = (
        "projects/gen-lang-client-0393777014/logs/run.googleapis.com%2Frequests"
    )
    stderr_log = (
        "projects/gen-lang-client-0393777014/logs/run.googleapis.com%2Fstderr"
    )
    assert mod.logging_filter_matches(
        log_filter, _entry(service="tea-agent", status=500, log_name=request_log)
    )
    assert mod.logging_filter_matches(
        log_filter, _entry(service="tea-agent", status=599, log_name=request_log)
    )
    assert not mod.logging_filter_matches(
        log_filter, _entry(service="tea-agent", status=499, log_name=request_log)
    )
    assert not mod.logging_filter_matches(
        log_filter, _entry(service="tea-agent", status=401, log_name=request_log)
    )
    assert not mod.logging_filter_matches(
        log_filter, _entry(service="tea-agent", status=600, log_name=request_log)
    )
    assert not mod.logging_filter_matches(
        log_filter,
        _entry(service="telegram-integration", status=500, log_name=request_log),
    )
    assert not mod.logging_filter_matches(
        log_filter,
        _entry(
            service="tea-agent",
            status=500,
            log_name=stderr_log,
            text="error_code=TEA_AGENT_ERROR",
        ),
    )
    assert mod.HTTP_5XX_THRESHOLD == 2
    assert mod.ANY_COUNT_THRESHOLD == 0
    assert mod.ALIGNMENT_PERIOD == "300s"


def test_policy_thresholds_and_channel() -> None:
    mod = _load()
    specs = {spec.metric_name: spec for spec in mod.alert_specs("europe-central2")}
    assert set(specs) == {
        "tea_agent_error_count",
        "tea_quota_count",
        "tea_agent_5xx_count",
        "telegram_integration_5xx_count",
    }
    channel = "projects/demo/notificationChannels/9"
    error = mod.policy_body(specs["tea_agent_error_count"], channel)
    threshold = error["conditions"][0]["conditionThreshold"]
    assert threshold["comparison"] == "COMPARISON_GT"
    assert threshold["thresholdValue"] == 0
    assert threshold["aggregations"][0]["alignmentPeriod"] == "300s"
    assert threshold["aggregations"][0]["perSeriesAligner"] == "ALIGN_SUM"
    assert "tea_agent_error_count" in threshold["filter"]
    assert error["notificationChannels"] == [channel]
    assert error["enabled"] is True
    five = mod.policy_body(specs["tea_agent_5xx_count"], channel)
    assert five["conditions"][0]["conditionThreshold"]["thresholdValue"] == 2
    assert "tea-agent" in specs["tea_agent_5xx_count"].log_filter
    assert "telegram-integration" in specs["telegram_integration_5xx_count"].log_filter
    assert mod.policy_signature(error) == mod.policy_signature(
        json.loads(json.dumps(error))
    )
    changed = json.loads(json.dumps(error))
    changed["conditions"][0]["conditionThreshold"]["thresholdValue"] = 4
    assert mod.policy_signature(error) != mod.policy_signature(changed)


def test_howto_force_line_matches_agent_error_filter() -> None:
    mod = _load()
    howto = HOW_TO.read_text(encoding="utf-8")
    assert mod.FORCE_TEXT_PAYLOAD in howto
    assert "service_name=telegram-integration" in howto
    assert "configuration_name=alert-probe" in howto
    log_filter = mod.error_log_filter(
        mod.TEA_AGENT_ERROR, service="telegram-integration", region="europe-central2"
    )
    assert mod.logging_filter_matches(
        log_filter, _entry(text=mod.FORCE_TEXT_PAYLOAD)
    )
    assert "roles/logging.configWriter" in howto
    assert "roles/monitoring.alertPolicyEditor" in howto
    assert "roles/monitoring.notificationChannelEditor" in howto
    assert "roles/secretmanager.secretAccessor" in howto


def test_dry_run_does_not_call_gcloud(capsys, monkeypatch) -> None:
    mod = _load()

    def boom(args):
        raise AssertionError(args)

    monkeypatch.setattr(mod, "_gcloud", boom)
    monkeypatch.setattr(
        sys, "argv", ["setup_alerting.py", "--project=demo-proj", "--notify-email=ops@example.com"]
    )
    mod.main()
    out = capsys.readouterr().out
    assert "no GCP changes" in out
    assert "error_code=TEA_AGENT_ERROR" in out
    assert "ops@example.com" in out
    assert "Dry-run only" in out


def test_execute_requires_email(monkeypatch) -> None:
    mod = _load()
    monkeypatch.delenv("TEA_ALERT_EMAIL", raising=False)
    monkeypatch.setattr(sys, "argv", ["setup_alerting.py", "--execute", "--project=demo-proj"])
    with pytest.raises(SystemExit):
        mod.main()


class _Proc:
    def __init__(self, code: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


class _FakeGcloud:
    def __init__(self) -> None:
        self.metrics: dict[str, dict] = {}
        self.channels: list[dict] = []
        self.policies: list[dict] = []
        self.calls: list[list[str]] = []
        self._policy_seq = 0
        self._channel_seq = 0

    def __call__(self, args: list[str]) -> _Proc:
        self.calls.append(list(args))
        if args[:2] == ["services", "enable"]:
            return _Proc(0)
        if args[:3] == ["logging", "metrics", "describe"]:
            name = args[3]
            if name not in self.metrics:
                return _Proc(1, stderr=f"NOT_FOUND: {name}")
            return _Proc(0, json.dumps(self.metrics[name]))
        if args[:3] == ["logging", "metrics", "create"]:
            name = args[3]
            self.metrics[name] = {
                "name": name,
                "description": _flag(args, "--description"),
                "filter": _flag(args, "--log-filter"),
            }
            return _Proc(0)
        if args[:3] == ["logging", "metrics", "update"]:
            name = args[3]
            self.metrics[name]["description"] = _flag(args, "--description")
            self.metrics[name]["filter"] = _flag(args, "--log-filter")
            return _Proc(0)
        if args[:3] == ["monitoring", "channels", "list"]:
            return _Proc(0, json.dumps(self.channels))
        if args[:3] == ["monitoring", "channels", "create"]:
            self._channel_seq += 1
            name = f"projects/demo/notificationChannels/{self._channel_seq}"
            email = _flag(args, "--channel-labels").split("=", 1)[1]
            channel = {
                "name": name,
                "type": "email",
                "displayName": _flag(args, "--display-name"),
                "labels": {"email_address": email},
                "enabled": True,
                "verificationStatus": "UNVERIFIED",
            }
            self.channels.append(channel)
            return _Proc(0, json.dumps(channel))
        if args[:3] == ["monitoring", "policies", "list"]:
            return _Proc(0, json.dumps(self.policies))
        if args[:3] == ["monitoring", "policies", "create"]:
            body = json.loads(Path(_flag(args, "--policy-from-file")).read_text())
            self._policy_seq += 1
            stored = {**body, "name": f"projects/demo/alertPolicies/{self._policy_seq}"}
            self.policies.append(stored)
            return _Proc(0, json.dumps(stored))
        if args[:3] == ["monitoring", "policies", "update"]:
            body = json.loads(Path(_flag(args, "--policy-from-file")).read_text())
            target = args[3]
            for index, policy in enumerate(self.policies):
                if policy["name"] == target:
                    self.policies[index] = {**body, "name": policy["name"]}
                    return _Proc(0)
            return _Proc(1, stderr="NOT_FOUND")
        return _Proc(1, stderr=f"unexpected {' '.join(args)}")


def _flag(args: list[str], name: str) -> str:
    prefix = name + "="
    for arg in args:
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    raise AssertionError(name)


def _commands(fake: _FakeGcloud, *prefix: str) -> list[list[str]]:
    return [call for call in fake.calls if call[: len(prefix)] == list(prefix)]


def test_execute_is_idempotent(monkeypatch) -> None:
    mod = _load()
    fake = _FakeGcloud()
    monkeypatch.setattr(mod, "_gcloud", fake)
    mod._execute("demo-proj", "europe-central2", "Ops@Example.com")
    mod._execute("demo-proj", "europe-central2", "ops@example.com")
    assert len(_commands(fake, "logging", "metrics", "create")) == 4
    assert _commands(fake, "logging", "metrics", "update") == []
    assert len(_commands(fake, "monitoring", "channels", "create")) == 1
    assert len(_commands(fake, "monitoring", "policies", "create")) == 4
    assert _commands(fake, "monitoring", "policies", "update") == []
    assert len(fake.channels) == 1
    assert fake.channels[0]["labels"]["email_address"] == "Ops@Example.com"
    assert len(fake.policies) == 4


def test_execute_updates_metric_when_filter_drifts(monkeypatch) -> None:
    mod = _load()
    fake = _FakeGcloud()
    monkeypatch.setattr(mod, "_gcloud", fake)
    mod._execute("demo-proj", "europe-central2", "ops@example.com")
    fake.metrics["tea_agent_error_count"]["filter"] = 'textPayload:"stale"'
    mod._execute("demo-proj", "europe-central2", "ops@example.com")
    updates = _commands(fake, "logging", "metrics", "update")
    assert any(call[3] == "tea_agent_error_count" for call in updates)
    assert "error_code=TEA_AGENT_ERROR" in fake.metrics["tea_agent_error_count"]["filter"]
