"""HTML shop parsers and robots rules. No network."""

from __future__ import annotations

from pathlib import Path

from tea_agent.eu_shops.html_catalog import fetch_shop
from tea_agent.eu_shops.html_pages import (
    nanyi_products_to_items,
    parse_czajownia_product,
    parse_nanyi_listing,
)
from tea_agent.eu_shops.live_fetch import _from_response
from tea_agent.eu_shops.models import FetchResponse, Shop
from tea_agent.eu_shops.robots import crawl_delay, robots_allows

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "eu_shops"
_FETCHED = "2026-10-15T12:00:00Z"
_ROBOTS = """
User-agent: *
Crawl-delay: 1
Disallow: /application
Disallow: /*/default/
Allow: /environment/cache/images

User-agent: pricebot
Crawl-delay: 10
Disallow: /
"""


def _text(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _response(data, *, status: int = 200, error: str | None = None) -> FetchResponse:
    return FetchResponse(status=status, headers={}, data=data, error=error)


def _html_shop(parser: str, listing: str, *, shop_id: str, currency: str) -> Shop:
    return Shop(
        id=shop_id,
        name=shop_id,
        platform="html",
        base_url="https://shop.example",
        country="DE",
        currency=currency,
        parser=parser,
        listing_urls=(listing,),
    )


def test_robots_allows_product_pages_and_blocks_sort_urls() -> None:
    assert robots_allows(_ROBOTS, "/pl/p/Herbata-zielona-Long-Jing/927")
    assert robots_allows(_ROBOTS, "/herbata-chinska/2")
    assert not robots_allows(_ROBOTS, "/herbata-chinska/1/default/2")
    assert not robots_allows(_ROBOTS, "/application")
    assert robots_allows(_ROBOTS, "/environment/cache/images/tea.jpg")
    assert crawl_delay(_ROBOTS, "TeaBotPriceIndex/1.0") == 1.0
    assert crawl_delay(_ROBOTS, "pricebot") == 10.0


def test_robots_tie_allows_the_path() -> None:
    text = "User-agent: *\nDisallow: /tea\nAllow: /tea\n"
    assert robots_allows(text, "/tea/long-jing")


def test_czajownia_variants_and_skipped_pages() -> None:
    listing = "https://www.czajownia.pl/herbata-chinska"
    product = "https://www.czajownia.pl/pl/p/Herbata-zielona-Long-Jing-Tiger-Spring-Upper-Grade-2026/927"
    calls: list[str] = []

    def fetch(url: str) -> FetchResponse:
        calls.append(url)
        if url.endswith("/robots.txt"):
            return _response(_ROBOTS)
        if url.rstrip("/") == listing:
            return _response(_text("czajownia_min.html"))
        if url.startswith(product):
            return _response(_text("czajownia_product.html"))
        return _response("missing", status=404, error="HTTP 404")

    result = fetch_shop(
        _html_shop("czajownia", listing, shop_id="czajownia", currency="PLN"),
        fetch,
        fetched_at=_FETCHED,
        max_pages=1,
        pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.status == "ok"
    assert result.pages == 1
    assert all("/default/" not in url for url in calls)
    assert not any(url.rstrip("/").endswith("/herbata-chinska/2") for url in calls)
    assert sum("/pl/p/" in url for url in calls) == 1
    by_pack = {offer["pack_size_g"]: offer for offer in result.offers}
    assert set(by_pack) == {10, 25, 50, 100}
    ten = by_pack[10]
    assert ten["tea_id"] == "xihu-longjing"
    assert ten["category"] == "green"
    assert ten["price"] == 30.6
    assert ten["currency"] == "PLN"
    assert ten["price_per_100g"] == 306.0
    assert ten["in_stock"] is False
    assert ten["url"].endswith("?option_10=44")
    assert by_pack[25]["price"] == 51.0
    assert by_pack[50]["price"] == 102.0
    assert by_pack[100]["price"] == 204.0
    assert by_pack[100]["price_per_100g"] == 204.0
    assert "pagination cap reached" in " ".join(result.warnings)


def test_czajownia_instock_uses_schema_and_quantity_label() -> None:
    page = "https://www.czajownia.pl/Bai-Mu-Dan-Upper-Grade"
    parsed = parse_czajownia_product(_text("czajownia_instock.html"), page)
    assert parsed.recognized
    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item.in_stock is True
    assert item.price == 16.5
    assert item.pack_text == "25 g"
    quantity_only = """
    <h1>Bai Mu Dan</h1>
    <div class="js__product-price-gross"><span class="js__price-value">9,90 zł</span></div>
    <div class="product-availability"><strong>średnia ilość</strong></div>
    """
    parsed = parse_czajownia_product(quantity_only, page)
    assert parsed.items[0].in_stock is True


def test_yoshien_uses_unit_value_and_drops_taiwan_and_sets() -> None:
    listing = "https://www.yoshien.com/gruener-tee/gruener-tee-china/"
    calls: list[str] = []
    slept: list[float] = []

    def fetch(url: str) -> FetchResponse:
        calls.append(url)
        if url.endswith("/robots.txt"):
            return _response("User-agent: *\nCrawl-delay: 5\n")
        return _response(_text("yoshien_list.html"))

    result = fetch_shop(
        _html_shop("yoshien", listing, shop_id="yoshien", currency="EUR"),
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        sleep=slept.append,
    )
    assert result.status == "ok"
    assert slept == [5.0]
    assert len(calls) == 2
    by_title = {offer["title"]: offer for offer in result.offers}
    anji = by_title["Anji Bai Cha Premium Grade Pest.Free"]
    assert anji["tea_id"] == "anji-baicha"
    assert anji["category"] == "green"
    assert anji["pack_size_g"] == 60
    assert anji["price"] == 13.9
    assert anji["price_per_100g"] == 23.17
    assert anji["in_stock"] is True
    longjing = by_title["Long Jing Premium Bio"]
    assert longjing["pack_size_g"] == 100
    assert longjing["price_per_100g"] == 13.9
    assert longjing["in_stock"] is False
    pearls = by_title["Jasmin Dragon Pearls Bio"]
    assert pearls["tea_id"] == "moli-longzhu"
    assert pearls["category"] == "scented"
    assert pearls["pack_size_g"] == 70
    assert pearls["price_per_100g"] == 21.29
    assert "Bi Luo Chun" not in " ".join(by_title)
    assert all("Set" not in title and "Tasting" not in title for title in by_title)


def test_nanyi_json_pages_skip_sets_and_qi_shan() -> None:
    listing = "https://nan-yi-tee.de/c/teesortiment/gruener-tee/gruener-tee-aus-china"
    calls: list[str] = []

    def fetch(url: str) -> FetchResponse:
        calls.append(url)
        if url.endswith("/robots.txt"):
            return _response("User-agent: *\n")
        if "page=2" in url:
            return _response(_text("nanyi_page2.html"))
        return _response(_text("nanyi_list.html"))

    result = fetch_shop(
        _html_shop("nanyi", listing, shop_id="nanyi", currency="EUR"),
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.status == "ok"
    assert any("page=2" in url for url in calls)
    by_url = {offer["url"]: offer for offer in result.offers}
    assert len(by_url) == 6
    longjing = next(offer for offer in result.offers if "drachenbrunnen-nr-43" in offer["url"])
    assert longjing["tea_id"] == "xihu-longjing"
    assert longjing["category"] == "green"
    assert longjing["pack_size_g"] == 100
    assert longjing["price"] == 18.0
    assert longjing["price_per_100g"] == 18.0
    assert longjing["in_stock"] is True
    anji_pack = next(offer for offer in result.offers if "50g-packung" in offer["url"])
    assert anji_pack["tea_id"] == "anji-baicha"
    assert anji_pack["pack_size_g"] == 50
    assert anji_pack["price"] == 25.0
    assert anji_pack["price_per_100g"] == 50.0
    dose = next(offer for offer in result.offers if "50g-dose" in offer["url"])
    assert dose["pack_size_g"] == 50
    assert dose["price"] == 35.0
    assert dose["price_per_100g"] == 70.0
    portion = next(offer for offer in result.offers if "3g-x-1-portion" in offer["url"])
    assert portion["pack_size_g"] == 3
    assert portion["price"] == 20.0
    assert portion["price_per_100g"] == 666.67
    yunnan = next(offer for offer in result.offers if "yunnan-maofeng" in offer["url"])
    assert yunnan["tea_id"] == "yun-nan-mao-feng"
    assert yunnan["pack_size_g"] == 50
    assert yunnan["price_per_100g"] == 40.0
    titles = " ".join(offer["title"] for offer in result.offers)
    assert "Qi Shan" not in titles
    assert "Portionen" not in titles
    parsed = parse_nanyi_listing(_text("nanyi_list.html"), listing)
    assert parsed.next_urls == (listing + "?page=2",)


def test_nanyi_skips_a_variation_master() -> None:
    items, warnings = nanyi_products_to_items(
        [
            {
                "name": "Da Hong Pao",
                "href": "/p/da-hong-pao",
                "hasVariations": True,
                "price": {"amount": 12, "currency": "EUR", "formatted": "12,00 €"},
            }
        ],
        "https://nan-yi-tee.de/c/oolong",
    )
    assert items == ()
    assert warnings == ("skipped a variation master",)


def test_eherbata_block_is_recorded_without_a_listing_fetch() -> None:
    calls: list[str] = []

    def fetch(url: str) -> FetchResponse:
        calls.append(url)
        return _response(None, status=403, error="HTTP 403 (blocked)")

    result = fetch_shop(
        _html_shop("eherbata", "https://eherbata.pl/", shop_id="eherbata", currency="PLN"),
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.status == "error"
    assert result.error == "HTTP 403 (blocked)"
    assert result.offers == []
    assert calls == ["https://shop.example/robots.txt"]


def test_cloudflare_challenge_page_is_a_block() -> None:
    def fetch(url: str) -> FetchResponse:
        if url.endswith("/robots.txt"):
            return _response("<html>Just a moment...</html>")
        raise AssertionError(url)

    result = fetch_shop(
        _html_shop("eherbata", "https://eherbata.pl/", shop_id="eherbata", currency="PLN"),
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.error == "HTTP 403 (blocked)"


def test_authored_eherbata_microdata_keeps_the_tea() -> None:
    """The HTML is authored: Cloudflare blocked a live capture."""

    def fetch(url: str) -> FetchResponse:
        if url.endswith("/robots.txt"):
            return _response("User-agent: *\n", status=200)
        return _response(_text("eherbata_authored.html"))

    result = fetch_shop(
        _html_shop("eherbata", "https://eherbata.pl/", shop_id="eherbata", currency="PLN"),
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.status == "ok"
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer["tea_id"] == "xihu-longjing"
    assert offer["category"] == "green"
    assert offer["pack_size_g"] == 50
    assert offer["price"] == 40.0
    assert offer["currency"] == "PLN"
    assert offer["price_per_100g"] == 80.0


def test_unrecognized_listing_fails_the_shop() -> None:
    def fetch(url: str) -> FetchResponse:
        if url.endswith("/robots.txt"):
            return _response("", status=404, error="HTTP 404")
        return _response("<html><body>hello</body></html>")

    result = fetch_shop(
        _html_shop("yoshien", "https://www.yoshien.com/gruener-tee/gruener-tee-china/", shop_id="yoshien", currency="EUR"),
        fetch,
        fetched_at=_FETCHED,
        pause_s=0,
        sleep=lambda _seconds: None,
    )
    assert result.status == "error"
    assert result.error == "listing format not recognized"


def test_html_body_is_returned_as_text() -> None:
    class _Response:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
            self.text = "<html>Long Jing</html>"

        def json(self):
            raise ValueError("not json")

    parsed = _from_response(_Response())
    assert parsed.error is None
    assert parsed.data == "<html>Long Jing</html>"
