"""Scrape teashop.by Chinese-tea categories into data/teashop_catalog.json.

Biweekly refresh checklist (every 1–2 weeks):
  1. uv run python scripts/parse_teashop.py --status
  2. If needs_refresh: uv run python scripts/parse_teashop.py
  3. Spot-check 2–3 products (price / availability / product_url)
  4. Commit data/teashop_catalog.json when the feed looks good

Stock: listing cards mark availability on the parent li (instock / outofstock),
not on div.product-item. Unknown stock stays "unknown". If category HTML
returns 403, the WooCommerce store API is used instead (is_in_stock).
The writer refuses a catalog where every SKU is in_stock unless
--allow-all-in-stock is set.

Usage:
  uv run python scripts/parse_teashop.py --status
  uv run python scripts/parse_teashop.py
  uv run python scripts/parse_teashop.py --max-pages 6 --dry-run
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

# Allow `uv run python scripts/parse_teashop.py` without installing as module path quirks.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tea_agent.shop_catalog import (  # noqa: E402
    REFRESH_INTERVAL_DAYS,
    catalog_freshness,
    catalog_meta,
    match_product_to_slug,
    parse_price_byn,
    remap_unmatched_items,
)

logger = logging.getLogger("parse_teashop")

# WooCommerce store API category ids for CATEGORY_URLS (stable term ids).
# Used when category HTML returns 403. Stock comes from is_in_stock /
# stock_availability, not from a missing CSS class.
STORE_API_PRODUCTS = "https://www.teashop.by/wp-json/wc/store/v1/products"
STORE_CATEGORY_IDS: dict[str, int] = {
    "https://www.teashop.by/shop/chaj/beliy/": 27,
    "https://www.teashop.by/shop/chaj/zheltiy/": 28,
    "https://www.teashop.by/shop/chaj/zeleniy/": 25,
    "https://www.teashop.by/shop/chaj/cherniy/": 24,
    "https://www.teashop.by/shop/chaj/puer/": 45,
    "https://www.teashop.by/shop/chaj/ulun/gaba/": 121,
}
_STOCK_OUT = "out_of_stock"
_STOCK_IN = "in_stock"
_STOCK_UNKNOWN = "unknown"


class TeashopBlocked(RuntimeError):
    """teashop.by refused an automated request (HTTP 403)."""

# v1 shop scope: white, yellow, green, red, puer (sheng+shu), GABA.
# Full oolong trees are a follow-up; GABA lives under ulun/gaba/.
CATEGORY_URLS: tuple[str, ...] = (
    "https://www.teashop.by/shop/chaj/beliy/",
    "https://www.teashop.by/shop/chaj/zheltiy/",
    "https://www.teashop.by/shop/chaj/zeleniy/",
    "https://www.teashop.by/shop/chaj/cherniy/",
    "https://www.teashop.by/shop/chaj/puer/",
    "https://www.teashop.by/shop/chaj/ulun/gaba/",
)
CATEGORY_LABELS: dict[str, str] = {
    "beliy": "Белый чай",
    "zheltiy": "Жёлтый чай",
    "zeleniy": "Зеленый чай",
    "cherniy": "Красный чай",
    "puer": "Пуэр",
    "gaba": "GABA",
}
GREEN_CATEGORY_URL = "https://www.teashop.by/shop/chaj/zeleniy/"
CATEGORY_URL = GREEN_CATEGORY_URL
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def clean_description(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["div", "img"], class_="wp-caption alignnone"):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # Keep catalog compact for the agent tool.
    return text[:1200]


def extract_breadcrumbs(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    breadcrumbs = soup.select("ul.breadcrumbs li a")
    category = breadcrumbs[-2].get_text(strip=True) if len(breadcrumbs) >= 2 else None
    subcategory = (
        breadcrumbs[-1].get_text(strip=True) if len(breadcrumbs) >= 1 else None
    )
    return category, subcategory


def _category_key(category_url: str) -> str:
    return category_url.rstrip("/").rsplit("/", 1)[-1]


def _fallback_category_label(category_url: str) -> str:
    return CATEGORY_LABELS.get(_category_key(category_url), "Чай")


def _page_url(category_url: str, page: int) -> str:
    base = category_url if category_url.endswith("/") else f"{category_url}/"
    if page <= 1:
        return base
    return f"{base}page/{page}/"


def _extract_weight(form_tag: Any) -> int | None:
    if not form_tag or not form_tag.has_attr("data-product_variations"):
        return None
    try:
        variations = json.loads(str(form_tag["data-product_variations"]))
    except (TypeError, json.JSONDecodeError):
        return None
    if not variations or not isinstance(variations, list):
        return None
    raw_weight = variations[0].get("attributes", {}).get("attribute_pa_ves")
    if not raw_weight:
        return None
    match = re.search(r"(\d+)", str(raw_weight))
    return int(match.group(1)) if match else None


def _class_tokens(classes: Any) -> set[str]:
    if isinstance(classes, str):
        return set(classes.split())
    return {str(token) for token in (classes or [])}


def _availability_from_classes(classes: Any) -> str | None:
    tokens = _class_tokens(classes)
    if "outofstock" in tokens or "out-of-stock" in tokens:
        return _STOCK_OUT
    if "instock" in tokens or "in-stock" in tokens:
        return _STOCK_IN
    return None


def _availability_from_variations(node: Any) -> str | None:
    form = node.select_one("form.variations_form") if hasattr(node, "select_one") else None
    if form is None or not form.has_attr("data-product_variations"):
        return None
    raw = html.unescape(str(form["data-product_variations"]))
    try:
        variations = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(variations, list):
        return None
    flags = [
        item.get("is_in_stock")
        for item in variations
        if isinstance(item, dict) and "is_in_stock" in item
    ]
    if not flags:
        return None
    if any(flag is False for flag in flags) and not any(flag is True for flag in flags):
        return _STOCK_OUT
    if any(flag is True for flag in flags):
        return _STOCK_IN
    return None


def _availability_from_schema(node: Any) -> str | None:
    if not hasattr(node, "select"):
        return None
    found_in = False
    for link in node.select('link[itemprop="availability"], meta[itemprop="availability"]'):
        href = str(link.get("href") or link.get("content") or "").lower()
        if "outofstock" in href:
            return _STOCK_OUT
        if "instock" in href:
            found_in = True
    return _STOCK_IN if found_in else None


def _availability_from_text(node: Any) -> str | None:
    if not hasattr(node, "get_text"):
        return None
    text = node.get_text(" ", strip=True).lower().replace("ё", "е")
    if "нет в наличии" in text or "нет на складе" in text:
        return _STOCK_OUT
    if "в наличии" in text:
        return _STOCK_IN
    return None


def _availability(product: Any) -> str:
    """Read WooCommerce stock. Unknown stays unknown — never assume in stock.

    Circolare listing cards put ``instock`` / ``outofstock`` on the parent
    ``li.product-grid-container-inner``. The inner ``div.product-item`` has
    neither class, so reading only that node marks every SKU in stock.
    """
    signals: list[str] = []
    node = product
    first = True
    for _ in range(6):
        if node is None or not hasattr(node, "get"):
            break
        if getattr(node, "name", None) in {"body", "html", "[document]"}:
            break
        class_signal = _availability_from_classes(node.get("class"))
        if class_signal:
            signals.append(class_signal)
        if first:
            if hasattr(node, "select_one"):
                if node.select_one("p.stock.out-of-stock, .stock.out-of-stock"):
                    signals.append(_STOCK_OUT)
                elif node.select_one("p.stock.in-stock, .stock.in-stock"):
                    signals.append(_STOCK_IN)
            schema_signal = _availability_from_schema(node)
            if schema_signal:
                signals.append(schema_signal)
            variation_signal = _availability_from_variations(node)
            if variation_signal:
                signals.append(variation_signal)
            text_signal = _availability_from_text(node)
            if text_signal:
                signals.append(text_signal)
            first = False
        node = getattr(node, "parent", None)
    if _STOCK_OUT in signals:
        return _STOCK_OUT
    if _STOCK_IN in signals:
        return _STOCK_IN
    return _STOCK_UNKNOWN


def availability_from_store_product(product: dict[str, Any]) -> str:
    """Map a WooCommerce store API product to in_stock / out_of_stock / unknown."""
    stock = product.get("stock_availability") or {}
    if not isinstance(stock, dict):
        stock = {}
    css = str(stock.get("class") or "").lower()
    text = str(stock.get("text") or "").lower().replace("ё", "е")
    flag = product.get("is_in_stock")
    if (
        "out-of-stock" in css
        or "outofstock" in css
        or "нет в наличии" in text
        or flag is False
    ):
        return _STOCK_OUT
    if "in-stock" in css or "instock" in css or flag is True:
        return _STOCK_IN
    return _STOCK_UNKNOWN


def store_price_byn(prices: dict[str, Any] | None) -> float | None:
    """Convert store API minor units (1590, minor 2) to BYN (15.90)."""
    if not isinstance(prices, dict):
        return None
    try:
        minor = int(prices.get("currency_minor_unit") or 2)
    except (TypeError, ValueError):
        minor = 2
    raw = None
    price_range = prices.get("price_range") or {}
    if isinstance(price_range, dict):
        raw = price_range.get("min_amount")
    if raw in (None, ""):
        raw = prices.get("price")
    if raw in (None, ""):
        return None
    try:
        return int(str(raw)) / (10**minor)
    except (TypeError, ValueError):
        return None


def _clean_store_name(name: str) -> str:
    text = html.unescape(name or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _weight_from_store_product(product: dict[str, Any]) -> int | None:
    variations = product.get("variations") or []
    if not isinstance(variations, list) or not variations:
        return None
    first = variations[0]
    if not isinstance(first, dict):
        return None
    attrs = first.get("attributes") or []
    if not isinstance(attrs, list) or not attrs or not isinstance(attrs[0], dict):
        return None
    match = re.search(r"(\d+)", str(attrs[0].get("value") or ""))
    return int(match.group(1)) if match else None


def _image_from_store_product(product: dict[str, Any]) -> str | None:
    images = product.get("images") or []
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        return None
    return images[0].get("thumbnail") or images[0].get("src")


def item_from_store_product(
    product: dict[str, Any],
    *,
    category_url: str,
    source_page: int,
    today: str,
) -> dict[str, Any]:
    name = _clean_store_name(str(product.get("name") or ""))
    link = product.get("permalink")
    link_s = str(link) if link else None
    matched_slug, confidence = _match_slug(name, link_s)
    year_match = re.search(r"\b(20\d{2})\b", name)
    product_id = product.get("id")
    return {
        "product_id": str(product_id) if product_id is not None else None,
        "product_name": name,
        "matched_slug": matched_slug,
        "mapping_confidence": confidence,
        "price_from_byn": store_price_byn(product.get("prices")),
        "availability": availability_from_store_product(product),
        "harvest_year": int(year_match.group(1)) if year_match else None,
        "weight_g": _weight_from_store_product(product),
        "category": _fallback_category_label(category_url),
        "subcategory": None,
        "category_url": category_url,
        "product_url": link_s,
        "image_url": _image_from_store_product(product),
        "description": None,
        "source_page": source_page,
        "last_checked": today,
    }


def availability_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {_STOCK_IN: 0, _STOCK_OUT: 0, _STOCK_UNKNOWN: 0}
    for item in items:
        key = str(item.get("availability") or _STOCK_UNKNOWN)
        counts[key] = counts.get(key, 0) + 1
    return counts


def all_items_in_stock(items: list[dict[str, Any]]) -> bool:
    return bool(items) and all(item.get("availability") == _STOCK_IN for item in items)


def _match_slug(
    product_name: str, product_url: str | None = None
) -> tuple[str | None, str]:
    """Return (slug, confidence) using the local tea.support dictionary."""
    return match_product_to_slug(product_name, product_url)


def parse_catalog(
    session: requests.Session | None = None,
    max_pages: int = 12,
    fetch_details: bool = True,
    category_urls: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    sess = session or requests.Session()
    urls = tuple(category_urls) if category_urls else CATEGORY_URLS
    try:
        return _parse_html_catalog(
            sess,
            max_pages=max_pages,
            fetch_details=fetch_details,
            category_urls=urls,
        )
    except TeashopBlocked:
        logger.warning(
            "Category HTML returned 403; refreshing via the WooCommerce store API"
        )
        return parse_catalog_via_store_api(
            sess,
            max_pages=max_pages,
            category_urls=urls,
        )


def _parse_html_catalog(
    sess: requests.Session,
    *,
    max_pages: int,
    fetch_details: bool,
    category_urls: tuple[str, ...],
) -> list[dict[str, Any]]:
    teas: list[dict[str, Any]] = []
    today = date.today().isoformat()
    for category_url in category_urls:
        teas.extend(
            _parse_category(
                sess,
                category_url=category_url,
                max_pages=max_pages,
                fetch_details=fetch_details,
                today=today,
            )
        )
    return teas


def _fetch_store_products(
    sess: requests.Session,
    category_id: int,
    *,
    max_pages: int,
) -> list[tuple[int, dict[str, Any]]]:
    found: list[tuple[int, dict[str, Any]]] = []
    headers = {**HEADERS, "Accept": "application/json"}
    for page in range(1, max_pages + 1):
        url = (
            f"{STORE_API_PRODUCTS}?category={category_id}&per_page=100&page={page}"
        )
        logger.info("Fetching store API %s", url)
        try:
            resp = sess.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            logger.warning("Store API failed for %s: %s", url, exc)
            break
        if resp.status_code == 403:
            raise TeashopBlocked(url)
        if resp.status_code != 200:
            logger.warning("Store API non-200 %s for %s", resp.status_code, url)
            break
        try:
            payload = resp.json()
        except ValueError:
            logger.warning("Store API did not return JSON for %s", url)
            break
        if not isinstance(payload, list) or not payload:
            break
        found.extend(
            (page, item) for item in payload if isinstance(item, dict)
        )
        if len(payload) < 100:
            break
        time.sleep(1.0)
    return found


def parse_catalog_via_store_api(
    session: requests.Session | None = None,
    max_pages: int = 12,
    category_urls: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Refresh SKUs from the public WooCommerce store API (explicit stock)."""
    sess = session or requests.Session()
    urls = tuple(category_urls) if category_urls else CATEGORY_URLS
    today = date.today().isoformat()
    teas: list[dict[str, Any]] = []
    for category_url in urls:
        category_id = STORE_CATEGORY_IDS.get(category_url)
        if category_id is None:
            logger.warning("No store category id for %s", category_url)
            continue
        records = _fetch_store_products(sess, category_id, max_pages=max_pages)
        teas.extend(
            item_from_store_product(
                product,
                category_url=category_url,
                source_page=page,
                today=today,
            )
            for page, product in records
        )
        time.sleep(0.8)
    return teas


def _parse_category(
    sess: requests.Session,
    *,
    category_url: str,
    max_pages: int,
    fetch_details: bool,
    today: str,
) -> list[dict[str, Any]]:
    teas: list[dict[str, Any]] = []
    fallback_label = _fallback_category_label(category_url)

    for page in range(1, max_pages + 1):
        url = _page_url(category_url, page)
        logger.info("Fetching %s", url)
        try:
            resp = sess.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException as exc:
            logger.warning("Failed to fetch %s: %s", url, exc)
            break
        if resp.status_code == 403:
            logger.error("Got 403 from teashop.by — site may block automated clients")
            raise TeashopBlocked(url)
        if resp.status_code != 200:
            logger.warning("Non-200 %s for %s", resp.status_code, url)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        # Prefer theme cards; avoid union selectors that double-count the same SKU.
        products = soup.select(".product-item")
        if not products:
            products = soup.select("ul.products li.product")
        if not products:
            logger.info("No products on page %s of %s — stop", page, category_url)
            break

        seen_on_page: set[str] = set()
        for product in products:
            title_tag = product.select_one(
                "div.title h3.title-container.product-titles, h2.woocommerce-loop-product__title, .woocommerce-loop-product__title"
            )
            name = title_tag.get_text(strip=True) if title_tag else None
            if not name:
                continue

            price_tag = product.select_one(
                "span.price span.woocommerce-Price-amount.amount bdi, "
                "span.price .amount, span.woocommerce-Price-amount"
            )
            price_text = price_tag.get_text(strip=True) if price_tag else None

            image_div = product.select_one("div.image.mosaic-block.bar, a.woocommerce-LoopProduct-link")
            image_tag = (
                image_div.select_one("img")
                if image_div
                else product.select_one("img")
            )
            image_url = None
            if image_tag:
                image_url = (
                    image_tag.get("src")
                    or image_tag.get("data-src")
                    or image_tag.get("data-lazy-src")
                )

            form_tag = product.select_one("form.variations_form")
            weight = _extract_weight(form_tag)
            product_id = (
                form_tag["data-product_id"]
                if form_tag and form_tag.has_attr("data-product_id")
                else product.get("data-product-id")
            )

            link_tag = product.select_one("a[href]")
            link = link_tag["href"] if link_tag and link_tag.has_attr("href") else None
            dedupe_key = str(product_id or link or name)
            if dedupe_key in seen_on_page:
                continue
            seen_on_page.add(dedupe_key)

            category, subcategory, description = None, None, None
            availability = _availability(product)
            if fetch_details and link and isinstance(link, str):
                try:
                    detail_resp = sess.get(link, headers=HEADERS, timeout=20)
                    if detail_resp.status_code == 200:
                        detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
                        category, subcategory = extract_breadcrumbs(detail_soup)
                        wrapper = detail_soup.select_one(
                            ".single-product-wrapper"
                        ) or detail_soup.select_one("div.product.type-product")
                        if wrapper is not None:
                            detail_availability = _availability(wrapper)
                            if detail_availability != _STOCK_UNKNOWN:
                                availability = detail_availability
                        desc_div = detail_soup.select_one(
                            "div#tab-description, #tab-description"
                        )
                        if desc_div:
                            description = clean_description(str(desc_div))
                    else:
                        logger.info(
                            "Detail non-200 for %s: %s", link, detail_resp.status_code
                        )
                except requests.RequestException as exc:
                    logger.info("Skip detail %s: %s", link, exc)
                time.sleep(0.5)

            matched_slug, confidence = _match_slug(
                name, link if isinstance(link, str) else None
            )
            harvest = None
            year_match = re.search(r"\b(20\d{2})\b", name)
            if year_match:
                harvest = int(year_match.group(1))

            teas.append(
                {
                    "product_id": str(product_id) if product_id else None,
                    "product_name": name,
                    "matched_slug": matched_slug,
                    "mapping_confidence": confidence,
                    "price_from_byn": parse_price_byn(price_text),
                    "availability": availability,
                    "harvest_year": harvest,
                    "weight_g": weight,
                    "category": category or fallback_label,
                    "subcategory": subcategory,
                    "category_url": category_url,
                    "product_url": link,
                    "image_url": image_url,
                    "description": description,
                    "source_page": page,
                    "last_checked": today,
                }
            )

        time.sleep(0.7)

    return teas


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(
            item.get("product_id")
            or item.get("product_url")
            or item.get("product_name")
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def write_catalog(items: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    items = _dedupe_items(items)
    today = date.today().isoformat()
    payload = {
        "source": "teashop.by",
        "category_url": CATEGORY_URLS[0],
        "category_urls": list(CATEGORY_URLS),
        "generated_on": today,
        "last_checked": today,
        "refresh_interval_days": REFRESH_INTERVAL_DAYS,
        "count": len(items),
        "items": items,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def print_status() -> int:
    """Print catalog freshness; exit 1 when a refresh is due."""
    meta = catalog_meta()
    freshness = catalog_freshness()
    print(f"path: {meta['path']}")
    print(f"exists: {meta['exists']}")
    print(f"count: {meta['count']}")
    print(f"last_checked: {freshness['last_checked']}")
    print(f"age_days: {freshness['age_days']}")
    print(f"refresh_interval_days: {freshness['refresh_interval_days']}")
    print(f"needs_refresh: {freshness['needs_refresh']}")
    if freshness["needs_refresh"]:
        print(
            "Action: run `uv run python scripts/parse_teashop.py` "
            "then commit data/teashop_catalog.json"
        )
        return 1
    print("Catalog is within the 1–2 week refresh window.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse teashop.by Chinese teas")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show last_checked / freshness and exit 1 if refresh is due",
    )
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip product pages (faster; no description/breadcrumbs)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-all-in-stock",
        action="store_true",
        help="Write the catalog even if every SKU is in_stock (hand-checked)",
    )
    parser.add_argument(
        "--remap-existing",
        action="store_true",
        help="Fill unmatched slugs in an existing catalog JSON without fetching the site",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "teashop_catalog.json",
    )
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.INFO,
    )

    if args.status:
        raise SystemExit(print_status())

    if args.remap_existing:
        if not args.out.exists():
            raise SystemExit(f"Catalog not found: {args.out}")
        payload = json.loads(args.out.read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise SystemExit("Catalog JSON has no items list")
        filled = remap_unmatched_items(items)
        matched = sum(1 for i in items if isinstance(i, dict) and i.get("matched_slug"))
        logger.info(
            "Remapped %d previously unmatched items (%d total with slug)",
            filled,
            matched,
        )
        if args.dry_run:
            for item in items:
                if not isinstance(item, dict) or not item.get("matched_slug"):
                    continue
                logger.info(
                    "%s | %s | %s",
                    item.get("product_name"),
                    item.get("matched_slug"),
                    item.get("mapping_confidence"),
                )
            return
        if isinstance(payload, dict):
            payload["count"] = len(items)
            payload["items"] = items
            args.out.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            write_catalog(items, args.out)
        logger.info("Wrote %s (%d items)", args.out, len(items))
        return

    items = parse_catalog(
        max_pages=args.max_pages,
        fetch_details=not args.no_details,
    )
    matched = sum(1 for i in items if i.get("matched_slug"))
    summary = availability_summary(items)
    logger.info(
        "Parsed %d items, %d with matched_slug, availability %s",
        len(items),
        matched,
        summary,
    )
    if all_items_in_stock(items) and not args.allow_all_in_stock:
        message = (
            "Refusing to write catalog: every item is in_stock. "
            "Stock detection saw no out-of-stock or unknown row. "
            "Check the shop by hand, then re-run with --allow-all-in-stock."
        )
        if args.dry_run:
            logger.error(message)
        else:
            raise SystemExit(message)

    if args.dry_run:
        for item in items[:5]:
            logger.info(
                "%s | %s | %s | %s",
                item.get("product_name"),
                item.get("matched_slug"),
                item.get("price_from_byn"),
                item.get("product_url"),
            )
        return

    if not items:
        raise SystemExit("No items parsed — catalog not written")

    write_catalog(items, args.out)
    logger.info("Wrote %s (%d items)", args.out, len(items))


if __name__ == "__main__":
    main()
