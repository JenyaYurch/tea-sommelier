"""Shopify and WooCommerce adapters against recorded fixtures. No network."""

from __future__ import annotations

import json
from pathlib import Path

from tea_agent.eu_shops.models import FetchResponse, Shop
from tea_agent.eu_shops.refresh import build_document, refresh_shops, should_write
from tea_agent.eu_shops.shopify import fetch_shop as fetch_shopify
from tea_agent.eu_shops.shopify import products_url as shopify_products_url
from tea_agent.eu_shops.woocommerce import fetch_shop as fetch_woo
from tea_agent.eu_shops.woocommerce import product_url as woo_product_url
from tea_agent.eu_shops.woocommerce import products_url as woo_products_url

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "eu_shops"
_FETCHED = "2026-10-15T12:00:00Z"


def _load(name: str):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _shopify_shop() -> Shop:
    return Shop(
        id="example",
        name="Example",
        platform="shopify",
        base_url="https://shop.example",
        country="DE",
        currency="EUR",
    )


def _woo_shop() -> Shop:
    return Shop(
        id="teaeye",
        name="TEA EYE",
        platform="woocommerce",
        base_url="https://teaeye.pl",
        country="PL",
        currency="PLN",
    )


def _response(data, *, status: int = 200, headers: dict | None = None, error: str | None = None):
    return FetchResponse(status=status, headers=headers or {}, data=data, error=error)


def test_shopify_fixture_maps_variants_and_skips_the_rest() -> None:
    payload = _load("shopify_products.json")
    calls: list[str] = []

    def fetch(url: str) -> FetchResponse:
        calls.append(url)
        if url.endswith("/meta.json"):
            return _response({"currency": "EUR", "name": "Example"})
        if "page=1" in url:
            return _response({"products": payload["products"][:5]})
        if "page=2" in url:
            return _response({"products": payload["products"][5:]})
        return _response({"products": []})

    result = fetch_shopify(
        _shopify_shop(),
        fetch,
        fetched_at=_FETCHED,
        page_limit=5,
        pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.status == "ok"
    assert result.pages == 2
    by_url = {offer["url"]: offer for offer in result.offers}

    fifty = by_url["https://shop.example/products/long-jing?variant=501"]
    assert fifty["tea_id"] == "xihu-longjing"
    assert fifty["title"] == "Long Jing"
    assert fifty["price"] == 25.0
    assert fifty["currency"] == "EUR"
    assert fifty["pack_size_g"] == 50
    assert fifty["pack_size_unclear"] is False
    assert fifty["price_per_100g"] == 50.0
    assert fifty["in_stock"] is True
    assert fifty["fetched_at"] == _FETCHED

    hundred = by_url["https://shop.example/products/long-jing?variant=502"]
    assert hundred["pack_size_g"] == 100
    assert hundred["price_per_100g"] == 40.0
    assert hundred["in_stock"] is False

    hou_kui = by_url["https://shop.example/products/tai-ping-hou-kui?variant=531"]
    assert hou_kui["tea_id"] == "taiping-hou-kui"
    assert hou_kui["pack_size_g"] == 20
    assert hou_kui["price_per_100g"] == 90.0

    anji = by_url["https://shop.example/products/anji-bai-cha?variant=571"]
    assert anji["tea_id"] == "anji-baicha"
    assert anji["pack_size_g"] == 25
    assert anji["price_per_100g"] == 56.0

    unclear = by_url["https://shop.example/products/bi-luo-chun?variant=521"]
    assert unclear["tea_id"] == "biluochun"
    assert unclear["pack_size_g"] is None
    assert unclear["pack_size_unclear"] is True
    assert unclear["price_per_100g"] is None

    titles = {offer["title"] for offer in result.offers}
    assert "Nepal Mao Feng" not in titles
    assert "Long Jing Teapot" not in titles
    assert "Milk Oolong" not in titles
    assert "Huangshan Mao Feng Hong Cha" not in titles
    assert calls[0].endswith("/meta.json")
    assert shopify_products_url("https://shop.example", 1, 5) in calls


def test_shopify_uses_config_currency_when_meta_fails() -> None:
    payload = _load("shopify_products.json")

    def fetch(url: str) -> FetchResponse:
        if url.endswith("/meta.json"):
            return _response(None, status=403, error="HTTP 403")
        return _response({"products": [payload["products"][3]]})

    result = fetch_shopify(
        _shopify_shop(),
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.status == "ok"
    assert result.offers[0]["currency"] == "EUR"


def test_shopify_repeated_page_does_not_publish_offers() -> None:
    payload = _load("shopify_products.json")
    page = {"products": payload["products"][:1]}

    def fetch(url: str) -> FetchResponse:
        if url.endswith("/meta.json"):
            return _response({"currency": "EUR"})
        return _response(page)

    result = fetch_shopify(
        _shopify_shop(),
        fetch,
        fetched_at=_FETCHED,
        page_limit=1,
        max_pages=3,
        pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.status == "error"
    assert result.error == "pagination returned a repeated page"
    assert result.offers == []


def test_woocommerce_fixture_prices_variations_and_skips_fetches() -> None:
    products = _load("woo_products.json")
    variations = {int(key): value for key, value in _load("woo_variations.json").items()}
    calls: list[str] = []

    def fetch(url: str) -> FetchResponse:
        calls.append(url)
        if "per_page=" in url and "page=1" in url:
            return _response(products, headers={"x-wp-totalpages": "1"})
        if "per_page=" in url:
            return _response([])
        for vid, detail in variations.items():
            if url.endswith(f"/{vid}"):
                return _response(detail)
        return _response(None, status=404, error="HTTP 404")

    result = fetch_woo(
        _woo_shop(),
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        variation_pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.status == "ok"
    by_url = {offer["url"]: offer for offer in result.offers}

    ten = by_url["https://teaeye.pl/product/long-jing-ming-qian/?attribute_pa_waga=10g"]
    assert ten["tea_id"] == "xihu-longjing"
    assert ten["shop_id"] == "teaeye"
    assert ten["shop"] == "TEA EYE"
    assert ten["price"] == 10.0
    assert ten["currency"] == "PLN"
    assert ten["pack_size_g"] == 10
    assert ten["price_per_100g"] == 100.0
    assert ten["in_stock"] is True

    fifty = by_url["https://teaeye.pl/product/long-jing-ming-qian/?attribute_pa_waga=50g"]
    assert fifty["pack_size_g"] == 50
    assert fifty["price"] == 45.0
    assert fifty["price_per_100g"] == 90.0
    assert fifty["in_stock"] is False

    yunwu = by_url["https://example.test/lu-shan-yun-wu/"]
    assert yunwu["tea_id"] == "lushan-yun-wu"
    assert yunwu["price"] == 32.0
    assert yunwu["currency"] == "EUR"
    assert yunwu["pack_size_g"] == 50
    assert yunwu["price_per_100g"] == 64.0

    unclear = by_url["https://example.test/organic-mao-feng/"]
    assert unclear["tea_id"] == "huangshan-mao-feng"
    assert unclear["pack_size_unclear"] is True
    assert unclear["pack_size_g"] is None
    assert unclear["price_per_100g"] is None

    small = by_url[
        "https://www.tezen.eu/produkt/bi-luo-chun/?attribute_pa_gewicht=20-g-washipapier-aromaschutztuete"
    ]
    assert small["tea_id"] == "biluochun"
    assert small["pack_size_g"] == 20
    assert small["price"] == 18.99
    assert small["price_per_100g"] == 94.95

    tin = by_url[
        "https://www.tezen.eu/produkt/bi-luo-chun/?attribute_pa_gewicht=80-g-geschenkdose-mit-holzdeckel"
    ]
    assert tin["pack_size_g"] == 80
    assert tin["price"] == 39.99
    assert tin["price_per_100g"] == 49.99

    titles = {offer["title"] for offer in result.offers}
    assert "Nepal Mao Feng" not in titles
    assert "Matcha Ceremonialna \u2013 Kagoshima 30 g (Organic)" not in titles
    assert "Qi Shan Mao Feng" not in titles
    assert "2025 Huangshan Organic Mao Feng Hong Cha" not in titles
    assert woo_product_url("https://teaeye.pl", 99999) not in calls
    assert woo_product_url("https://teaeye.pl", 88888) not in calls
    assert woo_product_url("https://teaeye.pl", 20998) in calls
    assert woo_products_url("https://teaeye.pl", 1, 100) in calls


def test_woocommerce_403_is_recorded_and_refresh_keeps_other_shops() -> None:
    payload = _load("shopify_products.json")
    blocked = Shop(
        id="czajnikowy",
        name="Czajnikowy",
        platform="woocommerce",
        base_url="https://www.czajnikowy.com.pl",
        country="PL",
        currency="PLN",
    )

    def fetch(url: str) -> FetchResponse:
        if "czajnikowy" in url:
            return _response(None, status=403, error="HTTP 403 (blocked)")
        if url.endswith("/meta.json"):
            return _response({"currency": "EUR"})
        if "page=1" in url:
            return _response({"products": [payload["products"][3]]})
        return _response({"products": []})

    results = refresh_shops(
        [blocked, _shopify_shop()],
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        concurrency=1,
        sleep=lambda _seconds: None,
    )
    assert results[0].status == "error"
    assert results[0].error == "HTTP 403 (blocked)"
    assert results[0].offers == []
    assert results[1].status == "ok"
    assert results[1].offers
    assert should_write(results) is None
    document = build_document(results, fetched_at=_FETCHED)
    assert document["shops"][0]["status"] == "error"
    assert document["count"] == len(results[1].offers)
    assert {offer["tea_id"] for offer in document["offers"]} == {"taiping-hou-kui"}


def test_retry_delay_backs_off_and_honors_retry_after() -> None:
    from tea_agent.eu_shops.live_fetch import _retry_delay

    class _Response:
        def __init__(self, headers: dict[str, str]) -> None:
            self.headers = headers

    assert _retry_delay(_Response({}), 0) == 2.0
    assert _retry_delay(_Response({}), 1) == 4.0
    assert _retry_delay(_Response({}), 2) == 8.0
    assert _retry_delay(_Response({"Retry-After": "12"}), 0) == 12.0
    assert _retry_delay(_Response({"Retry-After": "1"}), 2) == 8.0
    assert _retry_delay(_Response({"Retry-After": "60"}), 0) == 60.0
    assert _retry_delay(_Response({"Retry-After": "120"}), 0) == 90.0


def test_refresh_refuses_to_replace_the_index_when_every_shop_fails() -> None:
    shop = _woo_shop()

    def fetch(_url: str) -> FetchResponse:
        return _response(None, status=500, error="HTTP 500")

    results = refresh_shops(
        [shop],
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        concurrency=1,
        sleep=lambda _seconds: None,
    )
    assert results[0].status == "error"
    assert should_write(results) == "no shop could be read; index not written"
