# ruff: noqa: RUF001
"""In-chat beta feedback (TEA-17).

👍 / 👎 sit on recommendation keyboards. ``/feedback`` takes free text.
Neither path calls the model or spends the per-user message quota.
The allowlist still applies: a blocked user is refused and nothing is stored.

Production (``ADK_SERVER_URL`` set) POSTs the payload to tea-agent
``/feedback`` with ``X-Tea-Agent-Token``, and that process writes the
Cloud Logging line. If the POST fails, or this process is local polling
without an HTTP client, the same payload is logged here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import Message, Update
from telegram.ext import ContextTypes

from tea_agent.app_utils.feedback_log import log_feedback
from tea_agent.app_utils.typing import Feedback
from telegram_integration.access import get_access_gate
from telegram_integration.adk_client import AdkHttpClient
from telegram_integration.keyboard import (
    FEEDBACK_DOWN,
    FEEDBACK_SKIP,
    FEEDBACK_UP,
    build_feedback_skip_keyboard,
    parse_feedback_callback,
    telegram_session_id,
    telegram_user_key,
)

logger = logging.getLogger("telegram_integration.feedback")

PENDING_KEY = "feedback_pending"

THUMBS_UP_TEXT = "Спасибо, записал."
THUMBS_DOWN_TEXT = (
    "Записал 👎. Напишите одним сообщением, что не так — "
    "это уйдёт в отзыв, не сомелье. Или нажмите «Пропустить»."
)
FEEDBACK_SKIPPED_TEXT = "Хорошо. Дальше пишите сомелье как обычно."
FEEDBACK_SAVED_TEXT = "Спасибо, отзыв записан."
FEEDBACK_PROMPT_TEXT = (
    "Напишите следующим сообщением, что понравилось или что сломалось. "
    "Оно не уйдёт сомелье."
)
HELP_TEXT = (
    "Короткая памятка\n\n"
    "Я подбираю китайский чай: зелёный, белый, жёлтый, красный, "
    "шен и шу пуэр, GABA. Напишите вкус (мягкий, без горечи, на утро), "
    "сорт или список из заказа. Можно спросить, как заваривать.\n\n"
    "Под рекомендацией кнопки «мягче», «дешевле», «купить» и другие "
    "продолжают разговор с сомелье и тратят лимит сообщений. "
    "«магазины рядом» — отдельно от «Купить»: магазины в вашем городе, без цены.\n"
    "👍 и 👎 только оценивают этот ответ: сомелье их не читает как вопрос, "
    "лимит не тратится. После 👎 можно одним сообщением написать, что не так, "
    "или нажать «Пропустить».\n\n"
    "/city Warsaw — запомнить город (можно «Варшава» или «Warsaw, Poland»). "
    "Без текста команда спросит город. Он хранится в этой сессии. "
    "/city не тратит лимит и не уходит сомелье как вопрос.\n\n"
    "/currency USD — валюта цен витрины: EUR, USD или BYN. "
    "Без текста команда покажет три кнопки. Пока ничего не выбрано, цены в EUR. "
    "Город валюту не выбирает. "
    "/currency не тратит лимит и не уходит сомелье как вопрос.\n\n"
    "/feedback и текст — отзыв владельцу (что понравилось или что сломалось). "
    "Без текста команда попросит следующее сообщение; оно тоже не уйдёт сомелье.\n\n"
    "Это закрытая бета. Если бот молчит, напишите владельцу."
)

BOT_COMMANDS = (
    ("start", "Начать"),
    ("help", "Памятка"),
    ("city", "Город"),
    ("currency", "Валюта"),
    ("feedback", "Отзыв"),
)


@dataclass
class PendingFeedback:
    """Next plain-text message is feedback, not a sommelier turn.

    ``kind`` is ``reason`` (follow-up after 👎) or ``command`` (bare ``/feedback``).
    Forgotten when this process restarts. The 👎 itself is already logged.
    """

    kind: str
    message_id: int | None = None
    reply_excerpt: str = ""


def _pending_store(bot_data: dict) -> dict[int, PendingFeedback]:
    store = bot_data.get(PENDING_KEY)
    if not isinstance(store, dict):
        store = {}
        bot_data[PENDING_KEY] = store
    return store


def set_pending_feedback(
    bot_data: dict, telegram_user_id: int, pending: PendingFeedback
) -> None:
    _pending_store(bot_data)[int(telegram_user_id)] = pending


def pop_pending_feedback(
    bot_data: dict, telegram_user_id: int
) -> PendingFeedback | None:
    return _pending_store(bot_data).pop(int(telegram_user_id), None)


def clear_pending_feedback(bot_data: dict, telegram_user_id: int) -> None:
    pop_pending_feedback(bot_data, telegram_user_id)


def _clear_pending_city(bot_data: dict, telegram_user_id: int) -> None:
    """Local import: city.py imports this module."""
    from telegram_integration.city import clear_pending_city

    clear_pending_city(bot_data, telegram_user_id)


def build_feedback_payload(
    *,
    telegram_user_id: int,
    text: str = "",
    score: int | float | None = None,
    source: str,
    rating: str = "",
    message_id: int | None = None,
    reply_excerpt: str = "",
) -> dict:
    """JSON body for ``POST /feedback`` and the structured log."""
    item = Feedback(
        score=score,
        text=text,
        user_id=telegram_user_key(telegram_user_id),
        session_id=telegram_session_id(telegram_user_id),
        source=source,
        rating=rating,
        message_id="" if message_id is None else str(message_id),
        reply_excerpt=reply_excerpt,
    )
    return item.model_dump(mode="json")


async def record_feedback(bot_data: dict, payload: dict) -> None:
    """Store feedback. Does not raise: a failed POST is logged locally."""
    client = bot_data.get("adk_client")
    if isinstance(client, AdkHttpClient):
        try:
            await client.submit_feedback(payload)
        except Exception as err:
            logger.warning(
                "POST /feedback failed (%s); logging locally",
                type(err).__name__,
            )
        else:
            logger.info(
                "feedback stored via tea-agent user=%s source=%s rating=%s message_id=%s",
                payload.get("user_id"),
                payload.get("source") or "-",
                payload.get("rating") or "-",
                payload.get("message_id") or "-",
            )
            return
    log_feedback(payload)


def _message_text(message: Message) -> str:
    return str(message.text or message.caption or "")


def _message_id(message: Message) -> int | None:
    value = getattr(message, "message_id", None)
    if isinstance(value, int):
        return value
    return None


async def publish_bot_commands(bot) -> None:
    """Show /start, /help, /city, /currency, and /feedback in the Telegram menu. Failures are logged."""
    from telegram import BotCommand

    try:
        await bot.set_my_commands(
            [BotCommand(name, description) for name, description in BOT_COMMANDS]
        )
    except Exception as err:
        logger.warning(
            "could not publish Telegram bot commands (%s)", type(err).__name__
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    bot_data = context.application.bot_data
    clear_pending_feedback(bot_data, user.id)
    _clear_pending_city(bot_data, user.id)
    gate = get_access_gate(bot_data)
    if not gate.is_allowed(user.id):
        logger.info("blocked non-allowlisted telegram user %s", user.id)
        await message.reply_text(gate.denied_text())
        return
    await message.reply_text(HELP_TEXT)


async def feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    bot_data = context.application.bot_data
    gate = get_access_gate(bot_data)
    if not gate.is_allowed(user.id):
        clear_pending_feedback(bot_data, user.id)
        _clear_pending_city(bot_data, user.id)
        logger.info("blocked non-allowlisted telegram user %s", user.id)
        await message.reply_text(gate.denied_text())
        return
    _clear_pending_city(bot_data, user.id)
    args = getattr(context, "args", None) or []
    text = " ".join(str(part) for part in args).strip()
    if not text:
        set_pending_feedback(bot_data, user.id, PendingFeedback(kind="command"))
        await message.reply_text(FEEDBACK_PROMPT_TEXT)
        return
    clear_pending_feedback(bot_data, user.id)
    await record_feedback(
        bot_data,
        build_feedback_payload(
            telegram_user_id=user.id,
            text=text,
            source="command",
        ),
    )
    await message.reply_text(FEEDBACK_SAVED_TEXT)


async def maybe_capture_feedback_text(
    message: Message,
    telegram_user_id: int,
    bot_data: dict,
) -> bool:
    """Record this message as feedback when a prompt is waiting. True if handled."""
    pending = pop_pending_feedback(bot_data, telegram_user_id)
    if pending is None:
        return False
    text = (getattr(message, "text", None) or "").strip()
    if not text:
        set_pending_feedback(bot_data, telegram_user_id, pending)
        return False
    if pending.kind == "reason":
        source = "thumbs_reason"
        rating = "down"
        score: int | None = 0
    else:
        source = "command"
        rating = ""
        score = None
    await record_feedback(
        bot_data,
        build_feedback_payload(
            telegram_user_id=telegram_user_id,
            text=text,
            score=score,
            source=source,
            rating=rating,
            message_id=pending.message_id,
            reply_excerpt=pending.reply_excerpt,
        ),
    )
    await message.reply_text(FEEDBACK_SAVED_TEXT)
    return True


async def on_feedback_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    kind = parse_feedback_callback(query.data)
    message = query.message if isinstance(query.message, Message) else None
    if kind is None or message is None:
        await query.answer()
        return
    bot_data = context.application.bot_data
    gate = get_access_gate(bot_data)
    if not gate.is_allowed(user.id):
        clear_pending_feedback(bot_data, user.id)
        _clear_pending_city(bot_data, user.id)
        logger.info("blocked non-allowlisted telegram user %s", user.id)
        await query.answer()
        await message.reply_text(gate.denied_text())
        return
    _clear_pending_city(bot_data, user.id)
    await query.answer()
    if kind == FEEDBACK_SKIP:
        clear_pending_feedback(bot_data, user.id)
        await message.reply_text(FEEDBACK_SKIPPED_TEXT)
        return
    if kind == FEEDBACK_UP:
        clear_pending_feedback(bot_data, user.id)
        await record_feedback(
            bot_data,
            build_feedback_payload(
                telegram_user_id=user.id,
                score=1,
                source="thumbs",
                rating="up",
                message_id=_message_id(message),
                reply_excerpt=_message_text(message),
            ),
        )
        await message.reply_text(THUMBS_UP_TEXT)
        return
    if kind != FEEDBACK_DOWN:
        return
    excerpt = _message_text(message)
    message_id = _message_id(message)
    await record_feedback(
        bot_data,
        build_feedback_payload(
            telegram_user_id=user.id,
            score=0,
            source="thumbs",
            rating="down",
            message_id=message_id,
            reply_excerpt=excerpt,
        ),
    )
    set_pending_feedback(
        bot_data,
        user.id,
        PendingFeedback(kind="reason", message_id=message_id, reply_excerpt=excerpt),
    )
    await message.reply_text(
        THUMBS_DOWN_TEXT,
        reply_markup=build_feedback_skip_keyboard(),
    )
