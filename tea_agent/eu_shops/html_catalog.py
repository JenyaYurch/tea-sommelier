"""Polite HTML catalog reader. One listing page, then product pages only when needed."""

from __future__ import annotations

import logging
from collections.abc import Callable

from tea_agent.eu_shops.classify import match_chinese_tea
from tea_agent.eu_shops.html_pages import ListingItem, parse_listing, parse_product
from tea_agent.eu_shops.live_fetch import USER_AGENT
from tea_agent.eu_shops.models import FetchResponse, Shop, ShopResult
from tea_agent.eu_shops.offer import make_offer
from tea_agent.eu_shops.pack import resolve_pack
from tea_agent.eu_shops.robots import crawl_delay, robots_allows

logger = logging.getLogger("tea_agent.eu_shops")

HTML_MIN_PAUSE_S = 1.1
MAX_LISTING_PAGES = 8
_MAX_WARNINGS = 8
_CHALLENGE_MARKS = ("just a moment", "cf-challenge", "cf-mitigated", "attention required")


def fetch_shop(
    shop: Shop,
    fetch: Callable[[str], FetchResponse],
    *,
    fetched_at: str,
    max_pages: int = MAX_LISTING_PAGES,
    pause_s: float = HTML_MIN_PAUSE_S,
    sleep: Callable[[float], None] | None = None,
) -> ShopResult:
    """Read listing pages. Czajownia also opens a product page for each matched tea."""
    pause = sleep or _noop_sleep
    if not shop.parser or not shop.listing_urls:
        return _error(shop, "html shop is missing parser or listing URLs")

    robots_url = f"{shop.base_url}/robots.txt"
    robots = fetch(robots_url)
    if _blocked(robots):
        return _error(shop, "HTTP 403 (blocked)")
    if robots.status not in (200, 404):
        return _error(shop, robots.error or f"HTTP {robots.status}")
    robots_text = robots.data if robots.status == 200 and isinstance(robots.data, str) else ""
    if isinstance(robots_text, str) and _challenge(robots_text):
        return _error(shop, "HTTP 403 (blocked)")

    delay = max(HTML_MIN_PAUSE_S, pause_s, crawl_delay(robots_text, USER_AGENT) or 0.0)
    limit = max_pages if max_pages and max_pages > 0 else MAX_LISTING_PAGES
    offers: list[dict] = []
    warnings: list[str] = []
    pages = 0
    requests_made = 1
    seen_pages: set[str] = set()
    seen_products: set[str] = set()
    any_recognized = False
    disallowed = False
    errors: list[str] = []

    for listing in shop.listing_urls:
        queue = [listing]
        listing_pages = 0
        while queue and listing_pages < limit:
            url = queue.pop(0)
            if url in seen_pages:
                continue
            if robots_text and not robots_allows(robots_text, url, USER_AGENT):
                disallowed = True
                _warn(warnings, f"robots.txt disallows {url}")
                continue
            if requests_made:
                pause(delay)
            requests_made += 1
            seen_pages.add(url)
            logger.info("Reading %s listing %s", shop.id, url)
            response = fetch(url)
            pages += 1
            listing_pages += 1
            if _blocked(response):
                return _error(shop, "HTTP 403 (blocked)", pages)
            if response.status == 404 or response.error or not isinstance(response.data, str):
                message = response.error or f"HTTP {response.status}"
                errors.append(message)
                _warn(warnings, f"{url}: {message}")
                continue
            if _challenge(response.data):
                return _error(shop, "HTTP 403 (blocked)", pages)
            parsed = parse_listing(shop.parser, response.data, url)
            if not parsed.recognized:
                _warn(warnings, f"listing format not recognized: {url}")
                continue
            any_recognized = True
            for message in parsed.warnings:
                _warn(warnings, message)
            fresh = [item for item in parsed.items if item.url not in seen_products]
            for item in fresh:
                seen_products.add(item.url)
            if listing_pages > 1 and not fresh:
                break
            for item in fresh:
                outcome = _offers_for_item(
                    shop,
                    item,
                    fetch=fetch,
                    fetched_at=fetched_at,
                    pause=pause,
                    delay=delay,
                    warnings=warnings,
                )
                if outcome == "blocked":
                    return _error(shop, "HTTP 403 (blocked)", pages)
                if isinstance(outcome, list):
                    offers.extend(outcome)
            for nxt in parsed.next_urls:
                if nxt not in seen_pages and nxt not in queue:
                    if not robots_text or robots_allows(robots_text, nxt, USER_AGENT):
                        queue.append(nxt)
                    else:
                        _warn(warnings, f"robots.txt disallows {nxt}")
        if queue and listing_pages >= limit:
            _warn(warnings, f"pagination cap reached ({limit} pages)")

    if not any_recognized:
        if errors:
            message = errors[0]
        elif disallowed:
            message = "robots.txt disallows the listing"
        else:
            message = "listing format not recognized"
        return _error(shop, message, pages)
    return ShopResult(
        shop_id=shop.id,
        name=shop.name,
        platform=shop.platform,
        status="ok",
        offers=_dedupe(offers),
        warnings=warnings,
        pages=pages,
    )


def _offers_for_item(
    shop: Shop,
    item: ListingItem,
    *,
    fetch: Callable[[str], FetchResponse],
    fetched_at: str,
    pause: Callable[[float], None],
    delay: float,
    warnings: list[str],
) -> list[dict] | str | None:
    if item.needs_detail:
        if match_chinese_tea(item.title, item.context) is None:
            return None
        pause(delay)
        response = fetch(item.url)
        if _blocked(response):
            return "blocked"
        if response.error or response.status != 200 or not isinstance(response.data, str):
            _warn(warnings, f"{item.url}: {response.error or 'HTTP ' + str(response.status)}")
            return None
        if _challenge(response.data):
            return "blocked"
        parsed = parse_product(shop.parser or "", response.data, item.url)
        if not parsed.recognized:
            _warn(warnings, f"product format not recognized: {item.url}")
            return None
        for message in parsed.warnings:
            _warn(warnings, message)
        offers: list[dict] = []
        for detail in parsed.items:
            context = detail.context or item.context
            offer = _offer_from_item(shop, detail, context, fetched_at)
            if offer is not None:
                offers.append(offer)
        return offers
    offer = _offer_from_item(shop, item, item.context, fetched_at)
    if offer is None:
        return None
    return [offer]


def _offer_from_item(
    shop: Shop,
    item: ListingItem,
    context: str,
    fetched_at: str,
) -> dict | None:
    match = match_chinese_tea(item.title, context)
    if match is None or item.price is None:
        return None
    currency = (item.currency or shop.currency or "").strip()
    if not currency:
        return None
    return make_offer(
        tea_id=match.tea_id,
        category=match.category,
        title=item.title,
        shop_id=shop.id,
        shop=shop.name,
        url=item.url,
        price=item.price,
        currency=currency,
        pack=resolve_pack(item.pack_text, item.title),
        in_stock=item.in_stock is True,
        fetched_at=fetched_at,
    )


def _blocked(response: FetchResponse) -> bool:
    if response.status == 403:
        return True
    if response.error and "blocked" in response.error.lower():
        return True
    return isinstance(response.data, str) and _challenge(response.data)


def _challenge(text: str) -> bool:
    sample = text[:5000].lower()
    return any(mark in sample for mark in _CHALLENGE_MARKS)


def _warn(warnings: list[str], message: str) -> None:
    if message in warnings:
        return
    if len(warnings) < _MAX_WARNINGS:
        warnings.append(message)
        return
    note = "further warnings omitted"
    if note not in warnings:
        warnings.append(note)


def _dedupe(offers: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for offer in offers:
        key = str(offer.get("url") or "")
        if not key or key in seen:
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
