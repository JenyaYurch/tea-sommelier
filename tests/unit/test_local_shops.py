# ruff: noqa: RUF001
"""Local b2btea shops (TEA-32). Fixture responses only — no live network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from tea_agent.agent import instruction_text
from tea_agent.local_shops import directory_tea_key, find_local_shops
from tea_agent.location import (
    LOCAL_SHOPS_LAST_KEY,
    LOCAL_SHOPS_SAVED_KEY,
    parse_place,
    save_user_location,
)
from tea_agent.next_steps import (
    LOCAL_SHOPS_CHIP,
    LOCAL_SHOPS_HEADING,
    LOCAL_SHOPS_KEY,
    attach_next_steps_to_response,
    collect_local_shop_hits,
    ensure_next_steps,
    format_local_shops_section,
    invented_buy_urls,
)
from tea_agent.shop_catalog import load_catalog
from telegram_integration.format import markdown_to_telegram_html
from telegram_integration.keyboard import prepare_telegram_reply

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "b2btea" / "companies.json"
LONGJING_URL = "https://www.teashop.by/product/longjing-1/"
_FORBIDDEN = {"offset", "limit", "china_focus", "country_code", "page"}


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _ctx(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(state={} if state is None else state)


def _install(monkeypatch, scenario: str) -> list[tuple[str, dict]]:
    payload = _load()[scenario]
    calls: list[tuple[str, dict]] = []

    def fake_get_json(path: str, params: dict | None = None) -> dict:
        query = dict(params or {})
        calls.append((path, query))
        assert not path.startswith("http")
        assert "b2btea.com" not in path
        if path.startswith("/api/v2/tea/"):
            slug = path.rsplit("/", 1)[-1]
            card = payload["cards"].get(slug)
            if card is None:
                return {"status": "error", "error": "tea_not_found"}
            return card
        if path != "/api/v2/companies":
            return {"status": "error", "error": "unexpected_path"}
        assert _FORBIDDEN.isdisjoint(query)
        key = (
            f"{query.get('country', '')}|{query.get('city', '')}|"
            f"{query.get('type', '')}|{query.get('tea', '')}"
        )
        found = payload["queries"].get(key)
        if found is None:
            return {"status": "error", "error": "unexpected_query"}
        return found

    monkeypatch.setattr("tea_agent.local_shops.get_json", fake_get_json)
    return calls


def _company_calls(calls: list[tuple[str, dict]]) -> list[dict]:
    return [params for path, params in calls if path == "/api/v2/companies"]


def test_directory_key_maps_red_to_black_and_keeps_other_classes() -> None:
    assert directory_tea_key("red") == "black"
    assert directory_tea_key("green") == "green"
    assert directory_tea_key("yellow") == "yellow"
    assert directory_tea_key("oolong") == "oolong"
    assert directory_tea_key("puer") == "puer"
    assert directory_tea_key("gaba") is None


def test_biluochun_falls_back_to_green_class_not_the_cultivar(monkeypatch) -> None:
    calls = _install(monkeypatch, "warsaw_green")
    result = find_local_shops("Poland", "Warsaw", "biluochun", _ctx())

    assert result["status"] == "success"
    assert result["tea_type"] == "green"
    assert result["tea_class"] == "green"
    assert result["class_source"] == "slug_index"
    assert result["class_label_ru"] == "зелёный"
    assert result["source"] == "b2btea.com"
    assert "not the cultivar" in result["note"]
    assert len(result["shops"]) == 3
    assert [shop["slug"] for shop in result["shops"]] == [
        "alpha-tea",
        "zeta-leaf",
        "eta-room",
    ]
    assert all(shop["same_city"] for shop in result["shops"])
    assert all(shop["tea_class"] == "green" for shop in result["shops"])
    for shop in result["shops"]:
        assert shop["url"].startswith("https://b2btea.com/")
        assert "/c/" in shop["url"]
        assert shop["website"].startswith("https://")
        assert "price" not in shop
        assert "EUR" not in str(shop)
        assert "BYN" not in str(shop)
    teas = {params["tea"] for params in _company_calls(calls)}
    assert teas == {"green"}
    assert "biluochun" not in teas
    assert all("city" in params for params in _company_calls(calls))
    assert not any(params.get("type") == "ecommerce" and "city" not in params for params in _company_calls(calls))
    assert all(path.startswith("/api/v2/") for path, _ in calls)


def test_same_city_shops_come_before_online_shops(monkeypatch) -> None:
    calls = _install(monkeypatch, "online_fallback")
    result = find_local_shops("poland", "варшава", "biluochun", _ctx())

    assert result["status"] == "success"
    assert result["city"] == "Warsaw"
    assert result["country"] == "Poland"
    assert [shop["slug"] for shop in result["shops"]] == ["mu-warsaw", "iota-krakow"]
    assert [shop["same_city"] for shop in result["shops"]] == [True, False]
    assert any("city" not in params and params.get("type") == "ecommerce" for params in _company_calls(calls))


def test_red_tea_card_queries_directory_black(monkeypatch) -> None:
    calls = _install(monkeypatch, "red_black")
    result = find_local_shops("Poland", "Warsaw", "dianhong-gongfu", _ctx())

    assert result["status"] == "success"
    assert result["tea_type"] == "red"
    assert result["tea_class"] == "black"
    assert result["class_source"] == "tea_card"
    assert result["class_label_ru"] == "красный"
    assert result["shops"][0]["slug"] == "hong-cha"
    assert result["shops"][0]["url"] == "https://b2btea.com/en/c/hong-cha/"
    teas = {params["tea"] for params in _company_calls(calls)}
    assert teas == {"black"}
    assert "red" not in teas
    section = format_local_shops_section(result)
    assert "красный" in section
    assert "dianhong" not in section
    assert "hong-cha" in section
    assert "https://hong.example" in section
    assert "EUR" not in section
    assert "BYN" not in section


def test_empty_directory_says_nothing_was_found(monkeypatch) -> None:
    _install(monkeypatch, "empty")
    result = find_local_shops("Poland", "Warsaw", "biluochun", _ctx())
    assert result["status"] == "not_found"
    assert result["shops"] == []
    section = format_local_shops_section(result)
    assert "не нашлось" in section
    assert "http" not in section


def test_keyless_cap_does_not_read_an_eleventh_row_or_page(monkeypatch) -> None:
    calls = _install(monkeypatch, "cap")
    result = find_local_shops("Poland", "Warsaw", "biluochun", _ctx())
    assert result["status"] == "not_found"
    assert result["shops"] == []
    assert "eleventh-strong" not in json.dumps(result)
    for params in _company_calls(calls):
        assert _FORBIDDEN.isdisjoint(params)


def test_directory_errors_do_not_raise(monkeypatch) -> None:
    def boom(path: str, params: dict | None = None) -> dict:
        del path, params
        raise TimeoutError("slow")

    monkeypatch.setattr("tea_agent.local_shops.get_json", boom)
    result = find_local_shops("Poland", "Warsaw", "biluochun", _ctx())
    assert result["status"] == "error"
    assert result["shops"] == []
    assert "unavailable" in result["hint"]


def test_partial_query_failure_still_returns_shops(monkeypatch) -> None:
    payload = _load()["warsaw_green"]
    calls: list[str] = []

    def fake_get_json(path: str, params: dict | None = None) -> dict:
        query = dict(params or {})
        if path.startswith("/api/v2/tea/"):
            return payload["cards"]["biluochun"]
        calls.append(str(query.get("type")))
        if query.get("type") == "retail":
            return {"status": "error", "error": "timeout"}
        key = (
            f"{query.get('country', '')}|{query.get('city', '')}|"
            f"{query.get('type', '')}|{query.get('tea', '')}"
        )
        return payload["queries"][key]

    monkeypatch.setattr("tea_agent.local_shops.get_json", fake_get_json)
    result = find_local_shops("Poland", "Warsaw", "biluochun", _ctx())
    assert result["status"] == "success"
    assert "eta-room" in [shop["slug"] for shop in result["shops"]]
    assert "retail" in calls


def test_missing_city_does_not_call_the_directory(monkeypatch) -> None:
    calls = _install(monkeypatch, "warsaw_green")
    state: dict = {}
    result = find_local_shops("", "", "biluochun", _ctx(state))
    assert result["status"] == "need_location"
    assert result["shops"] == []
    assert calls == []
    assert state["user:location_prompted"] is True


def test_saved_city_is_used_when_arguments_are_empty(monkeypatch) -> None:
    _install(monkeypatch, "warsaw_green")
    state = {"user:city": "Warsaw", "user:country": "Poland", "city": "Warsaw", "country": "Poland"}
    result = find_local_shops("", "", "biluochun", _ctx(state))
    assert result["status"] == "success"
    assert result["city"] == "Warsaw"
    assert next(shop["slug"] for shop in result["shops"]) == "alpha-tea"


def test_unknown_class_does_not_invent_shops(monkeypatch) -> None:
    calls = _install(monkeypatch, "warsaw_green")
    result = find_local_shops("Poland", "Warsaw", "not-a-real-tea", _ctx())
    assert result["status"] == "unknown_class"
    assert result["shops"] == []
    assert _company_calls(calls) == []


def test_parse_place_infers_country_for_common_cities() -> None:
    warsaw = parse_place("Варшава")
    assert warsaw is not None
    assert (warsaw.city, warsaw.country, warsaw.label) == ("Warsaw", "Poland", "Варшава")
    both = parse_place("Warsaw, Poland")
    assert both is not None
    assert (both.city, both.country) == ("Warsaw", "Poland")
    minsk = parse_place("Минск, Беларусь")
    assert minsk is not None
    assert (minsk.city, minsk.country) == ("Minsk", "Belarus")
    assert parse_place("Неизвестныйград") is None
    france = parse_place("Brest, France")
    assert france is not None
    assert (france.city, france.country) == ("Brest", "France")
    brest = parse_place("Брест")
    assert brest is not None
    assert (brest.city, brest.country) == ("Brest", "Belarus")
    gdansk = parse_place("Gdansk")
    assert gdansk is not None
    assert gdansk.city == "Gdańsk"


def test_save_user_location_writes_session_keys() -> None:
    state: dict = {}
    result = save_user_location("Варшава", "", _ctx(state))  # type: ignore[arg-type]
    assert result["status"] == "success"
    assert state["city"] == "Warsaw"
    assert state["user:city"] == "Warsaw"
    assert state["country"] == "Poland"
    assert state["user:country"] == "Poland"
    assert state["user:city_label"] == "Варшава"
    repeated = save_user_location("Warsaw, Poland", "Poland", _ctx(state))  # type: ignore[arg-type]
    assert repeated["status"] == "success"
    assert repeated["country"] == "Poland"
    assert state["country"] == "Poland"


def test_reply_shows_only_tool_urls_and_a_separate_chip(monkeypatch) -> None:
    _install(monkeypatch, "warsaw_green")
    result = find_local_shops("Poland", "Warsaw", "biluochun", _ctx())
    text = (
        "1. Би Ло Чунь — зелёный чай.\n"
        "2. Лунцзин — мягкий утренний чай.\n"
        "3. Аньцзи Бай Ча — светлый вкус.\n"
        "Магазин: https://b2btea.com/en/c/invented-shop/ "
        "и [сайт](https://evil-shop.example/tea)"
    )
    updated = ensure_next_steps(
        text,
        products=[{"product_name": "Си Ху Лун Цзин", "product_url": LONGJING_URL}],
        local_shops=result,
    )
    assert "invented-shop" not in updated
    assert "evil-shop.example" not in updated
    assert updated.count("https://b2btea.com/en/c/alpha-tea/") == 1
    assert "https://alpha.example" in updated
    assert "https://zeta.example" in updated
    assert "https://eta.example" in updated
    assert "theta.example" not in updated
    assert LOCAL_SHOPS_HEADING in updated
    assert f"[{LOCAL_SHOPS_CHIP}]" in updated
    assert "Купить:" in updated
    assert LONGJING_URL in updated
    section = updated.split(LOCAL_SHOPS_HEADING, 1)[1].split("### Что дальше", 1)[0]
    assert "зелёный" in section
    assert "biluochun" not in section
    assert "Би Ло" not in section
    assert "EUR" not in section
    assert "BYN" not in section
    assert invented_buy_urls(updated) == []

    chunks, markup = prepare_telegram_reply(updated)
    body = "\n".join(chunks)
    assert "https://b2btea.com/en/c/alpha-tea/" in body
    assert markup is not None
    buttons = [button for row in markup.to_dict()["inline_keyboard"] for button in row]
    nearby = next(button for button in buttons if button["text"] == LOCAL_SHOPS_CHIP)
    assert "callback_data" in nearby
    assert "url" not in nearby
    buys = [button for button in buttons if str(button.get("text", "")).startswith("Купить")]
    assert buys
    assert all(button["url"].startswith("https://www.teashop.by/") for button in buys)
    assert all("b2btea.com" not in str(button.get("url", "")) for button in buttons)
    assert load_catalog()


def _tester_shops() -> dict:
    """The three shops from the 2026-10-04 tester note. No network."""
    return {
        "status": "success",
        "class_label_ru": "зелёный",
        "tea_class": "green",
        "city": "Warsaw",
        "country": "Poland",
        "shops": [
            {
                "name": "Teasome",
                "city": "Warsaw",
                "country": "Poland",
                "same_city": True,
                "url": "https://b2btea.com/en/c/teasome/",
                "website": "https://teasome.example",
            },
            {
                "name": "TEA EYE",
                "city": "Warsaw",
                "country": "Poland",
                "same_city": True,
                "url": "https://b2btea.com/en/c/tea-eye/",
                "website": "https://teaeye.example",
            },
            {
                "name": "Tea Mail Poland",
                "city": "Krakow",
                "country": "Poland",
                "same_city": False,
                "url": "https://b2btea.com/pl/c/tea-mail-poland/",
                "website": "https://teamail.example",
            },
        ],
    }


_SHOP_PROSE = (
    "Teasome — карточка магазина, официальный сайт "
    "[карточка](https://b2btea.com/en/c/teasome/) "
    "[сайт](https://evil.example/teasome).\n"
    "TEA EYE — карточка магазина, официальный сайт.\n"
    "Tea Mail Poland — карточка магазина, официальный сайт."
)


def _shops_section(text: str) -> str:
    chunk = text.split(LOCAL_SHOPS_HEADING, 1)[1]
    return chunk.split("### Что дальше", 1)[0].strip()


def _warsaw_state(payload: dict) -> dict:
    return {
        "city": "Warsaw",
        "user:city": "Warsaw",
        "country": "Poland",
        "user:country": "Poland",
        LOCAL_SHOPS_LAST_KEY: payload,
        LOCAL_SHOPS_SAVED_KEY: "yes",
    }


def test_follow_up_without_a_tool_call_repeats_the_same_shop_lines() -> None:
    """The model re-lists shops from history and does not call find_local_shops.

    Before the session copy, that reply had no «### Где рядом». A rewritten
    recommendation also dropped b2btea URLs and left the bare «карточка» label.
    """
    payload = _tester_shops()
    first = ensure_next_steps(
        "Рядом есть магазины этого класса.",
        local_shops=payload,
    )
    second = ensure_next_steps(
        _SHOP_PROSE,
        stored_shops=payload,
        user_text="Где ссылки на магазины?",
    )
    assert _shops_section(first) == _shops_section(second)
    assert f"{LOCAL_SHOPS_HEADING}\n{_shops_section(second)}" == format_local_shops_section(
        payload
    )
    lines = [line for line in _shops_section(second).splitlines() if "[Карточка](" in line]
    assert len(lines) == 3
    assert lines[0].startswith("Teasome — Warsaw, тот же город.")
    assert "[Карточка](https://b2btea.com/en/c/teasome/)" in lines[0]
    assert "[Сайт](https://teasome.example)" in lines[0]
    assert "TEA EYE — Warsaw, тот же город." in lines[1]
    assert "Tea Mail Poland — Krakow, онлайн в стране." in lines[2]
    body = second.split(LOCAL_SHOPS_HEADING, 1)[0]
    assert "карточка магазина" not in body
    assert "официальный сайт" not in body
    assert "Teasome" not in body
    assert "evil.example" not in second
    assert second.count("https://b2btea.com/en/c/teasome/") == 1
    assert invented_buy_urls(second) == []

    html = markdown_to_telegram_html(second)
    assert "<b>Где рядом</b>" in html
    assert '<a href="https://b2btea.com/en/c/teasome/">Карточка</a>' in html
    assert '<a href="https://teasome.example">Сайт</a>' in html
    assert '<a href="https://teaeye.example">Сайт</a>' in html
    assert '<a href="https://teamail.example">Сайт</a>' in html


def test_naming_the_saved_shops_rebuilds_the_block_without_a_shop_question() -> None:
    payload = _tester_shops()
    updated = ensure_next_steps(_SHOP_PROSE, stored_shops=payload, user_text="повторите")
    assert _shops_section(updated) == format_local_shops_section(payload).split(
        LOCAL_SHOPS_HEADING, 1
    )[1].strip()


def test_a_shop_question_rebuilds_the_block_even_if_names_are_missing() -> None:
    payload = _tester_shops()
    updated = ensure_next_steps(
        "Сейчас пришлю.",
        stored_shops=payload,
        user_text="Где ссылки на магазины?",
    )
    assert "Сейчас пришлю." in updated.split(LOCAL_SHOPS_HEADING, 1)[0]
    assert _shops_section(updated) == format_local_shops_section(payload).split(
        LOCAL_SHOPS_HEADING, 1
    )[1].strip()


def test_unrelated_reply_does_not_insert_stored_shops() -> None:
    updated = ensure_next_steps(
        "80 °C и около трёх граммов.",
        stored_shops=_tester_shops(),
        user_text="как заварить лунцзин?",
    )
    assert updated == "80 °C и около трёх граммов."
    assert LOCAL_SHOPS_HEADING not in updated


@pytest.mark.parametrize(
    "user_text",
    [
        "магазин",
        "магазины рядом",
        "где рядом",
        "где купить рядом",
        "[магазины рядом]",
        "Где ссылки на магазины?",
        "ссылки на магазины",
        "дай ссылку на магазин",
    ],
)
def test_shop_questions_reattach_the_saved_block(user_text: str) -> None:
    payload = _tester_shops()
    updated = ensure_next_steps(
        "Сейчас пришлю.",
        stored_shops=payload,
        user_text=user_text,
    )
    assert LOCAL_SHOPS_HEADING in updated
    assert "teasome.example" in updated
    assert "Сейчас пришлю." in updated.split(LOCAL_SHOPS_HEADING, 1)[0]


@pytest.mark.parametrize(
    "user_text",
    [
        "дай ссылку на чай",
        "пришли ссылку на лунцзин",
        "где купить лунцзин",
        "ссылка",
        "b2btea",
    ],
)
def test_a_product_link_question_does_not_reattach_saved_shops(user_text: str) -> None:
    reply = (
        "Ссылка на Лунцзин: "
        "[Купить](https://www.teashop.by/product/longjing-1/)"
    )
    updated = ensure_next_steps(
        reply,
        stored_shops=_tester_shops(),
        user_text=user_text,
    )
    assert updated == reply
    assert LOCAL_SHOPS_HEADING not in updated
    assert "teasome.example" not in updated
    assert "b2btea.com" not in updated


def test_tea_link_question_stays_shop_free_on_a_recommendation() -> None:
    reply = (
        "1. Би Ло Чунь — зелёный чай.\n"
        "2. Лунцзин — мягкий утренний чай.\n"
        "3. Аньцзи Бай Ча — светлый вкус.\n"
        f"Ссылка: [Купить: Лунцзин]({LONGJING_URL})"
    )
    updated = ensure_next_steps(
        reply,
        stored_shops=_tester_shops(),
        user_text="дай ссылку на чай",
    )
    assert LOCAL_SHOPS_HEADING not in updated
    assert "teasome.example" not in updated
    assert "b2btea.com" not in updated
    assert LONGJING_URL in updated
    assert "### Что дальше" in updated


def test_naming_a_saved_shop_restores_the_block_on_a_product_link_question() -> None:
    payload = _tester_shops()
    updated = ensure_next_steps(
        "Карточка Teasome: официальный сайт.",
        stored_shops=payload,
        user_text="дай ссылку на чай",
    )
    assert LOCAL_SHOPS_HEADING in updated
    assert "teasome.example" in updated


def test_numbered_shop_list_is_not_a_second_list_or_a_buy_link() -> None:
    payload = _tester_shops()
    text = (
        "1. Teasome — карточка магазина, официальный сайт.\n"
        "2. TEA EYE — карточка магазина, официальный сайт.\n"
        "3. Tea Mail Poland — карточка магазина, официальный сайт.\n"
    )
    updated = ensure_next_steps(
        text, stored_shops=payload, user_text="где ссылки на магазины?"
    )
    assert "Купить: Teasome" not in updated
    assert "Купить: TEA EYE" not in updated
    assert "Купить: Tea Mail" not in updated
    assert updated.count("[Карточка](") == 3
    assert "Teasome" not in updated.split(LOCAL_SHOPS_HEADING, 1)[0]


def test_not_found_and_error_keep_one_line_and_do_not_reuse_links() -> None:
    payload = _tester_shops()
    missing = ensure_next_steps(
        "Ничего.",
        local_shops={"status": "not_found", "shops": [], "class_label_ru": "зелёный"},
        stored_shops=payload,
        user_text="где магазины?",
    )
    assert "не нашлось" in missing
    assert "Карточка" not in missing
    assert "http" not in missing.split("### Что дальше", 1)[0]

    failed = ensure_next_steps(
        "Справочник молчит.",
        local_shops={"status": "error", "shops": []},
        stored_shops=payload,
        user_text="ссылки на магазины",
    )
    assert "недоступен" in failed
    assert "Карточка" not in failed
    assert "http" not in failed.split("### Что дальше", 1)[0]
    assert "teasome.example" not in failed


def test_need_location_this_turn_does_not_restore_old_shops() -> None:
    updated = ensure_next_steps(
        "В каком вы городе?",
        local_shops={"status": "need_location", "shops": []},
        stored_shops=_tester_shops(),
        user_text="магазины рядом",
    )
    assert LOCAL_SHOPS_HEADING not in updated
    assert "b2btea.com" not in updated


def test_cleared_store_does_not_invent_a_shop_block() -> None:
    updated = ensure_next_steps(
        _SHOP_PROSE,
        stored_shops={"status": "cleared", "shops": []},
        user_text="Где ссылки на магазины?",
    )
    assert LOCAL_SHOPS_HEADING not in updated
    assert "teasome.example" not in updated


def test_success_is_stored_outside_temp_and_not_found_does_not_replace_it(
    monkeypatch,
) -> None:
    _install(monkeypatch, "warsaw_green")
    state: dict = {}
    ctx = _ctx(state)
    result = find_local_shops("Poland", "Warsaw", "biluochun", ctx)
    collect_local_shop_hits(SimpleNamespace(name="find_local_shops"), {}, ctx, result)  # type: ignore[arg-type]

    assert state[LOCAL_SHOPS_KEY]["status"] == "success"
    saved = state[LOCAL_SHOPS_LAST_KEY]
    assert not LOCAL_SHOPS_LAST_KEY.startswith("temp:")
    assert saved["status"] == "success"
    assert saved["city"] == "Warsaw"
    assert saved["country"] == "Poland"
    assert saved["class_label_ru"] == "зелёный"
    assert [shop["name"] for shop in saved["shops"]] == [
        "Alpha Tea",
        "Zeta Leaf",
        "Eta Room",
    ]
    assert all(shop["url"] and shop["website"] for shop in saved["shops"])
    assert state[LOCAL_SHOPS_SAVED_KEY] == "yes"

    prose = "\n".join(
        f"{shop['name']} — карточка магазина, официальный сайт." for shop in saved["shops"]
    )
    first = ensure_next_steps("Класс зелёный.", local_shops=dict(state[LOCAL_SHOPS_KEY]))
    follow_state = {key: value for key, value in state.items() if not str(key).startswith("temp:")}
    assert LOCAL_SHOPS_KEY not in follow_state
    second = ensure_next_steps(
        prose,
        stored_shops=follow_state[LOCAL_SHOPS_LAST_KEY],
        user_text="Где ссылки на магазины?",
    )
    assert _shops_section(first) == _shops_section(second)
    assert f"{LOCAL_SHOPS_HEADING}\n{_shops_section(second)}" == format_local_shops_section(saved)

    collect_local_shop_hits(
        SimpleNamespace(name="find_local_shops"),  # type: ignore[arg-type]
        {},
        ctx,  # type: ignore[arg-type]
        {
            "status": "not_found",
            "shops": [],
            "class_label_ru": "красный",
            "city": "Warsaw",
            "country": "Poland",
        },
    )
    assert state[LOCAL_SHOPS_KEY]["status"] == "not_found"
    assert [shop["name"] for shop in state[LOCAL_SHOPS_LAST_KEY]["shops"]] == [
        "Alpha Tea",
        "Zeta Leaf",
        "Eta Room",
    ]
    assert state[LOCAL_SHOPS_SAVED_KEY] == "yes"

    collect_local_shop_hits(
        SimpleNamespace(name="find_local_shops"),  # type: ignore[arg-type]
        {},
        ctx,  # type: ignore[arg-type]
        {"status": "error", "shops": [], "city": "Warsaw", "country": "Poland"},
    )
    assert state[LOCAL_SHOPS_KEY]["status"] == "error"
    assert state[LOCAL_SHOPS_LAST_KEY]["status"] == "success"


def test_save_user_location_clears_shops_only_when_the_city_changes() -> None:
    payload = _tester_shops()
    same = _warsaw_state(payload)
    kept = save_user_location("Варшава", "", _ctx(same))  # type: ignore[arg-type]
    assert kept["status"] == "success"
    assert same[LOCAL_SHOPS_LAST_KEY]["status"] == "success"
    assert same[LOCAL_SHOPS_LAST_KEY]["shops"][0]["name"] == "Teasome"
    assert same[LOCAL_SHOPS_SAVED_KEY] == "yes"

    moved = _warsaw_state(payload)
    changed = save_user_location("Минск", "", _ctx(moved))  # type: ignore[arg-type]
    assert changed["city"] == "Minsk"
    assert moved["city"] == "Minsk"
    assert moved[LOCAL_SHOPS_LAST_KEY] == {"status": "cleared", "shops": []}
    assert moved[LOCAL_SHOPS_SAVED_KEY] == ""


def test_find_local_shops_clears_saved_shops_when_it_changes_the_city(monkeypatch) -> None:
    _install(monkeypatch, "warsaw_green")
    state = _warsaw_state(
        {
            "status": "success",
            "city": "Warsaw",
            "country": "Poland",
            "class_label_ru": "зелёный",
            "shops": [
                {
                    "name": "Keep Me",
                    "city": "Warsaw",
                    "same_city": True,
                    "url": "https://b2btea.com/en/c/keep/",
                    "website": "https://keep.example",
                }
            ],
        }
    )
    ctx = _ctx(state)
    same = find_local_shops("Poland", "Warsaw", "biluochun", ctx)
    assert same["status"] == "success"
    assert state[LOCAL_SHOPS_LAST_KEY]["shops"][0]["name"] == "Keep Me"
    collect_local_shop_hits(SimpleNamespace(name="find_local_shops"), {}, ctx, same)  # type: ignore[arg-type]
    assert state[LOCAL_SHOPS_LAST_KEY]["shops"][0]["name"] == "Alpha Tea"

    moved = find_local_shops("Belarus", "Minsk", "biluochun", ctx)
    assert moved["status"] == "error"
    assert state["city"] == "Minsk"
    assert state[LOCAL_SHOPS_LAST_KEY]["status"] == "cleared"
    assert state[LOCAL_SHOPS_SAVED_KEY] == ""
    collect_local_shop_hits(SimpleNamespace(name="find_local_shops"), {}, ctx, moved)  # type: ignore[arg-type]
    assert state[LOCAL_SHOPS_LAST_KEY]["status"] == "cleared"
    minsk_success = {
        "status": "success",
        "class_label_ru": "зелёный",
        "tea_class": "green",
        "city": "Minsk",
        "country": "Belarus",
        "shops": [
            {
                "name": "Minsk Tea",
                "city": "Minsk",
                "country": "Belarus",
                "same_city": True,
                "url": "https://b2btea.com/en/c/minsk-tea/",
                "website": "https://minsk.example",
            }
        ],
    }
    collect_local_shop_hits(
        SimpleNamespace(name="find_local_shops"),  # type: ignore[arg-type]
        {},
        ctx,  # type: ignore[arg-type]
        minsk_success,
    )
    assert state[LOCAL_SHOPS_LAST_KEY]["city"] == "Minsk"
    assert state[LOCAL_SHOPS_LAST_KEY]["shops"][0]["name"] == "Minsk Tea"
    assert state[LOCAL_SHOPS_SAVED_KEY] == "yes"


def test_callback_uses_the_saved_list_on_a_follow_up_with_no_tool_call() -> None:
    payload = _tester_shops()
    ctx = SimpleNamespace(
        state=_warsaw_state(payload),
        user_content=types.Content(
            role="user",
            parts=[types.Part.from_text(text="Где ссылки на магазины?")],
        ),
    )
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=_SHOP_PROSE)],
        )
    )
    updated = attach_next_steps_to_response(ctx, response)  # type: ignore[arg-type]
    assert updated is not None
    text = updated.content.parts[0].text or ""
    assert _shops_section(text) == format_local_shops_section(payload).split(
        LOCAL_SHOPS_HEADING, 1
    )[1].strip()
    assert "карточка магазина" not in text.split(LOCAL_SHOPS_HEADING, 1)[0]


def test_callback_does_not_restore_shops_for_a_different_city() -> None:
    state = _warsaw_state(_tester_shops())
    state["city"] = "Minsk"
    state["user:city"] = "Minsk"
    state["country"] = "Belarus"
    state["user:country"] = "Belarus"
    ctx = SimpleNamespace(
        state=state,
        user_content=types.Content(
            role="user",
            parts=[types.Part.from_text(text="Где ссылки на магазины?")],
        ),
    )
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text="Ссылки на магазины:\n" + _SHOP_PROSE)],
        )
    )
    updated = attach_next_steps_to_response(ctx, response)  # type: ignore[arg-type]
    text = _SHOP_PROSE if updated is None else (updated.content.parts[0].text or "")
    assert "teasome.example" not in text
    assert LOCAL_SHOPS_HEADING not in text


def test_instruction_tells_the_model_not_to_write_a_shop_list() -> None:
    text = instruction_text()
    assert "### Где рядом" in text
    assert "свой список магазинов" in text
    assert "не больше одного короткого предложения" in text
    assert "{local_shops_saved?}" in text
    assert "local_shops_saved пустой" in text
    assert "find_local_shops" in text
