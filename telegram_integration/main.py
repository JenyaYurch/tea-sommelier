"""Local Telegram polling for the tea sommelier.

Dev-only: the bot answers while this process is running. Production webhook
+ Cloud Run is a later phase (see the architecture doc).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.genai import types
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tea_agent.agent import app as adk_app
from tea_agent.app_utils import services
from telegram_integration.split import split_telegram_text

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("telegram_integration")

START_TEXT = (
    "Привет, я сомелье по зелёному китайскому чаю.\n\n"
    "Напишите, какой вкус хотите (мягкий, без горечи, утро), "
    "или спросите про сорт — Лунцзин, Би Ло Чунь, Аньцзи Бай Ча.\n\n"
    "Пока я работаю локально: если процесс на компьютере остановлен, "
    "в Telegram я молчу."
)
UNAVAILABLE_TEXT = (
    "Сомелье временно недоступен. Попробуйте ещё раз через минуту."
)
QUOTA_TEXT = (
    "Сейчас упёрлись в лимит бесплатного Gemini: 5 запросов в минуту, "
    "а один ответ с инструментами тратит несколько. Подождите около 30 секунд и напишите снова."
)
TYPING_INTERVAL_SEC = 4.0


def _is_quota_error(err: BaseException | None) -> bool:
    seen: set[int] = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        status = getattr(err, "status_code", None) or getattr(err, "code", None)
        if status == 429 or "RESOURCE_EXHAUSTED" in str(err):
            return True
        err = err.__cause__ or err.__context__
    return False


def _load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")


def _require_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing from .env")
    return token


def _build_runner() -> Runner:
    return Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )


async def _typing_loop(chat_id: int, bot, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            logger.debug("typing action failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=TYPING_INTERVAL_SEC)
        except asyncio.TimeoutError:
            continue


def _event_text(event) -> str:
    if not event.is_final_response() or event.author == "user":
        return ""
    if not event.content or not event.content.parts:
        return ""
    return "".join(part.text or "" for part in event.content.parts).strip()


async def ask_agent(runner: Runner, telegram_user_id: int, text: str) -> str:
    user_id = f"tg_{telegram_user_id}"
    session_id = f"tg_sess_{telegram_user_id}"
    message = types.Content(role="user", parts=[types.Part.from_text(text=text)])
    pieces: list[str] = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=message,
    ):
        piece = _event_text(event)
        if piece:
            pieces.append(piece)
    return "\n\n".join(pieces).strip()


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(START_TEXT)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not message.text or not user:
        return

    runner: Runner = context.application.bot_data["runner"]
    stop = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_loop(message.chat_id, context.bot, stop)
    )
    try:
        reply = await ask_agent(runner, user.id, message.text)
        if not reply:
            reply = UNAVAILABLE_TEXT
    except Exception as err:
        if _is_quota_error(err):
            logger.warning("Gemini quota exhausted for telegram user %s", user.id)
            reply = QUOTA_TEXT
        else:
            logger.exception("agent failed for telegram user %s", user.id)
            reply = UNAVAILABLE_TEXT
    finally:
        stop.set()
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    for chunk in split_telegram_text(reply):
        await message.reply_text(chunk)


async def post_init(application: Application) -> None:
    await application.bot.delete_webhook(drop_pending_updates=True)
    me = await application.bot.get_me()
    logger.info("Polling as @%s (live while this process runs)", me.username)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Another getUpdates client is running; retrying")
        return
    logger.exception("Telegram handler error", exc_info=err)


def main() -> None:
    _load_env()
    token = _require_token()
    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )
    application.bot_data["runner"] = _build_runner()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)
    logger.info("Starting Telegram polling (Ctrl+C to stop)")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
