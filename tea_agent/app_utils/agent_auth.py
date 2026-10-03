"""Shared-secret gate for the tea-agent HTTP API (TEA-34).

Cloud Run stays reachable at the proxy (``--allow-unauthenticated``) so the
ADK ``/health`` route can stay open for probes. Every other route requires
``X-Tea-Agent-Token`` to match ``TEA_AGENT_AUTH_SECRET`` (Secret Manager on
Cloud Run). Comparison is constant-time and does not reveal the secret length.

On Cloud Run (``K_SERVICE``) a missing secret fails closed: non-health routes
return 401. Local dev with the secret unset leaves the API and the ADK dev UI
open. Set the secret locally only when you want to exercise the check.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

from tea_agent.app_utils.session_uri import CLOUD_RUN_SERVICE_ENV

AUTH_HEADER = "X-Tea-Agent-Token"
AUTH_SECRET_ENV = "TEA_AGENT_AUTH_SECRET"
DEV_UI_ENV = "TEA_AGENT_DEV_UI"
_PUBLIC_PATHS = frozenset({"/health"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_log = logging.getLogger(__name__)


def dev_ui_enabled() -> bool:
    """ADK dev UI on for local runs, off when Cloud Run or the flag says so."""
    raw = os.getenv(DEV_UI_ENV)
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in _TRUTHY
    return not _on_cloud_run()


def expected_secret() -> str:
    return (os.getenv(AUTH_SECRET_ENV) or "").strip()


def auth_required() -> bool:
    """Require the header on Cloud Run, and locally whenever a secret is set."""
    if _on_cloud_run():
        return True
    return bool(expected_secret())


def secret_matches(provided: str | None) -> bool:
    """True when ``provided`` equals the configured secret.

    Empty secret never matches (fail closed). Both sides are hashed so a
    length mismatch does not short-circuit ``compare_digest``.
    """
    expected = expected_secret()
    if not expected:
        return False
    given = (provided or "").strip()
    return hmac.compare_digest(
        hashlib.sha256(given.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def request_headers(secret: str | None = None) -> dict[str, str]:
    """Header map for callers. ``None`` reads the env; ``""`` sends nothing."""
    if secret is None:
        value = expected_secret()
    else:
        value = secret.strip()
    if not value:
        return {}
    return {AUTH_HEADER: value}


def is_public_path(path: str) -> bool:
    normalized = (path or "/").split("?", 1)[0]
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized in _PUBLIC_PATHS


def header_from_scope(scope: dict, name: str) -> str:
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or []:
        if key.lower() == wanted:
            return value.decode("latin-1")
    return ""


class AgentAuthMiddleware:
    """Reject unauthenticated API calls before they reach ADK or Gemini."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        if is_public_path(path) or not auth_required():
            await self.app(scope, receive, send)
            return
        if secret_matches(header_from_scope(scope, AUTH_HEADER)):
            await self.app(scope, receive, send)
            return
        _log.warning("rejected tea-agent request path=%s", path)
        body = b'{"detail":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _on_cloud_run() -> bool:
    return bool((os.getenv(CLOUD_RUN_SERVICE_ENV) or "").strip())
