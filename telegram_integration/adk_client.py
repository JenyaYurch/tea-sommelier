"""HTTP client for the tea-agent Cloud Run / ADK FastAPI surface.

Used by Telegram webhook mode. Local polling still runs the agent in-process.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

import httpx

DEFAULT_APP_NAME = "tea_agent"
SESSION_TIMEOUT_SEC = 10.0
RUN_TIMEOUT_SEC = 120.0

TEA_COLD_START = "TEA_COLD_START"
TEA_TIMEOUT_RUN = "TEA_TIMEOUT_RUN"
TEA_UNAVAILABLE = "TEA_UNAVAILABLE"
TEA_QUOTA = "TEA_QUOTA"
TEA_AGENT_ERROR = "TEA_AGENT_ERROR"
TEA_SESSION_FAILED = "TEA_SESSION_FAILED"
TEA_EMPTY_REPLY = "TEA_EMPTY_REPLY"
TEA_UNKNOWN = "TEA_UNKNOWN"

_UNAVAILABLE_STATUS = {502, 503, 504}


class AdkError(RuntimeError):
    """Classified failure talking to the tea-agent backend."""

    error_code: str = TEA_UNKNOWN

    def __init__(
        self,
        message: str = "",
        *,
        error_code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code
        self.status_code = status_code


class AdkQuotaError(AdkError):
    """The agent backend rejected the call with a quota / rate-limit error."""

    error_code = TEA_QUOTA


class AdkTimeoutError(AdkError):
    """httpx timed out on session setup (cold start) or POST /run."""


class AdkUnavailableError(AdkError):
    """Connect failure or HTTP 502/503/504 from the agent backend."""

    error_code = TEA_UNAVAILABLE


class AdkClientError(AdkError):
    """The agent backend returned a non-success status (not quota/unavailable)."""


def normalize_adk_base_url(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if base.endswith("/run"):
        base = base[:-4]
    elif base.endswith("/query"):
        base = base[:-6]
    return base


def extract_reply_text(events: Any) -> str:
    """Join text parts from non-user agent events (skip partials)."""
    if events is None:
        return ""
    if not isinstance(events, list):
        events = [events]
    pieces: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("author") == "user" or event.get("partial"):
            continue
        content = event.get("content") or {}
        parts = content.get("parts") or []
        piece = "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict)
        ).strip()
        if piece:
            pieces.append(piece)
    return "\n\n".join(pieces).strip()


def _looks_like_quota(err: BaseException | None) -> bool:
    seen: set[int] = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        if isinstance(err, AdkQuotaError):
            return True
        status = getattr(err, "status_code", None) or getattr(err, "code", None)
        if status == 429 or "RESOURCE_EXHAUSTED" in str(err):
            return True
        err = err.__cause__ or err.__context__
    return False


def classify_adk_error(err: BaseException) -> str:
    """Stable TEA_* code for logs and Telegram mapping. Never send this to chat."""
    if _looks_like_quota(err):
        return TEA_QUOTA
    if isinstance(err, AdkError):
        return err.error_code or TEA_UNKNOWN
    if isinstance(err, httpx.ConnectError):
        return TEA_UNAVAILABLE
    return TEA_UNKNOWN


def _raise_for_status(status_code: int, body: str, *, scope: str) -> None:
    snippet = (body or "")[:200]
    if status_code == 429 or "RESOURCE_EXHAUSTED" in (body or ""):
        raise AdkQuotaError(
            f"ADK quota exhausted ({status_code})",
            status_code=status_code,
        )
    if status_code in _UNAVAILABLE_STATUS:
        raise AdkUnavailableError(
            f"ADK unavailable ({status_code}) {snippet}".strip(),
            status_code=status_code,
        )
    if scope == "session":
        raise AdkClientError(
            f"session failed: {status_code} {snippet}",
            error_code=TEA_SESSION_FAILED,
            status_code=status_code,
        )
    raise AdkClientError(
        f"ADK /run failed: {status_code} {snippet}",
        error_code=TEA_AGENT_ERROR,
        status_code=status_code,
    )


async def _await_adk(
    awaitable: Awaitable[httpx.Response], *, timeout_code: str
) -> httpx.Response:
    try:
        return await awaitable
    except httpx.TimeoutException as err:
        raise AdkTimeoutError(
            "ADK request timed out",
            error_code=timeout_code,
        ) from err
    except httpx.ConnectError as err:
        raise AdkUnavailableError("ADK connect failed") from err


class AdkHttpClient:
    """Create/reuse an ADK session and POST /run."""

    def __init__(
        self,
        base_url: str,
        app_name: str = DEFAULT_APP_NAME,
        *,
        timeout: float = RUN_TIMEOUT_SEC,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_adk_base_url(base_url)
        self.app_name = app_name
        self.timeout = timeout
        self._transport = transport

    def _session_url(self, user_id: str, session_id: str) -> str:
        return (
            f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        )

    async def ensure_session(
        self, client: httpx.AsyncClient, user_id: str, session_id: str
    ) -> None:
        url = self._session_url(user_id, session_id)
        check = await _await_adk(
            client.get(url, timeout=SESSION_TIMEOUT_SEC),
            timeout_code=TEA_COLD_START,
        )
        if check.status_code == 200:
            return
        if check.status_code not in {404, 422}:
            _raise_for_status(check.status_code, check.text, scope="session")
        created = await _await_adk(
            client.post(url, json={}, timeout=SESSION_TIMEOUT_SEC),
            timeout_code=TEA_COLD_START,
        )
        if created.status_code not in {200, 201}:
            _raise_for_status(created.status_code, created.text, scope="session")

    async def ask(self, user_id: str, session_id: str, text: str) -> str:
        async with httpx.AsyncClient(transport=self._transport) as client:
            await self.ensure_session(client, user_id, session_id)
            response = await _await_adk(
                client.post(
                    f"{self.base_url}/run",
                    json={
                        "appName": self.app_name,
                        "userId": user_id,
                        "sessionId": session_id,
                        "newMessage": {
                            "role": "user",
                            "parts": [{"text": text}],
                        },
                    },
                    timeout=self.timeout,
                ),
                timeout_code=TEA_TIMEOUT_RUN,
            )
        if response.status_code != 200:
            _raise_for_status(response.status_code, response.text, scope="run")
        return extract_reply_text(response.json())
