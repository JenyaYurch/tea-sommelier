"""Local teashop.by catalog for buy links / prices (structured, not RAG)."""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from tea_agent.slug_index import china_green_slugs, fold_text, resolve_query

# teashop.by URL slugs that the name matcher cannot disambiguate.
URL_SLUG_OVERRIDES: dict[str, tuple[str, str]] = {
    "yunnan-maofen-tou-chun": ("yun-nan-mao-feng", "high"),
    "maofeng": ("yun-nan-mao-feng", "high"),
    "junnan-jun-u": ("yun-wu-lu-cha", "medium"),
}


def _catalog_path() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / "data" / "teashop_catalog.json",
        here.parent / "data" / "teashop_catalog.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def parse_price_byn(price_text: str | None) -> float | None:
    """Normalize teashop price text like '29,50 р.' to float BYN."""
    if not price_text:
        return None
    s = price_text
    s = re.sub(r"^[^\d]*", "", s)
    s = s.replace("р.", "").replace("р", "").replace(",", ".")
    s = s.replace("\xa0", "").replace(" ", "").strip()
    if "-" in s:
        s = s.split("-", 1)[0].strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


@lru_cache(maxsize=1)
def load_catalog() -> list[dict[str, Any]]:
    path = _catalog_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "items" in raw:
        items = raw["items"]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    return [item for item in items if isinstance(item, dict)]


def reload_catalog() -> list[dict[str, Any]]:
    load_catalog.cache_clear()
    return load_catalog()


def _product_url_key(product_url: str | None) -> str:
    if not product_url:
        return ""
    return str(product_url).rstrip("/").rsplit("/", 1)[-1].lower()


def match_product_to_slug(
    product_name: str,
    product_url: str | None = None,
) -> tuple[str | None, str]:
    """Map a shop product to a China-green tea.support slug."""
    url_key = _product_url_key(product_url)
    if url_key in URL_SLUG_OVERRIDES:
        return URL_SLUG_OVERRIDES[url_key]

    matches = resolve_query(product_name, limit=3)
    allowed = china_green_slugs()
    for row in matches:
        slug = str(row.get("slug") or "")
        score = int(row.get("score") or 0)
        if slug not in allowed:
            continue
        if score >= 80:
            return slug, "high"
        if score >= 50:
            return slug, "medium"
        if score >= 40:
            return slug, "low"
    return None, "none"


def remap_unmatched_items(items: list[dict[str, Any]]) -> int:
    """Fill matched_slug only where it is currently empty. Returns how many filled."""
    filled = 0
    for item in items:
        if item.get("matched_slug"):
            continue
        slug, confidence = match_product_to_slug(
            str(item.get("product_name") or ""),
            item.get("product_url"),
        )
        if not slug:
            continue
        item["matched_slug"] = slug
        item["mapping_confidence"] = confidence
        filled += 1
    return filled


def find_products(
    *,
    slug: str | None = None,
    query: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Find shop products by tea.support slug and/or free-text name."""
    items = load_catalog()
    if not items:
        return []

    slug_n = (slug or "").strip().lower() or None
    needle = fold_text(query or "")
    scored: list[tuple[int, dict[str, Any]]] = []

    for item in items:
        score = 0
        matched = (item.get("matched_slug") or "").strip().lower()
        if slug_n and matched == slug_n:
            score = max(score, 100)
        name = fold_text(str(item.get("product_name") or ""))
        if needle and name:
            if needle == name:
                score = max(score, 95)
            elif needle in name or name in needle:
                score = max(score, 75 if min(len(needle), len(name)) >= 4 else 45)
            else:
                tokens = set(needle.split())
                hay = set(name.split())
                overlap = tokens & hay
                if overlap and len(overlap) >= max(1, len(tokens) - 1):
                    score = max(score, 40 + 10 * len(overlap))
        if score:
            scored.append((score, item))

    scored.sort(
        key=lambda row: (
            -row[0],
            0 if row[1].get("availability") == "in_stock" else 1,
            -(int(row[1].get("harvest_year") or 0)),
            str(row[1].get("product_name") or ""),
        )
    )
    out: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for score, item in scored:
        url = str(item.get("product_url") or "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        out.append(
            {
                "product_name": item.get("product_name"),
                "matched_slug": item.get("matched_slug"),
                "price_from_byn": item.get("price_from_byn"),
                "currency": "BYN",
                "availability": item.get("availability"),
                "weight_g": item.get("weight_g"),
                "product_url": item.get("product_url"),
                "image_url": item.get("image_url"),
                "mapping_confidence": item.get("mapping_confidence"),
                "match_score": score,
                "last_checked": item.get("last_checked"),
            }
        )
        if len(out) >= limit:
            break
    return out


def catalog_meta() -> dict[str, Any]:
    path = _catalog_path()
    items = load_catalog()
    return {
        "path": str(path),
        "count": len(items),
        "exists": path.exists(),
        "generated_on": date.today().isoformat() if items else None,
    }
