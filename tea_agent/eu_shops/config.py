"""Shop list for the PL/DE price index. Edit ``data/eu_green_shops.json`` to add one."""

from __future__ import annotations

import json
from pathlib import Path

from tea_agent.eu_shops.models import Shop

_PLATFORMS = frozenset({"shopify", "woocommerce", "html"})
_HTML_PARSERS = frozenset({"czajownia", "yoshien", "nanyi", "eherbata"})


def shops_path() -> Path:
    here = Path(__file__).resolve()
    candidates = (
        here.parents[2] / "data" / "eu_green_shops.json",
        here.parents[1] / "data" / "eu_green_shops.json",
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def load_shops(path: Path | None = None) -> list[Shop]:
    """Load the configured shops. Raises ValueError on a broken entry."""
    source = path or shops_path()
    raw = json.loads(source.read_text(encoding="utf-8"))
    rows = raw.get("shops") if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"shop config has no shops: {source}")
    shops: list[Shop] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("shop entry must be an object")
        shop = _shop_from_row(row)
        if shop.id in seen:
            raise ValueError(f"duplicate shop id: {shop.id}")
        seen.add(shop.id)
        shops.append(shop)
    return shops


def _shop_from_row(row: dict) -> Shop:
    shop_id = str(row.get("id") or "").strip()
    name = str(row.get("name") or "").strip()
    platform = str(row.get("platform") or "").strip().lower()
    base_url = str(row.get("base_url") or "").strip().rstrip("/")
    country = str(row.get("country") or "").strip()
    currency_raw = row.get("currency")
    currency = str(currency_raw).strip() if currency_raw else None
    if not shop_id or not name or not country:
        raise ValueError(f"shop entry missing id, name, or country: {row!r}")
    if platform not in _PLATFORMS:
        raise ValueError(f"{shop_id}: platform must be shopify, woocommerce, or html")
    if not base_url.startswith("https://"):
        raise ValueError(f"{shop_id}: base_url must be https")
    parser_raw = row.get("parser")
    parser = str(parser_raw).strip().lower() if parser_raw else None
    raw_urls = row.get("listing_urls") or []
    if not isinstance(raw_urls, list):
        raise ValueError(f"{shop_id}: listing_urls must be a list")
    listing_urls = tuple(str(url).strip() for url in raw_urls if str(url).strip())
    if platform == "html":
        if parser not in _HTML_PARSERS:
            allowed = ", ".join(sorted(_HTML_PARSERS))
            raise ValueError(f"{shop_id}: html parser must be one of: {allowed}")
        if not listing_urls:
            raise ValueError(f"{shop_id}: html shop needs listing_urls")
        for url in listing_urls:
            if not url.startswith("https://"):
                raise ValueError(f"{shop_id}: listing URL must be https")
    return Shop(
        id=shop_id,
        name=name,
        platform=platform,
        base_url=base_url,
        country=country,
        currency=currency,
        parser=parser,
        listing_urls=listing_urls,
    )
