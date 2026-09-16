# ruff: noqa: RUF001
"""Telegram bot: local polling or Cloud Run webhook (TEA-10 / TEA-13).

Polling (default): in-process ADK Runner. Stops when this process stops.
Webhook: PORT + SERVICE_URL set. Forwards text to tea-agent via HTTP.
Webhook URL: <SERVICE_URL>/<TELEGRAM_BOT_TOKEN>.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, Conflict
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_integration.adk_client import AdkClientError, AdkHttpClient, AdkQuotaError
from telegram_integration.deploy_spec import (
    ADK_APP_NAME,
    is_webhook_mode,
    normalize_service_url,
)
from telegram_integration.format import markdown_to_telegram_html
from telegram_integration.keyboard import (
    CALLBACK_PATTERN,
    parse_action_callback,
    prepare_telegram_reply,
    telegram_session_id,
    telegram_user_key,
)

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
    "или спросите про сорт — Лунцзин, Би Ло Чунь, Аньцзи Бай Ча."
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
    if isinstance(err, AdkQuotaError):
        return True
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


def _adk_http_client() -> AdkHttpClient | None:
    url = os.getenv("ADK_SERVER_URL", "").strip()
    if not url:
        return None
    app_name = os.getenv("ADK_APP_NAME", ADK_APP_NAME).strip() or ADK_APP_NAME
    return AdkHttpClient(url, app_name)


def _build_local_runner():
    from google.adk.runners import Runner

    from tea_agent.agent import app as adk_app
    from tea_agent.app_utils import services

    return Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )


def _event_text(event) -> str:
    if not event.is_final_response() or event.author == "user":
        return ""
    if not event.content or not event.content.parts:
        return ""
    return "".join(part.text or "" for part in event.content.parts).strip()


async def ask_agent_local(runner, telegram_user_id: int, text: str) -> str:
    from google.genai import types

    user_id = telegram_user_key(telegram_user_id)
    session_id = telegram_session_id(telegram_user_id)
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


async def ask_agent_remote(client: AdkHttpClient, telegram_user_id: int, text: str) -> str:
    return await client.ask(
        telegram_user_key(telegram_user_id),
        telegram_session_id(telegram_user_id),
        text,
    )


async def ask_agent(bot_data: dict, telegram_user_id: int, text: str) -> str:
    client = bot_data.get("adk_client")
    if isinstance(client, AdkHttpClient):
        return await ask_agent_remote(client, telegram_user_id, text)
    runner = bot_data.get("runner")
    if runner is None:
        raise AdkClientError("No ADK backend configured")
    return await ask_agent_local(runner, telegram_user_id, text)


async def _typing_loop(chat_id: int, bot, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            logger.debug("typing action failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=TYPING_INTERVAL_SEC)
        except TimeoutError:
            continue


async def _deliver_reply(target: Message, text: str) -> None:
    chunks, markup = prepare_telegram_reply(text)
    if not chunks:
        await target.reply_text(UNAVAILABLE_TEXT)
        return
    last = len(chunks) - 1
    for index, chunk in enumerate(chunks):
        keyboard = markup if index == last else None
        await _reply_formatted(target, chunk, keyboard)


async def _reply_formatted(target: Message, chunk: str, keyboard) -> None:
    html_text = markdown_to_telegram_html(chunk)
    try:
        await target.reply_text(
            html_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except BadRequest:
        logger.warning("Telegram rejected HTML formatting; sending plain text")
        await target.reply_text(chunk, reply_markup=keyboard)


async def _run_agent_and_reply(
    *,
    target: Message,
    telegram_user_id: int,
    text: str,
    bot,
    bot_data: dict,
) -> None:
    stop = asyncio.Event()
    typing_task = asyncio.create_task(_typing_loop(target.chat_id, bot, stop))
    try:
        reply = await ask_agent(bot_data, telegram_user_id, text)
        if not reply:
            reply = UNAVAILABLE_TEXT
    except Exception as err:
        if _is_quota_error(err):
            logger.warning(
                "Gemini quota exhausted for telegram user %s", telegram_user_id
            )
            reply = QUOTA_TEXT
        else:
            logger.exception("agent failed for telegram user %s", telegram_user_id)
            reply = UNAVAILABLE_TEXT
    finally:
        stop.set()
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
    await _deliver_reply(target, reply)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(START_TEXT)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not message.text or not user:
        return
    await _run_agent_and_reply(
        target=message,
        telegram_user_id=user.id,
        text=message.text,
        bot=context.bot,
        bot_data=context.application.bot_data,
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    label = parse_action_callback(query.data)
    message = query.message if isinstance(query.message, Message) else None
    if not label or message is None:
        await query.answer()
        return
    await query.answer()
    await _run_agent_and_reply(
        target=message,
        telegram_user_id=user.id,
        text=label,
        bot=context.bot,
        bot_data=context.application.bot_data,
    )


async def post_init_polling(application: Application) -> None:
    await application.bot.delete_webhook(drop_pending_updates=True)
    me = await application.bot.get_me()
    logger.info("Polling as @%s (live while this process runs)", me.username)


async def post_init_webhook(application: Application) -> None:
    me = await application.bot.get_me()
    logger.info("Webhook as @%s (url_path is the bot token, not logged)", me.username)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Another getUpdates client is running; retrying")
        return
    logger.exception("Telegram handler error", exc_info=err)


def _attach_backend(application: Application, *, webhook: bool) -> None:
    client = _adk_http_client()
    if client is not None:
        application.bot_data["adk_client"] = client
        logger.info("ADK backend: HTTP %s app=%s", client.base_url, client.app_name)
        return
    if webhook:
        raise SystemExit(
            "Webhook mode requires ADK_SERVER_URL (tea-agent Cloud Run URL)"
        )
    application.bot_data["runner"] = _build_local_runner()
    logger.info("ADK backend: in-process Runner")


def main() -> None:
    _load_env()
    token = _require_token()
    port = os.getenv("PORT")
    service_url = os.getenv("SERVICE_URL")
    webhook = is_webhook_mode(port=port, service_url=service_url)
    application = (
        Application.builder()
        .token(token)
        .post_init(post_init_webhook if webhook else post_init_polling)
        .concurrent_updates(True)
        .build()
    )
    _attach_backend(application, webhook=webhook)
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(
        CallbackQueryHandler(on_callback, pattern=CALLBACK_PATTERN)
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)
    if webhook:
        listen_url = normalize_service_url(service_url or "")
        logger.info("Starting Telegram webhook on port %s", port)
        application.run_webhook(
            listen="0.0.0.0",
            port=int(port or "8080"),
            url_path=token,
            webhook_url=f"{listen_url}/{token}",
            allowed_updates=Update.ALL_TYPES,
        )
        return
    logger.info("Starting Telegram polling (Ctrl+C to stop)")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
