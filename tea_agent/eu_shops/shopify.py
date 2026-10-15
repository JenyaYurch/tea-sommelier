"""Shopify ``/products.json`` adapter. One variant is one offer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tea_agent.eu_shops.classify import match_chinese_green
from tea_agent.eu_shops.models import FetchResponse, Shop, ShopResult
from tea_agent.eu_shops.offer import make_offer
from tea_agent.eu_shops.pack import parse_major_price, resolve_pack

PAGE_LIMIT = 250
MAX_PAGES = 12
_DEFAULT_VARIANT = {"default title", "default", "title"}


def products_url(base_url: str, page: int, limit: int = PAGE_LIMIT) -> str:
    return f"{base_url}/products.json?limit={limit}&page={page}"


def meta_url(base_url: str) -> str:
    return f"{base_url}/meta.json"


def fetch_shop(
    shop: Shop,
    fetch: Callable[[str], FetchResponse],
    *,
    fetched_at: str,
    max_pages: int = MAX_PAGES,
    page_limit: int = PAGE_LIMIT,
    pause_s: float = 0.4,
    sleep: Callable[[float], None] | None = None,
) -> ShopResult:
    """Page the public catalog. A failed shop contributes no offers."""
    pause = sleep or _noop_sleep
    currency, currency_error = _currency(shop, fetch)
    if not currency:
        return _error(shop, currency_error or "currency unknown")

    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = 0
    for page in range(1, max_pages + 1):
        response = fetch(products_url(shop.base_url, page, page_limit))
        if response.error or response.status != 200:
            return _error(shop, response.error or f"HTTP {response.status}", pages)
        payload = response.data
        if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
            return _error(shop, "response was not a Shopify product list", pages)
        batch = [item for item in payload["products"] if isinstance(item, dict)]
        pages = page
        if not batch:
            break
        ids = [_product_id(item) for item in batch]
        fresh = [item for item, pid in zip(batch, ids, strict=True) if pid not in seen]
        if not fresh:
            return _error(shop, "pagination returned a repeated page", pages)
        for pid in ids:
            seen.add(pid)
        products.extend(fresh)
        if len(batch) < page_limit:
            break
        if page == max_pages:
            return _error(
                shop,
                f"pagination cap reached ({max_pages} pages)",
                pages,
            )
        if pause_s:
            pause(pause_s)

    offers: list[dict[str, Any]] = []
    for product in products:
        offers.extend(
            offers_from_product(
                product,
                shop=shop,
                currency=currency,
                fetched_at=fetched_at,
            )
        )
    return ShopResult(
        shop_id=shop.id,
        name=shop.name,
        platform=shop.platform,
        status="ok",
        offers=_dedupe(offers),
        pages=pages,
    )


def offers_from_product(
    product: dict[str, Any],
    *,
    shop: Shop,
    currency: str,
    fetched_at: str,
) -> list[dict[str, Any]]:
    """Map one products.json object to offers. Shipping ``grams`` is ignored."""
    title = " ".join(str(product.get("title") or "").split())
    if not title:
        return []
    tea_id = match_chinese_green(title, _context(product))
    if not tea_id:
        return []
    handle = str(product.get("handle") or "").strip()
    if not handle:
        return []
    offers: list[dict[str, Any]] = []
    variants = product.get("variants")
    if not isinstance(variants, list):
        return []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        price = parse_major_price(variant.get("price"))
        if price is None:
            continue
        variant_id = variant.get("id")
        if variant_id in (None, ""):
            continue
        label = _variant_label(variant)
        pack = resolve_pack(label, title)
        offers.append(
            make_offer(
                tea_id=tea_id,
                title=title,
                shop_id=shop.id,
                shop=shop.name,
                url=f"{shop.base_url}/products/{handle}?variant={variant_id}",
                price=price,
                currency=currency,
                pack=pack,
                in_stock=variant.get("available") is True,
                fetched_at=fetched_at,
            )
        )
    return offers


def _currency(shop: Shop, fetch: Callable[[str], FetchResponse]) -> tuple[str | None, str | None]:
    response = fetch(meta_url(shop.base_url))
    if response.status == 200 and isinstance(response.data, dict):
        code = str(response.data.get("currency") or "").strip()
        if code:
            return code, None
    fallback = (shop.currency or "").strip()
    if fallback:
        return fallback, None
    if response.error:
        return None, response.error
    return None, "currency unknown"


def _context(product: dict[str, Any]) -> str:
    tags = product.get("tags")
    if isinstance(tags, list):
        tag_text = " ".join(str(tag) for tag in tags)
    else:
        tag_text = str(tags or "")
    return " ".join(
        part for part in (str(product.get("product_type") or ""), tag_text) if part
    )


def _variant_label(variant: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("option1", "option2", "option3", "title"):
        value = str(variant.get(key) or "").strip()
        if value.lower() in _DEFAULT_VARIANT:
            continue
        parts.append(value)
    return " ".join(parts)


def _product_id(product: dict[str, Any]) -> str:
    pid = product.get("id")
    if pid not in (None, ""):
        return str(pid)
    return str(product.get("handle") or product.get("title") or "")


def _dedupe(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for offer in offers:
        key = str(offer.get("url") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(offer)
    return out


def _error(shop: Shop, message: str, pages: int = 0) -> ShopResult:
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
