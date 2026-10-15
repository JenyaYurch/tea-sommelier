"""Polite HTTP for the refresh script.

Unit tests may import ``_retry_delay``. They do not send requests.
"""

from __future__ import annotations

import threading
import time

import requests

from tea_agent.eu_shops.models import FetchResponse

USER_AGENT = (
    "TeaBotPriceIndex/1.0 "
    "(+https://github.com/JenyaYurch/tea-sommelier; daily Chinese green tea price index)"
)
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "en,de;q=0.8,pl;q=0.7",
}
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_BACKOFF_S = 20.0
# Shopify answers 429 with Retry-After: 60. Honor that, and still bound a huge hint.
_MAX_RETRY_AFTER_S = 90.0


class RequestsFetcher:
    """GET JSON with a timeout, a few retries, and a thread-local session."""

    def __init__(self, timeout: float = 20.0, retries: int = 3) -> None:
        self.timeout = timeout
        self.retries = retries
        self._local = threading.local()

    def __call__(self, url: str) -> FetchResponse:
        last_error = "request failed"
        for attempt in range(self.retries + 1):
            try:
                response = self._session().get(
                    url,
                    headers=_HEADERS,
                    timeout=(5, self.timeout),
                )
            except requests.Timeout:
                last_error = "timeout"
                if attempt < self.retries:
                    time.sleep(0.5)
                continue
            except requests.RequestException:
                last_error = "request failed"
                if attempt < self.retries:
                    time.sleep(0.5)
                continue
            if response.status_code in _RETRY_STATUS and attempt < self.retries:
                last_error = f"HTTP {response.status_code}"
                time.sleep(_retry_delay(response, attempt))
                continue
            return _from_response(response)
        return FetchResponse(status=0, headers={}, data=None, error=last_error)

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
        return session


def _from_response(response: requests.Response) -> FetchResponse:
    headers = {key.lower(): value for key, value in response.headers.items()}
    if response.status_code != 200:
        return FetchResponse(
            status=response.status_code,
            headers=headers,
            data=None,
            error=_http_error(response.status_code, headers),
        )
    try:
        data = response.json()
    except ValueError:
        return FetchResponse(
            status=response.status_code,
            headers=headers,
            data=None,
            error="response was not JSON",
        )
    return FetchResponse(status=response.status_code, headers=headers, data=data, error=None)


def _retry_delay(response: requests.Response, attempt: int) -> float:
    """Back off after 429/5xx. A numeric Retry-After can only lengthen the wait."""
    backoff = min(_MAX_BACKOFF_S, 2.0 * (2**attempt))
    raw = response.headers.get("Retry-After")
    if not raw:
        return backoff
    try:
        hinted = min(_MAX_RETRY_AFTER_S, max(1.0, float(raw)))
    except ValueError:
        return backoff
    return max(backoff, hinted)


def _http_error(status: int, headers: dict[str, str]) -> str:
    if status == 403 and (
        "cf-mitigated" in headers or "cloudflare" in headers.get("server", "").lower()
    ):
        return "HTTP 403 (blocked)"
    return f"HTTP {status}"
