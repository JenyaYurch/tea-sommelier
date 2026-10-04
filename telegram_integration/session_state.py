"""Write ADK session state without calling the model.

``/city`` and ``/currency`` share this. Pilot sessions stay on the configured
session service (in-memory on Cloud Run). A missing runner or a failed PATCH
returns False and does not raise.
"""

from __future__ import annotations

import logging
import uuid

from tea_agent.location import local_shops_clear_delta
from telegram_integration.adk_client import AdkHttpClient
from telegram_integration.deploy_spec import ADK_APP_NAME
from telegram_integration.keyboard import telegram_session_id, telegram_user_key
from telegram_integration.turn_gate import get_turn_gate

logger = logging.getLogger("telegram_integration.session_state")


def _runner_app_name(runner: object) -> str:
    name = getattr(runner, "app_name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    app = getattr(runner, "app", None)
    app_name = getattr(app, "name", None)
    if isinstance(app_name, str) and app_name.strip():
        return app_name.strip()
    return ADK_APP_NAME


async def _write_runner_state(runner: object, telegram_user_id: int, delta: dict) -> None:
    from google.adk.events.event import Event
    from google.adk.events.event_actions import EventActions

    service = runner.session_service
    app_name = _runner_app_name(runner)
    user_id = telegram_user_key(telegram_user_id)
    session_id = telegram_session_id(telegram_user_id)
    session = await service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if session is None:
        await service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            state=delta,
        )
        return
    await service.append_event(
        session,
        Event(
            invocation_id=f"state-{uuid.uuid4().hex[:12]}",
            author="tea_sommelier",
            actions=EventActions(state_delta=delta),
        ),
    )


def _http_state_reader(client: object):
    """Session-state getter, or None for a test double that must not GET.

    ``AdkHttpClient`` subclasses in tests override ``ask`` and ``patch`` and
    would otherwise inherit a live HTTP read.
    """
    if type(client) is AdkHttpClient:
        return client.get_session_state
    if isinstance(client, AdkHttpClient) and "get_session_state" in type(client).__dict__:
        return client.get_session_state
    return None


async def _read_session_state(bot_data: dict, telegram_user_id: int) -> dict | None:
    """Current ADK session state, or None when it cannot be read."""
    user_id = telegram_user_key(telegram_user_id)
    session_id = telegram_session_id(telegram_user_id)
    client = bot_data.get("adk_client")
    try:
        reader = _http_state_reader(client)
        if reader is not None:
            state = await reader(user_id, session_id)
            return state if isinstance(state, dict) else {}
        runner = bot_data.get("runner")
        if runner is None:
            return None
        session = await runner.session_service.get_session(
            app_name=_runner_app_name(runner),
            user_id=user_id,
            session_id=session_id,
        )
    except Exception as err:
        logger.warning(
            "could not read session state for telegram user %s (%s)",
            telegram_user_id,
            type(err).__name__,
        )
        return None
    if session is None:
        return {}
    state = getattr(session, "state", None)
    return dict(state) if isinstance(state, dict) else {}


async def write_session_delta(
    bot_data: dict,
    telegram_user_id: int,
    delta: dict,
    *,
    what: str,
    shops_place: tuple[str, str] | None = None,
) -> bool:
    """Merge ``delta`` into the ADK session. False when nothing was stored.

    Uses the same per-user lock as an agent turn, so ``/city`` and
    ``/currency`` cannot overlap ``/run`` on that session. ``shops_place``
    is ``(city, country)`` for ``/city``: a different city clears the saved
    shop list inside this lock.
    """
    async with get_turn_gate(bot_data).lock_for(telegram_user_id):
        payload = dict(delta)
        if shops_place is not None:
            previous = await _read_session_state(bot_data, telegram_user_id)
            if isinstance(previous, dict):
                payload.update(
                    local_shops_clear_delta(previous, shops_place[0], shops_place[1])
                )
        client = bot_data.get("adk_client")
        try:
            if isinstance(client, AdkHttpClient):
                await client.patch_session_state(
                    telegram_user_key(telegram_user_id),
                    telegram_session_id(telegram_user_id),
                    payload,
                )
                return True
            runner = bot_data.get("runner")
            if runner is None:
                return False
            await _write_runner_state(runner, telegram_user_id, payload)
        except Exception as err:
            logger.warning(
                "could not save %s for telegram user %s (%s)",
                what,
                telegram_user_id,
                type(err).__name__,
            )
            return False
        return True
