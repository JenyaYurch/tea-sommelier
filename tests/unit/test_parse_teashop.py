"""Availability parsing for teashop.by HTML and the store API. No network."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from bs4 import BeautifulSoup

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _ROOT / "tests" / "fixtures" / "teashop"
_SPEC = importlib.util.spec_from_file_location(
    "parse_teashop", _ROOT / "scripts" / "parse_teashop.py"
)
assert _SPEC is not None and _SPEC.loader is not None
parse_teashop = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(parse_teashop)


def _card(name: str):
    html = (_FIXTURES / name).read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one(".product-item") or soup.select_one(".single-product-wrapper")


def test_listing_out_of_stock_is_on_parent_li_not_inner_card() -> None:
    card = _card("listing_out_of_stock.html")
    assert card is not None
    assert "outofstock" not in (card.get("class") or [])
    assert parse_teashop._availability(card) == "out_of_stock"


def test_listing_in_stock_class_on_parent_li() -> None:
    card = _card("listing_in_stock.html")
    assert parse_teashop._availability(card) == "in_stock"


def test_listing_without_stock_marker_is_unknown() -> None:
    card = _card("listing_unknown.html")
    assert parse_teashop._availability(card) == "unknown"


def test_product_page_out_of_stock_text_and_schema() -> None:
    wrapper = _card("product_out_of_stock.html")
    assert parse_teashop._availability(wrapper) == "out_of_stock"


def test_product_page_in_stock_schema() -> None:
    wrapper = _card("product_in_stock.html")
    assert parse_teashop._availability(wrapper) == "in_stock"


def test_variation_json_all_out_of_stock() -> None:
    card = _card("variations_out_of_stock.html")
    assert parse_teashop._availability(card) == "out_of_stock"


def test_store_api_stock_price_and_name() -> None:
    sold_out = {
        "id": 22194,
        "name": "Хо Шань Хуан Я, 2022 г.",
        "permalink": "https://www.teashop.by/product/xo-shan-xuan-ya/",
        "is_in_stock": False,
        "stock_availability": {"text": "Нет в наличии", "class": "out-of-stock"},
        "prices": {
            "price": "592",
            "price_range": {"min_amount": "592", "max_amount": "14800"},
            "currency_minor_unit": 2,
        },
        "variations": [{"attributes": [{"name": "Количество", "value": "10g"}]}],
        "images": [{"thumbnail": "https://www.teashop.by/wp-content/uploads/xo.jpg"}],
    }
    item = parse_teashop.item_from_store_product(
        sold_out,
        category_url="https://www.teashop.by/shop/chaj/zheltiy/",
        source_page=1,
        today="2026-10-03",
    )
    assert item["availability"] == "out_of_stock"
    assert item["price_from_byn"] == 5.92
    assert item["weight_g"] == 10
    assert item["product_name"] == "Хо Шань Хуан Я, 2022 г."
    assert item["matched_slug"] == "huoshan-huang-ya"

    quoted = {
        "id": 49148,
        "name": "Мэндин Хуан Я &#171;Желтые Почки&#187;, первый сбор, весна 2026 года",
        "permalink": "https://www.teashop.by/product/czingu-huan-cha-gu-shu/",
        "is_in_stock": False,
        "stock_availability": {"text": "Нет в наличии", "class": "out-of-stock"},
        "prices": {"price": "1840", "currency_minor_unit": 2},
    }
    named = parse_teashop.item_from_store_product(
        quoted,
        category_url="https://www.teashop.by/shop/chaj/zheltiy/",
        source_page=1,
        today="2026-10-03",
    )
    assert "«Желтые Почки»" in named["product_name"]
    assert named["matched_slug"] == "mengding-huang-ya"
    assert named["availability"] == "out_of_stock"


def test_store_api_missing_stock_is_unknown() -> None:
    assert (
        parse_teashop.availability_from_store_product({"name": "Чай"}) == "unknown"
    )


def test_all_in_stock_guard() -> None:
    assert parse_teashop.all_items_in_stock(
        [{"availability": "in_stock"}, {"availability": "in_stock"}]
    )
    assert not parse_teashop.all_items_in_stock(
        [{"availability": "in_stock"}, {"availability": "out_of_stock"}]
    )
    assert not parse_teashop.all_items_in_stock([])
    summary = parse_teashop.availability_summary(
        [
            {"availability": "in_stock"},
            {"availability": "out_of_stock"},
            {"availability": "unknown"},
        ]
    )
    assert summary == {"in_stock": 1, "out_of_stock": 1, "unknown": 1}
