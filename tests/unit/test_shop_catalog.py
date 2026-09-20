"""Unit tests for teashop catalog helpers."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tea_agent.shop_catalog import (
    catalog_freshness,
    catalog_meta,
    find_products,
    load_catalog,
    match_product_to_slug,
    parse_price_byn,
    reload_catalog,
    remap_unmatched_items,
)
from tea_agent.tools import find_in_shop


def test_parse_price_byn() -> None:
    assert parse_price_byn("29,50 р.") == 29.5
    assert parse_price_byn("6.45р") == 6.45
    assert parse_price_byn("10 - 20 р.") == 10.0
    assert parse_price_byn(None) is None
    assert parse_price_byn("") is None


def test_match_product_to_slug_chinese_greens() -> None:
    cases = [
        ("Бай Мао Хоу (Белая обезьяна)", None, "bai-mao-hou"),
        ("Чжэнь Мэй (Брови Дракона), весна 2026 г.", None, "meicha"),
        ("Цюэ Шэ «Воробьиные язычки», 1-й сбор, весна 2026 года.", None, "queshe-lucha"),
        ("Цзыян Фуси Мао Цзянь, 2026 г.", None, "ziyang-mao-jian"),
        ("Цзыян Цуйфэн, 1-й сбор, весна 2024 г.", None, "ziyang-lucha"),
        ("Инь Сы «Серебряные Нити», весна 2026 г.", None, "yin-si-lu-cha"),
        ("Жасминовая Жемчужина Дракона", None, "moli-longzhu"),
        ("Жасминовый Глаз Феникса (Моли Фэн Янь)", None, "moli-feng-yan"),
        ("Серебряный жасминовый пух", None, "moli-yin-hao"),
        ("Ганпаудер (Круглый чай) 3505", None, "pingshui-zhucha"),
        (
            "Мао Фэн Тоу Чунь, весна 2026 г",
            "https://www.teashop.by/product/yunnan-maofen-tou-chun/",
            "yun-nan-mao-feng",
        ),
        (
            "Бай Хао Мао Цзянь, весна 2023 г.",
            "https://www.teashop.by/product/junnan-jun-u/",
            "yun-wu-lu-cha",
        ),
    ]
    for name, url, expected in cases:
        slug, confidence = match_product_to_slug(name, url)
        assert slug == expected, (name, slug, confidence)
        assert confidence in {"high", "medium"}


def test_match_product_maps_new_types() -> None:
    cases = [
        ("Бай Му Дань", "bai-mudan"),
        ("Красный чай Чан Син «Лесной Дянь Хун», 2026 год", "dianhong-gongfu"),
        ("Цзюнь Шань Инь Чжэнь", "junshan-yin-zhen"),
        ("Конкурсный Лу Гу Дун Дин, 2026 г.", "dong-ding-wulong"),
        ("Ю Лань Чи Гань", "chi-gan-xiao-zhong"),
    ]
    for name, expected in cases:
        slug, confidence = match_product_to_slug(name)
        assert slug == expected, (name, slug, confidence)
        assert confidence in {"high", "medium"}


def test_match_product_skips_non_encyclopedia() -> None:
    for name in [
        "Чайный сет «Светлые»",
        "Чай зелёный «Асамуши сенча» (AS), High-grade",
        "Матча церемониальная Удзи",
        "Грузинский байховый зеленый чай",
        "Чай TeaCraft «Зелёный маофен» 250гр",
    ]:
        slug, confidence = match_product_to_slug(name)
        assert slug is None, name
        assert confidence == "none"


def test_remap_unmatched_items_keeps_existing() -> None:
    items = [
        {
            "product_name": "Си Ху Лун Цзин, 2026",
            "matched_slug": "xihu-longjing",
            "mapping_confidence": "high",
            "product_url": "https://www.teashop.by/product/longjing-1/",
        },
        {
            "product_name": "Бай Мао Хоу (Белая обезьяна)",
            "matched_slug": None,
            "mapping_confidence": "none",
            "product_url": "https://www.teashop.by/product/baj-mao-xou-snezhnaya-obezyana/",
        },
    ]
    filled = remap_unmatched_items(items)
    assert filled == 1
    assert items[0]["matched_slug"] == "xihu-longjing"
    assert items[1]["matched_slug"] == "bai-mao-hou"


def test_find_products_by_slug(tmp_path: Path, monkeypatch) -> None:
    catalog = {
        "items": [
            {
                "product_name": "Дунтин Би Ло Чунь, Цзянсу",
                "matched_slug": "biluochun",
                "price_from_byn": 29.5,
                "availability": "in_stock",
                "product_url": "https://www.teashop.by/product/biluochun/",
                "image_url": "https://example.com/b.jpg",
                "mapping_confidence": "high",
                "last_checked": "2026-09-08",
            },
            {
                "product_name": "Си Ху Лун Цзин, 2026",
                "matched_slug": "xihu-longjing",
                "price_from_byn": 19.9,
                "availability": "in_stock",
                "product_url": "https://www.teashop.by/product/longjing/",
                "mapping_confidence": "high",
                "last_checked": "2026-09-08",
            },
        ]
    }
    path = tmp_path / "teashop_catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(
        "tea_agent.shop_catalog._catalog_path",
        lambda: path,
    )
    reload_catalog()

    hits = find_products(slug="biluochun")
    assert len(hits) == 1
    assert hits[0]["product_url"].endswith("/biluochun/")
    assert hits[0]["price_from_byn"] == 29.5

    tool = find_in_shop(slug="biluochun")
    assert tool["status"] == "success"
    assert tool["products"][0]["product_url"]

    missing = find_in_shop(slug="no-such-tea")
    assert missing["status"] == "not_found"


def test_find_products_by_query(tmp_path: Path, monkeypatch) -> None:
    catalog = {
        "items": [
            {
                "product_name": "Бай Мао Хоу (Белая обезьяна)",
                "matched_slug": "bai-mao-hou",
                "price_from_byn": 12.0,
                "availability": "in_stock",
                "product_url": "https://www.teashop.by/product/baj-mao-xou/",
                "mapping_confidence": "high",
            },
            {
                "product_name": "Чай зелёный «Асамуши сенча»",
                "matched_slug": None,
                "price_from_byn": 15.0,
                "availability": "in_stock",
                "product_url": "https://www.teashop.by/product/sencha/",
                "mapping_confidence": "none",
            },
        ]
    }
    path = tmp_path / "teashop_catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr("tea_agent.shop_catalog._catalog_path", lambda: path)
    reload_catalog()

    by_query = find_products(query="Бай Мао Хоу")
    assert len(by_query) == 1
    assert by_query[0]["matched_slug"] == "bai-mao-hou"

    sencha = find_in_shop(query="сенча")
    assert sencha["status"] == "success"
    assert sencha["products"][0]["product_url"].endswith("/sencha/")

    empty = find_in_shop()
    assert empty["status"] == "error"


def test_real_catalog_find_in_shop_mapped_slugs() -> None:
    reload_catalog()
    for slug in [
        "bai-mao-hou",
        "meicha",
        "queshe-lucha",
        "ziyang-mao-jian",
        "moli-longzhu",
        "moli-feng-yan",
        "yun-nan-mao-feng",
        "pingshui-zhucha",
    ]:
        result = find_in_shop(slug=slug)
        assert result["status"] == "success", slug
        assert result["products"][0]["product_url"]
        assert result["products"][0]["matched_slug"] == slug


def test_real_catalog_finds_new_types_by_query() -> None:
    reload_catalog()
    for query in ("Дянь Хун", "GABA", "габа", "пуэр", "Ю Лань Чи Гань", "Е Шен Сычуань"):
        result = find_in_shop(query=query)
        assert result["status"] == "success", query
        assert result["products"][0]["product_url"]


def test_catalog_covers_v1_categories() -> None:
    reload_catalog()
    urls = " ".join(str(item.get("category_url") or "") for item in load_catalog())
    for fragment in ("beliy", "zheltiy", "zeleniy", "cherniy", "puer", "gaba"):
        assert fragment in urls, fragment


def test_catalog_meta_reads_last_checked(tmp_path: Path, monkeypatch) -> None:
    catalog = {
        "source": "teashop.by",
        "generated_on": "2026-09-08",
        "last_checked": "2026-09-08",
        "refresh_interval_days": 14,
        "count": 1,
        "items": [
            {
                "product_name": "Си Ху Лун Цзин",
                "matched_slug": "xihu-longjing",
                "last_checked": "2026-09-08",
            }
        ],
    }
    path = tmp_path / "teashop_catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr("tea_agent.shop_catalog._catalog_path", lambda: path)
    reload_catalog()

    meta = catalog_meta()
    assert meta["last_checked"] == "2026-09-08"
    assert meta["generated_on"] == "2026-09-08"
    assert meta["count"] == 1

    fresh = catalog_freshness(today=date(2026, 9, 15))
    assert fresh["age_days"] == 7
    assert fresh["needs_refresh"] is False

    stale = catalog_freshness(today=date(2026, 9, 22))
    assert stale["age_days"] == 14
    assert stale["needs_refresh"] is True


def test_catalog_freshness_falls_back_to_item_dates(
    tmp_path: Path, monkeypatch
) -> None:
    catalog = {
        "items": [
            {"product_name": "A", "last_checked": "2026-09-01"},
            {"product_name": "B", "last_checked": "2026-09-10"},
        ]
    }
    path = tmp_path / "teashop_catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr("tea_agent.shop_catalog._catalog_path", lambda: path)
    reload_catalog()

    fresh = catalog_freshness(today=date(2026, 9, 15))
    assert fresh["last_checked"] == "2026-09-10"
    assert fresh["age_days"] == 5
    assert fresh["needs_refresh"] is False


def test_real_catalog_has_last_checked() -> None:
    reload_catalog()
    meta = catalog_meta()
    assert meta["exists"] is True
    assert meta["count"] > 0
    assert meta["last_checked"]
    assert isinstance(meta["last_checked"], str)
