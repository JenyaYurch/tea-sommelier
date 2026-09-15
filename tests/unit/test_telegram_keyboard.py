# ruff: noqa: RUF001
"""Unit tests for Telegram InlineKeyboard next-steps (TEA-11)."""

from tea_agent.next_steps import (
    ACTION_LABELS,
    HEADING,
    format_next_steps_block,
)
from telegram_integration.keyboard import (
    CALLBACK_PREFIX,
    action_callback_data,
    build_next_steps_keyboard,
    parse_action_callback,
    prepare_telegram_reply,
    telegram_session_id,
    telegram_user_key,
)

LONGJING_URL = "https://www.teashop.by/product/longjing-1/"
BILUOCHUN_URL = "https://www.teashop.by/product/duntin-bi-lo-chun/"
FAKE_URL = "https://www.teashop.by/product/totally-invented-tea/"


def _recs_with_block() -> str:
    recs = (
        "1. Лунцзин — мягкий утренний чай.\n"
        "2. Би Ло Чунь — без горечи.\n"
        "3. Аньцзи Бай Ча — светлый вкус."
    )
    block = format_next_steps_block(
        [
            {"product_name": "Си Ху Лун Цзин", "product_url": LONGJING_URL},
            {"product_name": "Дунтин Би Ло Чунь", "product_url": BILUOCHUN_URL},
        ]
    )
    return f"{recs}\n\n{block}"


def _buttons(markup) -> list[dict]:
    rows = markup.to_dict()["inline_keyboard"]
    return [button for row in rows for button in row]


def test_callback_reuses_same_session_as_text_messages() -> None:
    user_id = 4242
    # Text and callback handlers both pass user.id into ask_agent, which uses these.
    assert telegram_session_id(user_id) == "tg_sess_4242"
    assert telegram_user_key(user_id) == "tg_4242"


def test_action_callback_data_fits_telegram_limit() -> None:
    for label in (*ACTION_LABELS, "купить"):
        payload = action_callback_data(label)
        assert len(payload.encode("utf-8")) <= 64
        assert parse_action_callback(payload) == label
    assert parse_action_callback("tea:a:hack") is None
    assert parse_action_callback("unrelated") is None


def test_buy_buttons_open_catalog_urls_not_callback() -> None:
    chunks, markup = prepare_telegram_reply(_recs_with_block())
    assert markup is not None
    body = "\n".join(chunks)
    assert HEADING not in body
    assert "[мягче]" not in body
    assert "[Купить:" not in body
    buttons = _buttons(markup)
    actions = {button["text"]: button for button in buttons if "callback_data" in button}
    buys = [button for button in buttons if "url" in button]
    for label in ACTION_LABELS:
        assert actions[label]["callback_data"] == f"{CALLBACK_PREFIX}{label}"
    urls = {button["url"] for button in buys}
    assert LONGJING_URL in urls
    assert BILUOCHUN_URL in urls
    assert FAKE_URL not in urls
    assert all(button["url"].startswith("https://") for button in buys)
    assert not any("callback_data" in button for button in buys)


def test_buy_without_catalog_url_is_callback() -> None:
    text = (
        "1. Лунцзин\n2. Би Ло Чунь\n3. Аньцзи\n\n"
        f"{HEADING}\n[мягче] [дешевле] [без горечи] [подарок] [подробнее]\n[купить]"
    )
    _, markup = prepare_telegram_reply(text)
    assert markup is not None
    buttons = _buttons(markup)
    assert not any("url" in button for button in buttons)
    buy = next(button for button in buttons if button["text"] == "купить")
    assert parse_action_callback(buy["callback_data"]) == "купить"


def test_invented_buy_url_is_not_a_url_button() -> None:
    text = (
        "1. Лунцзин\n2. Би Ло Чунь\n3. Аньцзи\n\n"
        f"{HEADING}\n[мягче] [дешевле] [без горечи] [подарок] [подробнее]\n"
        f"[Купить: фейк]({FAKE_URL})"
    )
    _, markup = prepare_telegram_reply(text)
    assert markup is not None
    buttons = _buttons(markup)
    assert FAKE_URL not in {button.get("url") for button in buttons}
    # No catalog URL → fallback callback «купить», never open an invented shop page.
    buy = next(button for button in buttons if button.get("text") == "купить")
    assert "url" not in buy
    assert parse_action_callback(buy["callback_data"]) == "купить"


def test_no_keyboard_without_next_steps_block() -> None:
    chunks, markup = prepare_telegram_reply("Какой у вас опыт с китайским чаем?")
    assert markup is None
    assert chunks == ["Какой у вас опыт с китайским чаем?"]


def test_keyboard_none_when_no_steps() -> None:
    assert build_next_steps_keyboard([]) is None
