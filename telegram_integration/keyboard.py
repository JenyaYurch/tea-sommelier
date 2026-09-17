"""Turn the agent next-steps block into a Telegram InlineKeyboard.

The agent (TEA-7) ends recommendation replies with:

    ### Что дальше
    [мягче] [дешевле] [без горечи] [подарок] [подробнее]
    [Купить: <name>](<catalog product_url>)

This module strips that block from the visible text and attaches real buttons.
Action taps send callback_data into the same ADK session as the user's chat.
«Купить» is a URL button that opens the teashop.by catalog link — never an invented URL.
"""

from __future__ import annotations

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tea_agent.next_steps import (
    ACTION_LABELS,
    NextStep,
    is_catalog_url,
    parse_next_steps,
    products_for_reply,
    strip_next_steps_block,
)
from telegram_integration.split import TELEGRAM_MAX_MESSAGE_LENGTH, split_telegram_text

CALLBACK_PREFIX = "tea:a:"
CALLBACK_PATTERN = re.compile(rf"^{re.escape(CALLBACK_PREFIX)}")
_ALLOWED_CALLBACKS = frozenset(ACTION_LABELS) | {"купить"}
_BUTTON_TEXT_LIMIT = 64
_MAX_BUY_BUTTONS = 3


def telegram_user_key(telegram_user_id: int) -> str:
    """ADK user_id for a Telegram account (stable across messages and callbacks)."""
    return f"tg-{int(telegram_user_id)}"


def telegram_session_id(telegram_user_id: int) -> str:
    """ADK session_id for a Telegram account. Callbacks must reuse this.

    Agent Platform custom session ids allow ``[a-z0-9-]`` only (no underscores).
    """
    session_id = f"tg-sess-{int(telegram_user_id)}"
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,61}[a-z0-9]", session_id):
        raise ValueError(f"session_id is not Agent Engine compatible: {session_id}")
    return session_id


def action_callback_data(label: str) -> str:
    return f"{CALLBACK_PREFIX}{label}"


def parse_action_callback(data: str | None) -> str | None:
    """Return a safe next-step label, or None if the callback_data is not ours."""
    if not data or not data.startswith(CALLBACK_PREFIX):
        return None
    label = data[len(CALLBACK_PREFIX) :]
    if label in _ALLOWED_CALLBACKS:
        return label
    return None


def build_next_steps_keyboard(
    steps: list[NextStep],
) -> InlineKeyboardMarkup | None:
    if not steps:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    action_buttons = [
        InlineKeyboardButton(
            text=_clip(label),
            callback_data=action_callback_data(label),
        )
        for label in ACTION_LABELS
    ]
    for i in range(0, len(action_buttons), 3):
        rows.append(action_buttons[i : i + 3])

    catalog_buys = [
        step
        for step in steps
        if step.kind == "buy" and step.url and is_catalog_url(step.url)
    ]
    if catalog_buys:
        for step in catalog_buys[:_MAX_BUY_BUTTONS]:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=_clip(f"Купить: {step.label}"),
                        url=step.url,
                    )
                ]
            )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="купить",
                    callback_data=action_callback_data("купить"),
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def prepare_telegram_reply(
    text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH
) -> tuple[list[str], InlineKeyboardMarkup | None]:
    """Split visible text and attach a keyboard built from the next-steps block."""
    steps = parse_next_steps(text)
    if steps:
        steps = _align_buy_steps(text, steps)
    markup = build_next_steps_keyboard(steps)
    body = strip_next_steps_block(text) if markup is not None else text
    chunks = split_telegram_text(body, limit=limit)
    if not chunks and markup is not None:
        chunks = ["Что дальше:"]
    return chunks, markup


def _align_buy_steps(text: str, steps: list[NextStep]) -> list[NextStep]:
    """Keep action chips; bind Купить buttons to teas named in this reply."""
    actions = [step for step in steps if step.kind == "action"]
    pool = [
        {"product_name": step.label, "product_url": step.url}
        for step in steps
        if step.kind == "buy" and step.url
    ]
    buys = [
        NextStep(
            "buy",
            str(item.get("product_name") or "чай"),
            str(item.get("product_url") or "") or None,
        )
        for item in products_for_reply(text, pool)
    ]
    return [*actions, *buys] or steps


def _clip(text: str, limit: int = _BUTTON_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
