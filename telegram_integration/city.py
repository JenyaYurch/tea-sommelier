# ruff: noqa: RUF001
"""``/city`` sets the session city without calling the model.

The same keys as ``save_user_location``. Pilot sessions stay on the configured
ADK session service (in-memory on Cloud Run). This command does not spend the
message quota. A blocked user is refused and nothing is stored.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from tea_agent.location import Place, parse_place, place_state_delta
from telegram_integration.access import get_access_gate
from telegram_integration.feedback import clear_pending_feedback
from telegram_integration.session_state import write_session_delta

logger = logging.getLogger("telegram_integration.city")

PENDING_CITY_KEY = "city_pending"

CITY_ASK_TEXT = (
    "В каком вы городе? Ответьте одним сообщением: Варшава, Минск "
    "или Warsaw, Poland. Геолокацию присылать не нужно. "
    "Сменить город позже — снова /city."
)
CITY_NEED_COUNTRY_TEXT = (
    "Не понял страну для этого города. Напишите так: Warsaw, Poland "
    "или Минск, Беларусь."
)
CITY_SAVE_FAILED_TEXT = (
    "Не получилось запомнить город. Попробуйте ещё раз: /city Warsaw."
)


def saved_text(place: Place) -> str:
    if place.label.casefold() == place.city.casefold():
        shown = f"{place.city}, {place.country}"
    else:
        shown = f"{place.label} ({place.city}, {place.country})"
    return (
        f"Запомнил: {shown}. "
        "Можно спросить, где рядом купить чай, или нажать «магазины рядом». "
        "Сменить город — ещё раз /city."
    )


def _pending(bot_data: dict) -> set[int]:
    store = bot_data.get(PENDING_CITY_KEY)
    if not isinstance(store, set):
        store = set()
        bot_data[PENDING_CITY_KEY] = store
    return store


def set_pending_city(bot_data: dict, telegram_user_id: int) -> None:
    _pending(bot_data).add(int(telegram_user_id))


def clear_pending_city(bot_data: dict, telegram_user_id: int) -> None:
    _pending(bot_data).discard(int(telegram_user_id))


def is_pending_city(bot_data: dict, telegram_user_id: int) -> bool:
    return int(telegram_user_id) in _pending(bot_data)


async def remember_place(bot_data: dict, telegram_user_id: int, place: Place) -> bool:
    """Write city and country into the ADK session. False on failure."""
    return await write_session_delta(
        bot_data,
        telegram_user_id,
        place_state_delta(place),
        what="city",
    )


def _looks_like_place_attempt(text: str) -> bool:
    if any(mark in text for mark in ("?", "!", "\n")):
        return False
    words = text.replace(",", " ").split()
    return 1 <= len(words) <= 5 and len(text) <= 60


async def city_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    bot_data = context.application.bot_data
    clear_pending_feedback(bot_data, user.id)
    gate = get_access_gate(bot_data)
    if not gate.is_allowed(user.id):
        clear_pending_city(bot_data, user.id)
        logger.info("blocked non-allowlisted telegram user %s", user.id)
        await message.reply_text(gate.denied_text())
        return
    args = getattr(context, "args", None) or []
    text = " ".join(str(part) for part in args).strip()
    if not text:
        set_pending_city(bot_data, user.id)
        await message.reply_text(CITY_ASK_TEXT)
        return
    place = parse_place(text)
    if place is None:
        set_pending_city(bot_data, user.id)
        await message.reply_text(CITY_NEED_COUNTRY_TEXT)
        return
    clear_pending_city(bot_data, user.id)
    if not await remember_place(bot_data, user.id, place):
        await message.reply_text(CITY_SAVE_FAILED_TEXT)
        return
    await message.reply_text(saved_text(place))


async def maybe_capture_city_text(
    message, telegram_user_id: int, bot_data: dict
) -> bool:
    """Save a short reply after ``/city`` with no argument. True if handled.

    A tea question is not swallowed: if the text is not a place, the pending
    flag is cleared and the caller sends the message to the sommelier.
    """
    if not is_pending_city(bot_data, telegram_user_id):
        return False
    text = str(getattr(message, "text", None) or "").strip()
    if not text:
        return False
    place = parse_place(text)
    if place is None:
        if _looks_like_place_attempt(text):
            await message.reply_text(CITY_NEED_COUNTRY_TEXT)
            return True
        clear_pending_city(bot_data, telegram_user_id)
        return False
    clear_pending_city(bot_data, telegram_user_id)
    if not await remember_place(bot_data, telegram_user_id, place):
        await message.reply_text(CITY_SAVE_FAILED_TEXT)
        return True
    await message.reply_text(saved_text(place))
    return True
