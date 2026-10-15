"""Refresh the PL/DE Chinese tea price index from configured shops."""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tea_agent.eu_price_index import REFRESH_INTERVAL_DAYS
from tea_agent.eu_shops.models import FetchResponse, Shop, ShopResult
from tea_agent.eu_shops.shopify import fetch_shop as fetch_shopify
from tea_agent.eu_shops.woocommerce import fetch_shop as fetch_woocommerce

SHOP_CONCURRENCY = 2
PAGE_PAUSE_SECONDS = 0.4
VARIATION_PAUSE_SECONDS = 0.2

logger = logging.getLogger("tea_agent.eu_shops")


def utc_now_stamp(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def refresh_shops(
    shops: list[Shop],
    fetch: Callable[[str], FetchResponse] | None = None,
    *,
    fetched_at: str | None = None,
    max_pages: int | None = None,
    pause_s: float = PAGE_PAUSE_SECONDS,
    variation_pause_s: float = VARIATION_PAUSE_SECONDS,
    concurrency: int = SHOP_CONCURRENCY,
    sleep: Callable[[float], None] | None = None,
) -> list[ShopResult]:
    """Read every shop. One failure does not stop the others.

    Pass ``fetch`` from tests. The script leaves it empty and uses HTTP.
    """
    stamp = fetched_at or utc_now_stamp()
    pause = sleep or time.sleep
    workers = max(1, min(concurrency, len(shops) or 1))

    def run(shop: Shop) -> ShopResult:
        logger.info("Reading %s (%s)", shop.id, shop.platform)
        try:
            return _refresh_one(
                shop,
                fetch=fetch,
                fetched_at=stamp,
                max_pages=max_pages,
                pause_s=pause_s,
                variation_pause_s=variation_pause_s,
                sleep=pause,
            )
        except Exception as exc:
            return ShopResult(
                shop_id=shop.id,
                name=shop.name,
                platform=shop.platform,
                status="error",
                error=f"unexpected error: {exc.__class__.__name__}",
            )

    if workers == 1:
        return [run(shop) for shop in shops]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, shops))


def build_document(
    results: list[ShopResult],
    *,
    fetched_at: str,
) -> dict[str, Any]:
    offers = [offer for result in results for offer in result.offers]
    offers.sort(
        key=lambda offer: (
            str(offer.get("tea_id") or ""),
            str(offer.get("shop_id") or ""),
            offer.get("pack_size_g") if isinstance(offer.get("pack_size_g"), int) else 10**9,
            str(offer.get("url") or ""),
        )
    )
    day = fetched_at[:10]
    return {
        "schema_version": 2,
        "source": "pl-de-open-catalogs",
        "generated_on": day,
        "last_checked": day,
        "fetched_at": fetched_at,
        "refresh_interval_days": REFRESH_INTERVAL_DAYS,
        "shops": [result.report() for result in results],
        "count": len(offers),
        "offers": offers,
    }


def write_index(document: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _category_counts(offers: list[dict[str, Any]]) -> str:
    counts = Counter(str(offer.get("category")) for offer in offers if offer.get("category"))
    if not counts:
        return ""
    parts = [f"{name} {counts[name]}" for name in sorted(counts)]
    return " (" + ", ".join(parts) + ")"


def format_summary(results: list[ShopResult]) -> str:
    lines: list[str] = []
    offers = [offer for result in results for offer in result.offers]
    ok = sum(1 for result in results if result.status == "ok")
    in_stock = sum(1 for offer in offers if offer.get("in_stock") is True)
    unclear = sum(1 for offer in offers if offer.get("pack_size_unclear") is True)
    shops_with_offers = sum(1 for result in results if result.offers)
    for result in results:
        err = f" — {result.error}" if result.error else ""
        warn = f" ({len(result.warnings)} warnings)" if result.warnings else ""
        stock = sum(1 for offer in result.offers if offer.get("in_stock") is True)
        lines.append(
            f"{result.shop_id:<14} {result.status:<5} {len(result.offers):>4} offers"
            f"  {stock:>4} in stock{_category_counts(result.offers)}{warn}{err}"
        )
    lines.append(
        f"shops ok: {ok}/{len(results)}  shops with offers: {shops_with_offers}  "
        f"offers: {len(offers)}  in stock: {in_stock}  unclear pack: {unclear}"
        f"{_category_counts(offers)}"
    )
    counts = Counter(str(offer.get("tea_id") or "") for offer in offers)
    for tea_id, count in counts.most_common(12):
        lines.append(f"  {tea_id}: {count}")
    return "\n".join(lines)


def should_write(results: list[ShopResult]) -> str | None:
    """Return an error message when the snapshot must not replace the file."""
    if not any(result.status == "ok" for result in results):
        return "no shop could be read; index not written"
    if not any(result.offers for result in results):
        return "no Chinese tea offers; index not written"
    return None


def _refresh_one(
    shop: Shop,
    *,
    fetch: Callable[[str], FetchResponse] | None,
    fetched_at: str,
    max_pages: int | None,
    pause_s: float,
    variation_pause_s: float,
    sleep: Callable[[float], None],
) -> ShopResult:
    fetcher = fetch if fetch is not None else _live_fetcher()
    kwargs: dict[str, Any] = {
        "fetched_at": fetched_at,
        "pause_s": pause_s,
        "sleep": sleep,
    }
    if max_pages is not None:
        kwargs["max_pages"] = max_pages
    if shop.platform == "shopify":
        return fetch_shopify(shop, fetcher, **kwargs)
    if shop.platform == "woocommerce":
        kwargs["variation_pause_s"] = variation_pause_s
        return fetch_woocommerce(shop, fetcher, **kwargs)
    if shop.platform == "html":
        from tea_agent.eu_shops.html_catalog import fetch_shop as fetch_html

        return fetch_html(shop, fetcher, **kwargs)
    return ShopResult(
        shop_id=shop.id,
        name=shop.name,
        platform=shop.platform,
        status="error",
        error=f"unknown platform: {shop.platform}",
    )


def _live_fetcher() -> Callable[[str], FetchResponse]:
    from tea_agent.eu_shops.live_fetch import RequestsFetcher

    return RequestsFetcher()
