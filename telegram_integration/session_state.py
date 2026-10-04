"""Write ADK session state without calling the model.

``/city`` and ``/currency`` share this. Pilot sessions stay on the configured
session service (in-memory on Cloud Run). A missing runner or a failed PATCH
returns False and does not raise.
"""

from __future__ import annotations

import logging
import uuid

from telegram_integration.adk_client import AdkHttpClient
from telegram_integration.deploy_spec import ADK_APP_NAME
from telegram_integration.keyboard import telegram_session_id, telegram_user_key

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


async def write_session_delta(
    bot_data: dict,
    telegram_user_id: int,
    delta: dict,
    *,
    what: str,
) -> bool:
    """Merge ``delta`` into the ADK session. False when nothing was stored."""
    client = bot_data.get("adk_client")
    try:
        if isinstance(client, AdkHttpClient):
            await client.patch_session_state(
                telegram_user_key(telegram_user_id),
                telegram_session_id(telegram_user_id),
                delta,
            )
            return True
        runner = bot_data.get("runner")
        if runner is None:
            return False
        await _write_runner_state(runner, telegram_user_id, delta)
    except Exception as err:
        logger.warning(
            "could not save %s for telegram user %s (%s)",
            what,
            telegram_user_id,
            type(err).__name__,
        )
        return False
    return True
