# ruff: noqa: RUF001
"""Local b2btea shops (TEA-32). Fixture responses only — no live network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tea_agent.local_shops import directory_tea_key, find_local_shops
from tea_agent.location import parse_place, save_user_location
from tea_agent.next_steps import (
    LOCAL_SHOPS_CHIP,
    LOCAL_SHOPS_HEADING,
    ensure_next_steps,
    format_local_shops_section,
    invented_buy_urls,
)
from tea_agent.shop_catalog import load_catalog
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
