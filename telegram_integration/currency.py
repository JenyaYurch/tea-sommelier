# ruff: noqa: RUF001
"""``/currency`` sets the session display currency without calling the model.

Same session write as ``/city``. Pilot sessions stay in memory. This command
does not spend the message quota. A blocked user is refused and nothing is stored.
EUR, USD, and BYN only. With no argument, three inline buttons. Unset stays EUR.
The saved city does not choose a currency.
"""

from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from tea_agent.currency import (
    SUPPORTED_CURRENCIES,
    currency_state_delta,
    parse_currency,
)
from telegram_integration.access import get_access_gate
from telegram_integration.city import clear_pending_city
from telegram_integration.feedback import clear_pending_feedback
from telegram_integration.session_state import write_session_delta

logger = logging.getLogger("telegram_integration.currency")

CURRENCY_CALLBACK_PREFIX = "tea:c:"
CURRENCY_CALLBACK_PATTERN = re.compile(rf"^{re.escape(CURRENCY_CALLBACK_PREFIX)}")

CURRENCY_ASK_TEXT = (
    "В какой валюте показывать цены витрины? Нажмите EUR, USD или BYN. "
    "Если не выбирать, цены в EUR. Город валюту не выбирает. "
    "Сменить позже — снова /currency."
)
CURRENCY_UNKNOWN_TEXT = (
    "Не понял валюту. Напишите /currency EUR, /currency USD или /currency BYN "
    "— или нажмите кнопку."
)
CURRENCY_SAVE_FAILED_TEXT = (
    "Не получилось запомнить валюту. Попробуйте ещё раз: /currency EUR."
)


def currency_callback_data(code: str) -> str:
    return f"{CURRENCY_CALLBACK_PREFIX}{code}"


def parse_currency_callback(data: str | None) -> str | None:
    """Return EUR, USD, or BYN. Action and feedback callbacks are not currency."""
    if not data or not data.startswith(CURRENCY_CALLBACK_PREFIX):
        return None
    return parse_currency(data[len(CURRENCY_CALLBACK_PREFIX) :])


def currency_keyboard() -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(text=code, callback_data=currency_callback_data(code))
        for code in SUPPORTED_CURRENCIES
    ]
    return InlineKeyboardMarkup([row])


def saved_text(code: str) -> str:
    if code == "BYN":
        detail = "Цены teashop.by покажу в BYN, без пересчёта."
    else:
        detail = (
            f"Цены teashop.by покажу в {code}; рядом останется сумма в BYN, "
            "источник курса и его дата."
        )
    return f"Запомнил: {code}. {detail} Сменить — ещё раз /currency."


def _clear_pending(bot_data: dict, telegram_user_id: int) -> None:
    clear_pending_feedback(bot_data, telegram_user_id)
    clear_pending_city(bot_data, telegram_user_id)


async def _store(bot_data: dict, telegram_user_id: int, code: str) -> bool:
    return await write_session_delta(
        bot_data,
        telegram_user_id,
        currency_state_delta(code),
        what="currency",
    )


async def currency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    bot_data = context.application.bot_data
    _clear_pending(bot_data, user.id)
    gate = get_access_gate(bot_data)
    if not gate.is_allowed(user.id):
        logger.info("blocked non-allowlisted telegram user %s", user.id)
        await message.reply_text(gate.denied_text())
        return
    args = getattr(context, "args", None) or []
    text = " ".join(str(part) for part in args).strip()
    if not text:
        await message.reply_text(CURRENCY_ASK_TEXT, reply_markup=currency_keyboard())
        return
    code = parse_currency(text)
    if code is None:
        await message.reply_text(
            CURRENCY_UNKNOWN_TEXT, reply_markup=currency_keyboard()
        )
        return
    if not await _store(bot_data, user.id, code):
        await message.reply_text(CURRENCY_SAVE_FAILED_TEXT)
        return
    await message.reply_text(saved_text(code))


async def on_currency_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    code = parse_currency_callback(query.data)
    message = query.message if isinstance(query.message, Message) else None
    await query.answer()
    if code is None or message is None:
        return
    bot_data = context.application.bot_data
    _clear_pending(bot_data, user.id)
    gate = get_access_gate(bot_data)
    if not gate.is_allowed(user.id):
        logger.info("blocked non-allowlisted telegram user %s", user.id)
        await message.reply_text(gate.denied_text())
        return
    if not await _store(bot_data, user.id, code):
        await message.reply_text(CURRENCY_SAVE_FAILED_TEXT)
        return
    await message.reply_text(saved_text(code))
