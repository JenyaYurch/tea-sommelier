"""Unit tests for teashop catalog helpers."""

from __future__ import annotations

import json
from pathlib import Path

from tea_agent.shop_catalog import find_products, parse_price_byn, reload_catalog
from tea_agent.tools import find_in_shop


def test_parse_price_byn() -> None:
    assert parse_price_byn("29,50 р.") == 29.5
    assert parse_price_byn("6.45р") == 6.45
    assert parse_price_byn("10 - 20 р.") == 10.0
    assert parse_price_byn(None) is None
    assert parse_price_byn("") is None


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
