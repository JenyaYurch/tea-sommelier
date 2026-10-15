"""One saved offer. Field names are the contract for a later price tool."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from tea_agent.eu_shops.pack import PackParse, money_number, price_per_100g


def make_offer(
    *,
    tea_id: str,
    title: str,
    shop_id: str,
    shop: str,
    url: str,
    price: Decimal,
    currency: str,
    pack: PackParse,
    in_stock: bool,
    fetched_at: str,
) -> dict[str, Any]:
    """Build one index row. Unclear packs keep the offer and leave per-100g empty."""
    unclear = pack.unclear or pack.grams is None
    grams = None if unclear else pack.grams
    per_100 = None if unclear else price_per_100g(price, grams)
    if per_100 is None:
        unclear = True
        grams = None
    return {
        "tea_id": tea_id,
        "title": title,
        "shop_id": shop_id,
        "shop": shop,
        "url": url,
        "price": money_number(price),
        "currency": currency,
        "pack_size_g": grams,
        "pack_size_unclear": unclear,
        "price_per_100g": money_number(per_100),
        "in_stock": in_stock,
        "fetched_at": fetched_at,
    }
