"""HTTP client for the tea-agent Cloud Run / ADK FastAPI surface.

Used by Telegram webhook mode. Local polling still runs the agent in-process.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_APP_NAME = "tea_agent"
SESSION_TIMEOUT_SEC = 10.0
RUN_TIMEOUT_SEC = 120.0


class AdkQuotaError(RuntimeError):
    """The agent backend rejected the call with a quota / rate-limit error."""


class AdkClientError(RuntimeError):
    """The agent backend was unreachable or returned a non-success status."""


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


def _raise_for_quota(status_code: int, body: str) -> None:
    if status_code == 429 or "RESOURCE_EXHAUSTED" in body:
        raise AdkQuotaError(f"ADK quota exhausted ({status_code})")


def cloud_run_identity_token(audience: str) -> str:
    """Identity token for a private tea-agent. Never logged."""
    token = (os.environ.get("CLOUD_RUN_ID_TOKEN") or "").strip()
    if token:
        return token
    try:
        response = httpx.get(
            "http://metadata.google.internal/computeMetadata/v1/"
            "instance/service-accounts/default/identity",
            params={"audience": audience},
            headers={"Metadata-Flavor": "Google"},
            timeout=2.0,
        )
    except httpx.HTTPError:
        return ""
    if response.status_code != 200:
        return ""
    return response.text.strip()


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
        self._iam_headers: dict[str, str] | None = None

    def _auth_headers(self) -> dict[str, str]:
        if self._iam_headers is None:
            token = cloud_run_identity_token(self.base_url)
            self._iam_headers = (
                {"Authorization": f"Bearer {token}"} if token else {}
            )
        return self._iam_headers

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        headers = dict(kwargs.get("headers") or {})
        if self._iam_headers:
            headers.update(self._iam_headers)
        request_kwargs = {**kwargs, "headers": headers} if headers else kwargs
        response = await client.request(method, url, **request_kwargs)
        if response.status_code not in {401, 403}:
            return response
        auth = self._auth_headers()
        token = auth.get("Authorization")
        if not token or headers.get("Authorization") == token:
            return response
        headers = {**headers, **auth}
        return await client.request(method, url, **{**kwargs, "headers": headers})

    def _session_url(self, user_id: str, session_id: str) -> str:
        return (
            f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        )

    async def ensure_session(
        self, client: httpx.AsyncClient, user_id: str, session_id: str
    ) -> None:
        url = self._session_url(user_id, session_id)
        check = await self._request(client, "GET", url, timeout=SESSION_TIMEOUT_SEC)
        if check.status_code == 200:
            return
        if check.status_code not in {404, 422}:
            _raise_for_quota(check.status_code, check.text)
            raise AdkClientError(
                f"session check failed: {check.status_code} {check.text[:200]}"
            )
        created = await self._request(
            client, "POST", url, json={}, timeout=SESSION_TIMEOUT_SEC
        )
        if created.status_code not in {200, 201}:
            _raise_for_quota(created.status_code, created.text)
            raise AdkClientError(
                f"session create failed: {created.status_code} {created.text[:200]}"
            )

    async def ask(self, user_id: str, session_id: str, text: str) -> str:
        async with httpx.AsyncClient(transport=self._transport) as client:
            await self.ensure_session(client, user_id, session_id)
            response = await self._request(
                client,
                "POST",
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
            )
        _raise_for_quota(response.status_code, response.text)
        if response.status_code != 200:
            raise AdkClientError(
                f"ADK /run failed: {response.status_code} {response.text[:200]}"
            )
        return extract_reply_text(response.json())
