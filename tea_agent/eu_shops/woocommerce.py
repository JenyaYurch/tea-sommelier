"""WooCommerce Store API adapter (``/wp-json/wc/store/v1/products``).

The list payload has variation ids and attribute slugs, not per-variation
prices. Matching products are the only ones whose variations are fetched.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from typing import Any

from tea_agent.eu_shops.classify import match_chinese_tea
from tea_agent.eu_shops.models import FetchResponse, Shop, ShopResult
from tea_agent.eu_shops.offer import make_offer
from tea_agent.eu_shops.pack import resolve_pack, woo_major_price

PAGE_LIMIT = 100
MAX_PAGES = 20
_MAX_WARNINGS = 8


def products_url(base_url: str, page: int, per_page: int = PAGE_LIMIT) -> str:
    return (
        f"{base_url}/wp-json/wc/store/v1/products?per_page={per_page}&page={page}"
    )


def product_url(base_url: str, product_id: int) -> str:
    return f"{base_url}/wp-json/wc/store/v1/products/{product_id}"


def fetch_shop(
    shop: Shop,
    fetch: Callable[[str], FetchResponse],
    *,
    fetched_at: str,
    max_pages: int = MAX_PAGES,
    page_limit: int = PAGE_LIMIT,
    pause_s: float = 0.4,
    variation_pause_s: float = 0.2,
    sleep: Callable[[float], None] | None = None,
) -> ShopResult:
    """Page the Store API, then fetch variations only for matched Chinese teas."""
    pause = sleep or _noop_sleep
    products, pages, error = _paginate(
        shop,
        fetch,
        max_pages=max_pages,
        page_limit=page_limit,
        pause_s=pause_s,
        sleep=pause,
    )
    if error:
        return _error(shop, error, pages)

    offers: list[dict[str, Any]] = []
    warnings: list[str] = []
    for product in products:
        title = _clean_name(str(product.get("name") or ""))
        if not title or match_chinese_tea(title, _context(product)) is None:
            continue
        details: dict[int, dict[str, Any]] = {}
        for ref in _variation_refs(product):
            try:
                vid = int(ref.get("id"))
            except (TypeError, ValueError):
                _add_warning(warnings, f"variation id missing on {title}")
                continue
            if variation_pause_s:
                pause(variation_pause_s)
            response = fetch(product_url(shop.base_url, vid))
            if (
                response.error
                or response.status != 200
                or not isinstance(response.data, dict)
            ):
                message = response.error or f"HTTP {response.status}"
                _add_warning(warnings, f"variation {vid}: {message}")
                continue
            details[vid] = response.data
        product_offers, product_warnings = offers_from_product(
            product,
            variations=details,
            shop=shop,
            fetched_at=fetched_at,
        )
        offers.extend(product_offers)
        for message in product_warnings:
            _add_warning(warnings, message)
    return ShopResult(
        shop_id=shop.id,
        name=shop.name,
        platform=shop.platform,
        status="ok",
        offers=_dedupe(offers),
        warnings=warnings,
        pages=pages,
    )


def offers_from_product(
    product: dict[str, Any],
    *,
    variations: dict[int, dict[str, Any]],
    shop: Shop,
    fetched_at: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Map one Store API product. Variable products become one offer per variation."""
    title = _clean_name(str(product.get("name") or ""))
    match = match_chinese_tea(title, _context(product))
    if not title or match is None:
        return [], []
    refs = _variation_refs(product)
    if product.get("type") == "variable" and not refs:
        return [], [f"no variations on {title}"]
    if refs:
        offers: list[dict[str, Any]] = []
        warnings: list[str] = []
        for ref in refs:
            try:
                vid = int(ref.get("id"))
            except (TypeError, ValueError):
                warnings.append(f"variation id missing on {title}")
                continue
            detail = variations.get(vid)
            if not isinstance(detail, dict):
                continue
            offer = _offer_from_variation(
                parent=product,
                ref=ref,
                detail=detail,
                tea_id=match.tea_id,
                category=match.category,
                title=title,
                shop=shop,
                fetched_at=fetched_at,
            )
            if offer is not None:
                offers.append(offer)
        return offers, warnings

    price, currency = woo_major_price(product.get("prices"))
    if price is None:
        return [], []
    currency = currency or (shop.currency or "").strip()
    if not currency:
        return [], [f"currency missing on {title}"]
    url = str(product.get("permalink") or "").strip()
    if not url:
        return [], []
    return [
        make_offer(
            tea_id=match.tea_id,
            category=match.category,
            title=title,
            shop_id=shop.id,
            shop=shop.name,
            url=url,
            price=price,
            currency=currency,
            pack=resolve_pack("", title),
            in_stock=_in_stock(product),
            fetched_at=fetched_at,
        )
    ], []


def _offer_from_variation(
    *,
    parent: dict[str, Any],
    ref: dict[str, Any],
    detail: dict[str, Any],
    tea_id: str,
    category: str,
    title: str,
    shop: Shop,
    fetched_at: str,
) -> dict[str, Any] | None:
    price, currency = woo_major_price(detail.get("prices"))
    if price is None:
        return None
    currency = currency or (shop.currency or "").strip()
    if not currency:
        return None
    url = str(detail.get("permalink") or "").strip()
    if not url:
        return None
    pack = resolve_pack(_variation_text(parent, ref, detail), title)
    return make_offer(
        tea_id=tea_id,
        category=category,
        title=title,
        shop_id=shop.id,
        shop=shop.name,
        url=url,
        price=price,
        currency=currency,
        pack=pack,
        in_stock=_in_stock(detail),
        fetched_at=fetched_at,
    )


def _paginate(
    shop: Shop,
    fetch: Callable[[str], FetchResponse],
    *,
    max_pages: int,
    page_limit: int,
    pause_s: float,
    sleep: Callable[[float], None],
) -> tuple[list[dict[str, Any]], int, str | None]:
    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = 0
    for page in range(1, max_pages + 1):
        response = fetch(products_url(shop.base_url, page, page_limit))
        if response.error or response.status != 200:
            return [], pages, response.error or f"HTTP {response.status}"
        if not isinstance(response.data, list):
            return [], pages, "response was not a WooCommerce product list"
        batch = [item for item in response.data if isinstance(item, dict)]
        pages = page
        if not batch:
            break
        ids = [str(item.get("id") or "") for item in batch]
        fresh = [item for item, pid in zip(batch, ids, strict=True) if pid not in seen]
        if not fresh:
            return [], pages, "pagination returned a repeated page"
        seen.update(ids)
        products.extend(fresh)
        total_pages = _total_pages(response.headers)
        if total_pages is not None and page >= total_pages:
            break
        if total_pages is None and len(batch) < page_limit:
            break
        if page == max_pages and (total_pages is None or total_pages > max_pages):
            return [], pages, f"pagination cap reached ({max_pages} pages)"
        if pause_s:
            sleep(pause_s)
    return products, pages, None


def _variation_refs(product: dict[str, Any]) -> list[dict[str, Any]]:
    if product.get("type") not in {None, "variable"}:
        return []
    raw = product.get("variations")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("id") is not None]


def _variation_text(
    parent: dict[str, Any], ref: dict[str, Any], detail: dict[str, Any]
) -> str:
    parts: list[str] = []
    label = str(detail.get("variation") or "").strip()
    if label:
        parts.append(label)
    attributes = ref.get("attributes")
    if isinstance(attributes, list):
        for attr in attributes:
            if not isinstance(attr, dict):
                continue
            value = str(attr.get("value") or "").strip()
            if value:
                parts.append(value)
            term = _term_name(parent, str(attr.get("name") or ""), value)
            if term:
                parts.append(term)
    return " ".join(parts)


def _term_name(parent: dict[str, Any], attr_name: str, slug: str) -> str:
    raw = parent.get("attributes")
    if not isinstance(raw, list) or not slug:
        return ""
    wanted = attr_name.strip().lower()
    for attr in raw:
        if not isinstance(attr, dict):
            continue
        if str(attr.get("name") or "").strip().lower() != wanted:
            continue
        terms = attr.get("terms")
        if not isinstance(terms, list):
            continue
        for term in terms:
            if isinstance(term, dict) and str(term.get("slug") or "") == slug:
                return str(term.get("name") or "")
    return ""


def _context(product: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("categories", "tags"):
        raw = product.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                parts.append(str(item["name"]))
            elif isinstance(item, str):
                parts.append(item)
    return " ".join(parts)


def _in_stock(product: dict[str, Any]) -> bool:
    stock = product.get("stock_availability") or {}
    css = str(stock.get("class") or "").lower() if isinstance(stock, dict) else ""
    if "out-of-stock" in css or "outofstock" in css:
        return False
    return product.get("is_in_stock") is True


def _total_pages(headers: dict[str, str]) -> int | None:
    raw = headers.get("x-wp-totalpages") or headers.get("X-WP-TotalPages")
    if raw in (None, ""):
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _clean_name(name: str) -> str:
    text = html.unescape(name or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _dedupe(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for offer in offers:
        key = str(offer.get("url") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(offer)
    return out


def _add_warning(warnings: list[str], message: str) -> None:
    if message in warnings:
        return
    if len(warnings) < _MAX_WARNINGS:
        warnings.append(message)
        return
    note = "further warnings omitted"
    if note not in warnings:
        warnings.append(note)


def _error(shop: Shop, message: str, pages: int) -> ShopResult:
    return ShopResult(
        shop_id=shop.id,
        name=shop.name,
        platform=shop.platform,
        status="error",
        error=message,
        pages=pages,
    )


def _noop_sleep(_seconds: float) -> None:
    return None
