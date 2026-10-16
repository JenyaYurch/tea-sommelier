"""Index file, shop config, and daily freshness. No network."""

from __future__ import annotations

import json
from datetime import date

from tea_agent.eu_price_index import (
    index_freshness,
    index_meta,
    load_offers,
    reload_index,
)
from tea_agent.eu_shops.config import load_shops

_PILOT_SHOPS = {
    "thetea",
    "teaeye",
    "czajnikowy",
    "tea-mail",
    "teasome",
    "teewald",
    "tezen",
    "nannuoshan",
    "laifufu",
    "tushita",
    "eherbata",
    "czajownia",
    "yoshien",
    "nanyi",
}


def test_shop_config_lists_the_pilot_shops() -> None:
    shops = load_shops()
    assert {shop.id for shop in shops} == _PILOT_SHOPS
    assert {shop.platform for shop in shops} <= {"shopify", "woocommerce", "html"}
    woo = {shop.id for shop in shops if shop.platform == "woocommerce"}
    shopify = {shop.id for shop in shops if shop.platform == "shopify"}
    html = {shop.id for shop in shops if shop.platform == "html"}
    assert woo == {"thetea", "teaeye", "czajnikowy", "teasome", "tezen"}
    assert shopify == {"tea-mail", "teewald", "nannuoshan", "laifufu", "tushita"}
    assert html == {"eherbata", "czajownia", "yoshien", "nanyi"}
    assert all(shop.base_url.startswith("https://") for shop in shops)
    assert all(shop.parser and shop.listing_urls for shop in shops if shop.platform == "html")


def test_index_reader_and_daily_freshness(tmp_path, monkeypatch) -> None:
    path = tmp_path / "eu_green_price_index.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_on": "2026-10-14",
                "last_checked": "2026-10-14",
                "fetched_at": "2026-10-14T08:00:00Z",
                "refresh_interval_days": 1,
                "shops": [{"id": "thetea", "status": "ok"}],
                "count": 1,
                "offers": [
                    {
                        "tea_id": "xihu-longjing",
                        "title": "Long Jing",
                        "shop_id": "thetea",
                        "shop": "TheTea",
                        "url": "https://thetea.pl/produkt/long-jing/?attribute_pa_waga=50-g",
                        "price": 42.0,
                        "currency": "PLN",
                        "pack_size_g": 50,
                        "pack_size_unclear": False,
                        "price_per_100g": 84.0,
                        "in_stock": True,
                        "fetched_at": "2026-10-14T08:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tea_agent.eu_price_index.index_path", lambda: path)
    reload_index()
    offers = load_offers()
    assert len(offers) == 1
    assert offers[0]["price_per_100g"] == 84.0
    assert offers[0]["pack_size_unclear"] is False

    fresh = index_freshness(today=date(2026, 10, 14))
    assert fresh["needs_refresh"] is False
    assert fresh["age_days"] == 0

    due = index_freshness(today=date(2026, 10, 15))
    assert due["needs_refresh"] is True
    assert due["age_days"] == 1
    assert due["refresh_interval_days"] == 1

    meta = index_meta()
    assert meta["count"] == 1
    assert meta["exists"] is True
    monkeypatch.undo()
    reload_index()
