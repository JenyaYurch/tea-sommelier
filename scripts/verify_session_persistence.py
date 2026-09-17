"""Verify TEA-14 against a running tea-agent (local or Cloud Run).

Writes a Telegram-shaped session, then after you restart the service, --check
confirms the taste profile is still there.

    uv run python scripts/verify_session_persistence.py --base-url URL --write
    # restart tea-agent (Cloud Run new revision, or kill local uvicorn)
    uv run python scripts/verify_session_persistence.py --base-url URL --check

Does not create Cloud SQL or deploy. Default telegram user 140014 is a probe id,
not a real chat.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from telegram_integration.adk_client import DEFAULT_APP_NAME, normalize_adk_base_url
from telegram_integration.keyboard import telegram_session_id, telegram_user_key

PROBE_TELEGRAM_USER_ID = 140014
PROFILE = {
    "experience": "новичок",
    "user:experience": "новичок",
    "user:taste_profile": "мягкий без горечи",
    "user:profile_complete": True,
}


def session_url(base_url: str, telegram_user_id: int, app_name: str = DEFAULT_APP_NAME) -> str:
    base = normalize_adk_base_url(base_url)
    user_id = telegram_user_key(telegram_user_id)
    session_id = telegram_session_id(telegram_user_id)
    return f"{base}/apps/{app_name}/users/{user_id}/sessions/{session_id}"


def _patch_profile(client: httpx.Client, url: str) -> dict:
    patched = client.patch(url, json={"state_delta": PROFILE})
    if patched.status_code not in {200, 201}:
        raise SystemExit(f"patch failed: {patched.status_code} {patched.text[:300]}")
    return patched.json()


def write_profile(url: str) -> dict:
    """Create or update the probe session so --write is not a no-op.

    Telegram webhook ``ensure_session`` POSTs ``{}`` first. A GET 200 empty
    session must still receive ``user:`` keys via ADK PATCH state_delta.
    """
    with httpx.Client(timeout=20.0) as client:
        existing = client.get(url)
        if existing.status_code == 200:
            return _patch_profile(client, url)
        created = client.post(url, json=PROFILE)
        if created.status_code == 409:
            return _patch_profile(client, url)
        if created.status_code not in {200, 201}:
            raise SystemExit(f"write failed: {created.status_code} {created.text[:300]}")
        body = created.json()
        state = body.get("state") or {}
        if state.get("user:experience") != PROFILE["user:experience"]:
            return _patch_profile(client, url)
        return body


def check_profile(url: str) -> dict:
    with httpx.Client(timeout=20.0) as client:
        loaded = client.get(url)
    if loaded.status_code != 200:
        raise SystemExit(
            f"profile missing after restart: {loaded.status_code} {loaded.text[:300]}"
        )
    body = loaded.json()
    state = body.get("state") or {}
    if state.get("user:experience") != "новичок":
        raise SystemExit(f"user:experience not restored: {state!r}")
    if state.get("user:profile_complete") is not True:
        raise SystemExit(f"user:profile_complete not restored: {state!r}")
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="tea-agent origin, no secrets")
    parser.add_argument("--telegram-user-id", type=int, default=PROBE_TELEGRAM_USER_ID)
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    url = session_url(args.base_url, args.telegram_user_id, args.app_name)
    if args.write:
        body = write_profile(url)
        print(f"wrote session {body.get('id')} user {body.get('userId')}")
        print("Restart tea-agent, then rerun with --check")
        return
    body = check_profile(url)
    print(f"ok: session {body.get('id')} still has user:experience")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as err:
        print(f"HTTP error: {err}", file=sys.stderr)
        raise SystemExit(1)
