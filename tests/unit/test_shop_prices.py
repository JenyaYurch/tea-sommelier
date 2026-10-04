"""Vitrine prices in EUR, USD, and BYN. Fixture rates only — no live network."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from tea_agent.currency import (
    DEFAULT_CURRENCY,
    annotate_product,
    currency_from_state,
    price_display,
    show_price,
)
from tea_agent.fx_rates import SOURCE_ERAPI, SOURCE_NBRB, FxQuote, set_fx_provider
from tea_agent.location import save_user_location
from tea_agent.next_steps import (
    LOCAL_SHOPS_HEADING,
    VITRINE_PRICE_HEADING,
    ensure_next_steps,
    format_local_shops_section,
    format_vitrine_price_section,
    strip_model_shop_prices,
)
from tea_agent.shop_catalog import reload_catalog
from tea_agent.tools import find_in_shop

NOW = datetime(2026, 10, 4, 12, 0)
BYN = Decimal("25")
EUR_LINE = "~7,39 EUR (25,00 BYN, курс NBRB 04.10.2026)"
USD_LINE = "~8,32 USD (25,00 BYN, курс NBRB 04.10.2026)"
ER_LINE = "~7,39 EUR (25,00 BYN, курс ExchangeRate-API 04.10.2026)"
BYN_LINE = "25,00 BYN"
TEA = "Дунтин Би Ло Чунь"
URL = "https://www.teashop.by/product/duntin-bi-lo-chun/"


def _nbrb(**overrides) -> FxQuote:
    data = {
        "source": SOURCE_NBRB,
        "fetched_at": NOW,
        "byn_per_eur": Decimal("3.3818"),
        "eur_date": date(2026, 10, 4),
        "byn_per_usd": Decimal("3.005"),
        "usd_date": date(2026, 10, 4),
    }
    data.update(overrides)
    return FxQuote(**data)


def _erapi() -> FxQuote:
    return FxQuote(
        source=SOURCE_ERAPI,
        fetched_at=NOW,
        byn_per_eur=Decimal("3.381805"),
        eur_date=date(2026, 10, 4),
        byn_per_usd=Decimal("3.381805") / Decimal("1.125133"),
        usd_date=date(2026, 10, 4),
    )


def test_default_currency_is_eur_and_city_does_not_change_it() -> None:
    assert DEFAULT_CURRENCY == "EUR"
    assert currency_from_state(None) == "EUR"
    assert currency_from_state({}) == "EUR"
    assert currency_from_state({"city": "Minsk", "country": "Belarus"}) == "EUR"
    state: dict = {}
    save_user_location("Минск", "", _Ctx(state))  # type: ignore[arg-type]
    assert state["city"] == "Minsk"
    assert "currency" not in state
    assert currency_from_state(state) == "EUR"


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def test_default_eur_explicit_usd_explicit_byn_and_missing_price() -> None:
    quote = _nbrb()
    eur = show_price(BYN, currency_from_state(None), quote)
    usd = show_price(BYN, "USD", quote)
    byn = show_price(BYN, "BYN", quote)
    missing = show_price(None, "EUR", quote)

    assert eur.text == EUR_LINE
    assert eur.display_currency == "EUR"
    assert eur.rate_source == "NBRB"
    assert eur.rate_date == "2026-10-04"
    assert eur.text.startswith("~")
    assert eur.text.index("EUR") < eur.text.index("BYN")

    assert usd.text == USD_LINE
    assert usd.text.index("USD") < usd.text.index("BYN")
    assert usd.rate_source == "NBRB"

    assert byn.text == BYN_LINE
    assert byn.rate_source is None
    assert "курс" not in byn.text
    assert "EUR" not in byn.text

    assert missing.text is None
    assert missing.display_currency is None
    assert price_display(None, "USD", quote) is None
    assert "7,39" not in json.dumps(annotate_product({"price_from_byn": None}, "EUR", quote))


def test_each_currency_sample_and_a_shop_card_without_that_price() -> None:
    """Sample replies the PR quotes. The b2btea card never carries the vitrine amount."""
    quote = _nbrb()
    samples = {
        "EUR": f"{EUR_LINE} — {TEA}",
        "USD": f"{USD_LINE} — {TEA}",
        "BYN": f"{BYN_LINE} — {TEA}",
    }
    for code, line in samples.items():
        shown = annotate_product(
            {"product_name": TEA, "price_from_byn": 25, "product_url": URL},
            code,
            quote if code != "BYN" else None,
        )
        section = format_vitrine_price_section([shown])
        assert line in section
        assert section.startswith(VITRINE_PRICE_HEADING)

    absent = format_vitrine_price_section(
        [annotate_product({"product_name": TEA, "price_from_byn": None}, "EUR", quote)]
    )
    assert absent == ""

    shop = {
        "status": "success",
        "class_label_ru": "зелёный",
        "shops": [
            {
                "name": "Alpha Tea",
                "city": "Warsaw",
                "same_city": True,
                "url": "https://b2btea.com/en/c/alpha-tea/",
                "website": "https://alpha.example",
                "price_from_byn": 25,
                "price_display": EUR_LINE,
            }
        ],
    }
    cards = format_local_shops_section(shop)
    assert "b2btea.com" in cards
    assert "7,39" not in cards
    assert "25,00" not in cards
    assert "EUR" not in cards
    assert "BYN" not in cards
    assert "NBRB" not in cards


def test_exchange_rate_api_is_named_when_it_is_the_source() -> None:
    text = price_display(25, "EUR", _erapi())
    assert text == ER_LINE
    usd = price_display(25, "USD", _erapi())
    assert usd == "~8,32 USD (25,00 BYN, курс ExchangeRate-API 04.10.2026)"


def test_currency_specific_nbrb_dates_stay_with_that_currency() -> None:
    quote = _nbrb(usd_date=date(2026, 10, 3))
    assert "04.10.2026" in price_display(25, "EUR", quote)
    assert "03.10.2026" in price_display(25, "USD", quote)


def test_failed_rate_shows_byn_only() -> None:
    text = price_display(25, "EUR", None)
    assert text == BYN_LINE
    assert "курс" not in text
    assert "~" not in text


def test_model_price_is_replaced_and_not_stuck_to_a_b2btea_link() -> None:
    product = {
        "product_name": TEA,
        "product_url": URL,
        "matched_slug": "biluochun",
        "price_from_byn": 25,
        "price_display": EUR_LINE,
        "availability": "in_stock",
    }
    text = (
        f"1. {TEA} — ~9,99 EUR (25,00 BYN, курс NBRB 01.01.2000).\n"
        "До 20 EUR тоже можно смотреть.\n"
        f"{LOCAL_SHOPS_HEADING}\n"
        f"Alpha — {EUR_LINE}. "
        "[Карточка](https://b2btea.com/en/c/alpha-tea/) "
        "[Сайт](https://alpha.example)\n"
    )
    shops = {
        "status": "success",
        "class_label_ru": "зелёный",
        "shops": [
            {
                "name": "Alpha Tea",
                "city": "Warsaw",
                "same_city": True,
                "url": "https://b2btea.com/en/c/alpha-tea/",
                "website": "https://alpha.example",
                "price_display": EUR_LINE,
            }
        ],
    }
    updated = ensure_next_steps(text, [product], local_shops=shops, currency="EUR")
    assert "9,99" not in updated
    assert "01.01.2000" not in updated
    assert "до 20 EUR" in updated.lower() or "До 20 EUR" in updated
    assert EUR_LINE in updated
    assert updated.index(EUR_LINE) < updated.index(LOCAL_SHOPS_HEADING)
    for line in updated.splitlines():
        if "b2btea.com" in line or "alpha.example" in line:
            assert "7,39" not in line
            assert "25,00" not in line
            assert "EUR" not in line
            assert "BYN" not in line
    assert strip_model_shop_prices("до 20 евро на подарок") == "до 20 евро на подарок"


def test_find_in_shop_uses_session_currency_and_does_not_call_the_network(
    tmp_path: Path, monkeypatch
) -> None:
    def boom(url: str, timeout: float) -> tuple[int, bytes]:
        raise AssertionError(f"live fx network {url} {timeout}")

    monkeypatch.setattr("tea_agent.fx_rates._http_get", boom)
    catalog = {
        "items": [
            {
                "product_name": TEA,
                "matched_slug": "biluochun",
                "price_from_byn": 25,
                "availability": "in_stock",
                "product_url": URL,
            },
            {
                "product_name": "Чай без цены",
                "matched_slug": "biluochun",
                "availability": "in_stock",
                "product_url": "https://www.teashop.by/product/no-price/",
            },
        ]
    }
    path = tmp_path / "teashop_catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr("tea_agent.shop_catalog._catalog_path", lambda: path)
    reload_catalog()
    try:
        set_fx_provider(lambda _now: _nbrb())
        default = find_in_shop(slug="biluochun")
        assert default["requested_currency"] == "EUR"
        assert default["fx_status"] == "ok"
        assert default["fx"]["source"] == "NBRB"
        assert default["fx"]["eur_date"] == "2026-10-04"
        priced = next(
            row for row in default["products"] if row["product_url"] == URL
        )
        assert priced["price_display"] == EUR_LINE
        assert priced["price_from_byn"] == 25
        blank = next(row for row in default["products"] if row["price_from_byn"] is None)
        assert blank["price_display"] is None
        assert blank["rate_source"] is None

        usd = find_in_shop(
            slug="biluochun",
            tool_context=_Ctx({"user:currency": "USD"}),  # type: ignore[arg-type]
        )
        assert usd["requested_currency"] == "USD"
        assert usd["products"][0]["price_display"] == USD_LINE or any(
            row["price_display"] == USD_LINE for row in usd["products"]
        )

        byn = find_in_shop(
            slug="biluochun",
            tool_context=_Ctx({"currency": "BYN"}),  # type: ignore[arg-type]
        )
        assert byn["requested_currency"] == "BYN"
        assert byn["fx_status"] == "not_needed"
        assert byn["fx"] is None
        assert any(row["price_display"] == BYN_LINE for row in byn["products"])
        assert all(
            row["price_display"] is None or "курс" not in row["price_display"]
            for row in byn["products"]
        )

        set_fx_provider(lambda _now: None)
        failed = find_in_shop(
            slug="biluochun",
            tool_context=_Ctx({"user:currency": "EUR"}),  # type: ignore[arg-type]
        )
        assert failed["fx_status"] == "unavailable"
        assert any(row["price_display"] == BYN_LINE for row in failed["products"])
    finally:
        monkeypatch.undo()
        reload_catalog()
