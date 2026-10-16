"""find_best_price reads the saved index and the session currency. No network."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from google.adk.models.llm_response import LlmResponse
from google.genai import types

from tea_agent.agent import instruction_text
from tea_agent.currency import convert_money, price_display
from tea_agent.eu_price_index import reload_index
from tea_agent.fx_rates import SOURCE_NBRB, FxQuote, clear_fx_cache, set_fx_provider
from tea_agent.next_steps import (
    EU_PRICE_HEADING,
    EU_PRICE_KEY,
    HEADING,
    attach_next_steps_to_response,
    collect_turn_hits,
    ensure_next_steps,
    format_eu_price_section,
)
from tea_agent.tools import find_best_price

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
_LEADING = re.compile(r"(\d+),(\d{2})")


def _quote(**overrides) -> FxQuote:
    data = {
        "source": SOURCE_NBRB,
        "fetched_at": NOW,
        "byn_per_eur": Decimal("4"),
        "eur_date": date(2026, 10, 4),
        "byn_per_usd": Decimal("2"),
        "usd_date": date(2026, 10, 4),
        "byn_per_pln": Decimal("1"),
        "pln_date": date(2026, 10, 4),
    }
    data.update(overrides)
    return FxQuote(**data)


def _nbrb_vitrine() -> FxQuote:
    return FxQuote(
        source=SOURCE_NBRB,
        fetched_at=NOW,
        byn_per_eur=Decimal("3.3818"),
        eur_date=date(2026, 10, 4),
        byn_per_usd=Decimal("3.005"),
        usd_date=date(2026, 10, 4),
    )


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _offer(**overrides) -> dict:
    row = {
        "tea_id": "xihu-longjing",
        "category": "green",
        "title": "Long Jing",
        "shop_id": "thetea",
        "shop": "TheTea",
        "url": "https://thetea.pl/p/long-jing?pack=100",
        "price": 20,
        "currency": "EUR",
        "pack_size_g": 100,
        "pack_size_unclear": False,
        "price_per_100g": 20,
        "in_stock": True,
        "fetched_at": "2026-10-15T00:00:00Z",
    }
    row.update(overrides)
    return row


def _install(monkeypatch, tmp_path, offers: list[dict]) -> None:
    path = tmp_path / "eu_green_price_index.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "last_checked": "2026-10-15",
                "generated_on": "2026-10-15",
                "offers": offers,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tea_agent.eu_price_index.index_path", lambda: path)
    reload_index()


def _leading(text: str) -> Decimal:
    match = _LEADING.search(text)
    assert match is not None
    return Decimal(f"{match.group(1)}.{match.group(2)}")


def setup_module() -> None:
    set_fx_provider(None)
    clear_fx_cache()


def teardown_module() -> None:
    set_fx_provider(None)
    clear_fx_cache()
    reload_index()


def test_convert_money_matches_the_vitrine_line() -> None:
    quote = _nbrb_vitrine()
    assert convert_money(25, "BYN", "EUR", quote).text == price_display(25, "EUR", quote)
    assert convert_money(25, "BYN", "USD", quote).text == price_display(25, "USD", quote)
    assert convert_money(25, "BYN", "BYN", quote).text == "25,00 BYN"
    exact = convert_money(21, "EUR", "EUR", quote)
    assert exact.text == "21,00 EUR"
    assert exact.converted is False
    assert "~" not in exact.text
    pln = convert_money(40, "PLN", "EUR", _quote())
    assert pln.text == "~10,00 EUR (40,00 PLN, курс NBRB 04.10.2026)"
    assert pln.amount == Decimal("10.00")
    assert convert_money(40, "PLN", "EUR", None).text is None


def test_empty_query_and_bad_category(tmp_path, monkeypatch) -> None:
    _install(monkeypatch, tmp_path, [_offer()])
    empty = find_best_price("  ")
    assert empty["status"] == "error"
    assert empty["error"] == "need_query"
    assert empty["offers"] == []

    invalid = find_best_price("long jing", category="herbal")
    assert invalid["status"] == "error"
    assert invalid["error"] == "invalid_category"
    assert invalid["offers"] == []

    mismatch = find_best_price("улун", category="green")
    assert mismatch["status"] == "error"
    assert mismatch["error"] == "category_mismatch"
    assert mismatch["offers"] == []


def test_ranks_by_price_per_100g_and_skips_stock_and_unclear_packs(tmp_path, monkeypatch) -> None:
    calls: list[object] = []

    def provider(now):
        calls.append(now)
        return _quote()

    set_fx_provider(provider)
    _install(
        monkeypatch,
        tmp_path,
        [
            _offer(
                shop="Tiny",
                url="https://shop.example/tiny",
                price=5,
                pack_size_g=10,
                price_per_100g=50,
            ),
            _offer(
                shop="Best",
                url="https://shop.example/best",
                price=15,
                pack_size_g=100,
                price_per_100g=15,
            ),
            _offer(
                shop="Mid",
                url="https://shop.example/mid",
                price=20,
                pack_size_g=100,
                price_per_100g=20,
            ),
            _offer(
                shop="Large",
                url="https://shop.example/large",
                price=40,
                pack_size_g=200,
                price_per_100g=20,
            ),
            _offer(
                shop="Fourth",
                url="https://shop.example/fourth",
                price=30,
                pack_size_g=100,
                price_per_100g=30,
            ),
            _offer(
                shop="SoldOut",
                url="https://shop.example/out",
                price=1,
                price_per_100g=1,
                in_stock=False,
            ),
            _offer(
                shop="Unclear",
                url="https://shop.example/unclear",
                price=1,
                price_per_100g=1,
                pack_size_unclear=True,
                pack_size_g=None,
            ),
        ],
    )
    result = find_best_price("Long Jing")
    assert result["status"] == "success"
    assert result["requested_currency"] == "EUR"
    assert result["fx_status"] == "not_needed"
    assert result["fx"] is None
    assert calls == []
    assert [row["shop"] for row in result["offers"]] == ["Best", "Mid", "Large"]
    assert result["offers"][0]["pack_size"] == "100 g"
    assert result["offers"][0]["price"] == "15,00 EUR"
    assert result["offers"][0]["price_per_100g"] == "15,00 EUR"
    assert result["offers"][0]["url"] == "https://shop.example/best"
    assert "SoldOut" not in {row["shop"] for row in result["offers"]}
    assert "Unclear" not in {row["shop"] for row in result["offers"]}
    assert "Tiny" not in {row["shop"] for row in result["offers"]}


def test_pln_and_eur_rank_in_the_session_currency(tmp_path, monkeypatch) -> None:
    set_fx_provider(lambda _now: _quote())
    _install(
        monkeypatch,
        tmp_path,
        [
            _offer(
                shop="EuroShop",
                url="https://shop.example/eur",
                currency="EUR",
                price=12,
                price_per_100g=12,
            ),
            _offer(
                shop="ZlotyShop",
                url="https://tea.example/pln?pack=50",
                currency="PLN",
                price=40,
                pack_size_g=100,
                price_per_100g=40,
            ),
        ],
    )
    result = find_best_price("лунцзин")
    assert [row["shop"] for row in result["offers"]] == ["ZlotyShop", "EuroShop"]
    assert result["offers"][0]["price"] == "~10,00 EUR (40,00 PLN, курс NBRB 04.10.2026)"
    assert result["offers"][0]["price_per_100g"] == "~10,00 EUR (40,00 PLN, курс NBRB 04.10.2026)"
    assert result["offers"][0]["url"] == "https://tea.example/pln?pack=50"
    assert result["offers"][1]["price"] == "12,00 EUR"
    assert result["fx_status"] == "ok"
    assert result["fx"]["source"] == "NBRB"
    assert result["index_date"] == "2026-10-15"

    usd = find_best_price(
        "лунцзин",
        tool_context=_Ctx({"user:currency": "USD", "city": "Minsk"}),  # type: ignore[arg-type]
    )
    assert usd["requested_currency"] == "USD"
    assert usd["offers"][0]["price"].startswith("~20,00 USD")

    byn = find_best_price(
        "лунцзин",
        tool_context=_Ctx({"currency": "BYN"}),  # type: ignore[arg-type]
    )
    assert byn["offers"][0]["price"] == "~40,00 BYN (40,00 PLN, курс NBRB 04.10.2026)"


def test_city_does_not_pick_currency_and_missing_rate_is_not_a_mixed_ranking(
    tmp_path, monkeypatch
) -> None:
    set_fx_provider(lambda _now: _quote(byn_per_pln=None, pln_date=None))
    _install(
        monkeypatch,
        tmp_path,
        [
            _offer(shop="EuroShop", url="https://shop.example/eur", price_per_100g=30, price=30),
            _offer(
                shop="ZlotyShop",
                url="https://shop.example/pln",
                currency="PLN",
                price=4,
                price_per_100g=4,
            ),
        ],
    )
    partial = find_best_price("Long Jing", tool_context=_Ctx({"city": "Warsaw"}))  # type: ignore[arg-type]
    assert partial["requested_currency"] == "EUR"
    assert partial["status"] == "success"
    assert partial["fx_status"] == "partial"
    assert partial["skipped_fx"] == 1
    assert [row["shop"] for row in partial["offers"]] == ["EuroShop"]

    _install(
        monkeypatch,
        tmp_path,
        [
            _offer(
                shop="ZlotyShop",
                url="https://shop.example/pln",
                currency="PLN",
                price=4,
                price_per_100g=4,
            )
        ],
    )
    missing = find_best_price("Long Jing")
    assert missing["status"] == "fx_unavailable"
    assert missing["offers"] == []
    assert missing["fx"] is None
    assert "PLN" in missing["hint"]


def test_nothing_matches_is_an_empty_result(tmp_path, monkeypatch) -> None:
    set_fx_provider(lambda _now: _quote())
    _install(monkeypatch, tmp_path, [_offer()])
    missing = find_best_price("qixyzzy")
    assert missing["status"] == "not_found"
    assert missing["offers"] == []
    assert "Do not invent" in missing["hint"]

    _install(
        monkeypatch,
        tmp_path,
        [_offer(in_stock=False, url="https://shop.example/out")],
    )
    stock = find_best_price("Long Jing")
    assert stock["status"] == "not_found"
    assert stock["offers"] == []
    assert "clear pack" in stock["hint"]


def test_matching_is_fuzzy_and_category_aware(tmp_path, monkeypatch) -> None:
    set_fx_provider(lambda _now: _quote())
    _install(
        monkeypatch,
        tmp_path,
        [
            _offer(shop="WestLake", url="https://shop.example/xihu", price_per_100g=30, price=30),
            _offer(
                tea_id="dafu-long-jing",
                title="Da Fo Long Jing",
                shop="DaFo",
                url="https://shop.example/dafo",
                price_per_100g=10,
                price=10,
            ),
            _offer(
                tea_id="fanjingshan-hongcha",
                category="black",
                title="Fanjingshan Hong Cha",
                shop="Fan",
                url="https://shop.example/fan",
                price_per_100g=5,
                price=5,
            ),
            _offer(
                tea_id="anji-baicha",
                category="green",
                title="Anji Bai Cha",
                shop="Anji",
                url="https://shop.example/anji",
                price_per_100g=18,
                price=18,
            ),
            _offer(
                tea_id="tie-guanyin",
                category="oolong",
                title="Tie Guan Yin",
                shop="TGY",
                url="https://shop.example/tgy",
                price_per_100g=22,
                price=22,
            ),
            _offer(
                tea_id="jin-xuan",
                category="oolong",
                title="Jin Xuan",
                shop="Milk",
                url="https://shop.example/milk",
                price_per_100g=16,
                price=16,
            ),
            _offer(
                tea_id="7542",
                category="puerh",
                title="Sheng cake",
                shop="Cake",
                url="https://shop.example/cake",
                price_per_100g=40,
                price=40,
            ),
            _offer(
                tea_id="alishan-wulong",
                category="oolong",
                title="GABA Oolong Alishan",
                shop="GabaShop",
                url="https://shop.example/gaba",
                price_per_100g=11,
                price=11,
            ),
            _offer(
                tea_id="other-green",
                category="green",
                title="Plain green leaf",
                shop="Plain",
                url="https://shop.example/plain",
                price_per_100g=3,
                price=3,
            ),
        ],
    )
    russian = find_best_price("лунцзин")
    assert [row["shop"] for row in russian["offers"]] == ["WestLake"]

    english = find_best_price("Long Jing")
    assert {row["shop"] for row in english["offers"]} == {"DaFo", "WestLake"}
    assert english["offers"][0]["shop"] == "DaFo"

    assert find_best_price("longjng")["offers"][0]["tea_id"] == "xihu-longjing"
    assert find_best_price("anji")["offers"][0]["shop"] == "Anji"
    assert "Fan" not in {row["shop"] for row in find_best_price("anji")["offers"]}
    assert find_best_price("tie guan yin")["offers"][0]["tea_id"] == "tie-guanyin"
    assert find_best_price("те гуань инь")["offers"][0]["shop"] == "TGY"
    assert find_best_price("молочный улун")["offers"][0]["tea_id"] == "jin-xuan"
    assert find_best_price("GABA")["offers"][0]["shop"] == "GabaShop"

    shelf = find_best_price("пуэр")
    assert shelf["status"] == "success"
    assert shelf["category"] == "pu-erh"
    assert [row["shop"] for row in shelf["offers"]] == ["Cake"]

    oolong = find_best_price("улун")
    assert oolong["category"] == "oolong"
    assert {row["category"] for row in oolong["offers"]} == {"oolong"}
    assert "Plain" not in {row["shop"] for row in oolong["offers"]}

    filtered = find_best_price("Long Jing", category="oolong")
    assert filtered["status"] == "not_found"
    assert filtered["offers"] == []

    red = find_best_price("Fanjingshan", category="red")
    assert red["offers"][0]["shop"] == "Fan"
    assert red["category"] == "black"


def test_reply_block_uses_tool_links_and_prices(tmp_path, monkeypatch) -> None:
    set_fx_provider(lambda _now: _quote())
    url = "https://thetea.pl/produkt/long-jing/?attribute_pa_waga=50-g"
    _install(
        monkeypatch,
        tmp_path,
        [_offer(url=url, price=21, price_per_100g=42, currency="PLN", pack_size_g=50)],
    )
    result = find_best_price("Long Jing")
    section = format_eu_price_section(result)
    assert section.startswith(EU_PRICE_HEADING)
    assert "TheTea" in section
    assert "50 g" in section
    assert "~10,50 EUR" in section
    assert url in section
    assert "15.10.2026" in section
    assert "teashop.by" in section

    body = (
        "До 20 EUR тоже можно.\n"
        "Long Jing — ~9,99 EUR.\n"
        f"[Товар]({url})"
    )
    updated = ensure_next_steps(body, eu_prices=result)
    assert EU_PRICE_HEADING in updated
    assert HEADING not in updated
    assert "до 20 EUR" in updated or "До 20 EUR" in updated
    assert "9,99" not in updated
    assert updated.count(url) == 1

    state: dict = {}
    ctx = SimpleNamespace(state=state)
    collect_turn_hits(SimpleNamespace(name="find_best_price"), {}, ctx, result)  # type: ignore[arg-type]
    assert state[EU_PRICE_KEY]["offers"][0]["url"] == url
    response = LlmResponse(
        content=types.Content(role="model", parts=[types.Part.from_text(text="Нашёл вариант.")])
    )
    rendered = attach_next_steps_to_response(ctx, response)  # type: ignore[arg-type]
    assert rendered is not None
    text = rendered.content.parts[0].text
    assert url in text
    assert "attribute_pa_waga=50-g" in text
    assert HEADING not in text


def test_prompt_registers_the_tool() -> None:
    text = instruction_text()
    assert "find_best_price" in text
    assert EU_PRICE_HEADING in text
    assert "eu_green_price_index.json" in text
    from tea_agent.agent import build_root_agent

    names = [getattr(tool, "__name__", "") for tool in build_root_agent().tools]
    assert "find_best_price" in names


def test_real_index_longjing_stays_offline(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError(f"network {args[:1]} {kwargs}")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr("tea_agent.fx_rates._http_get", boom)
    set_fx_provider(lambda _now: _quote())
    reload_index()
    result = find_best_price("лунцзин")
    assert result["status"] == "success"
    assert result["source"] == "data/eu_green_price_index.json"
    assert 1 <= len(result["offers"]) <= 3
    amounts = [_leading(row["price_per_100g"]) for row in result["offers"]]
    assert amounts == sorted(amounts)
    for row in result["offers"]:
        assert row["shop"]
        assert row["product_name"]
        assert re.fullmatch(r"\d+ g", row["pack_size"])
        assert row["price"]
        assert row["price_per_100g"]
        assert row["url"].startswith("https://")
        assert row["in_stock"] is True
    assert find_best_price("лунцзин", category="oolong")["offers"] == []
    oolong = find_best_price("улун")
    assert oolong["status"] == "success"
    assert len(oolong["offers"]) == 3
    assert {row["category"] for row in oolong["offers"]} == {"oolong"}
