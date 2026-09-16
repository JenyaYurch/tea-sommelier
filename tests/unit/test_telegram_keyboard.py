# ruff: noqa: RUF001
"""Unit tests for Telegram InlineKeyboard next-steps (TEA-11)."""

from tea_agent.next_steps import (
    ACTION_LABELS,
    HEADING,
    format_next_steps_block,
    normalize_product_url,
)
from tea_agent.shop_catalog import load_catalog
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
LONGJING_SKU2 = "https://www.teashop.by/product/xihu-longjing/"
BILUOCHUN_URL = "https://www.teashop.by/product/duntin-bi-lo-chun/"
ANJI_URL = "https://www.teashop.by/product/anczi-bajcha/"
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


def _catalog_slug(url: str) -> str:
    key = normalize_product_url(url)
    for item in load_catalog():
        if normalize_product_url(str(item.get("product_url") or "")) == key:
            return str(item.get("matched_slug") or "")
    return ""


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
    urls = [button["url"] for button in buys]
    assert urls == [LONGJING_URL, BILUOCHUN_URL, ANJI_URL]
    assert all(button["url"].startswith("https://") for button in buys)
    assert not any("callback_data" in button for button in buys)


def test_buy_buttons_follow_named_teas_not_extra_skus() -> None:
    recs = (
        "1. Лунцзин — мягкий утренний чай.\n"
        "2. Би Ло Чунь — без горечи.\n"
        "3. Аньцзи Бай Ча — светлый вкус."
    )
    block = format_next_steps_block(
        [
            {"product_name": "Си Ху Лун Цзин", "product_url": LONGJING_URL},
            {"product_name": "Си Ху Лун Цзин ещё", "product_url": LONGJING_SKU2},
            {"product_name": "Дунтин Би Ло Чунь", "product_url": BILUOCHUN_URL},
        ]
    )
    _, markup = prepare_telegram_reply(f"{recs}\n\n{block}")
    assert markup is not None
    urls = [button["url"] for button in _buttons(markup) if "url" in button]
    assert urls == [LONGJING_URL, BILUOCHUN_URL, ANJI_URL]
    assert LONGJING_SKU2 not in urls


def test_named_recs_without_buy_urls_still_get_catalog_buttons() -> None:
    text = (
        "1. Лунцзин\n2. Би Ло Чунь\n3. Аньцзи Бай Ча\n\n"
        f"{HEADING}\n[мягче] [дешевле] [без горечи] [подарок] [подробнее]\n[купить]"
    )
    _, markup = prepare_telegram_reply(text)
    assert markup is not None
    buttons = _buttons(markup)
    buys = [button for button in buttons if "url" in button]
    urls = [button["url"] for button in buys]
    labels = [button["text"] for button in buys]
    assert len(urls) == 3
    assert [_catalog_slug(url) for url in urls] == [
        "xihu-longjing",
        "biluochun",
        "anji-baicha",
    ]
    assert "Лунцзин" in labels[0]
    assert "Би Ло Чунь" in labels[1]
    assert "Аньцзи" in labels[2]


def test_invented_buy_url_without_named_recs_is_not_a_url_button() -> None:
    text = (
        "Какой чай вам нравится?\n\n"
        f"{HEADING}\n[мягче] [дешевле] [без горечи] [подарок] [подробнее]\n"
        f"[Купить: фейк]({FAKE_URL})"
    )
    _, markup = prepare_telegram_reply(text)
    assert markup is not None
    buttons = _buttons(markup)
    assert FAKE_URL not in {button.get("url") for button in buttons}
    buy = next(button for button in buttons if button.get("text") == "купить")
    assert "url" not in buy
    assert parse_action_callback(buy["callback_data"]) == "купить"


def test_no_keyboard_without_next_steps_block() -> None:
    chunks, markup = prepare_telegram_reply("Какой у вас опыт с китайским чаем?")
    assert markup is None
    assert chunks == ["Какой у вас опыт с китайским чаем?"]


def test_keyboard_none_when_no_steps() -> None:
    assert build_next_steps_keyboard([]) is None
