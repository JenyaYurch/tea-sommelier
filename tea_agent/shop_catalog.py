"""Local teashop.by catalog for buy links / prices (structured, not RAG)."""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from tea_agent.slug_index import fold_text, known_tea_slugs, resolve_query

# Refresh cadence for the partner feed (TEA-19).
REFRESH_INTERVAL_DAYS = 14

# teashop.by URL slugs that the name matcher cannot disambiguate.
URL_SLUG_OVERRIDES: dict[str, tuple[str, str]] = {
    "yunnan-maofen-tou-chun": ("yun-nan-mao-feng", "high"),
    "maofeng": ("yun-nan-mao-feng", "high"),
    "junnan-jun-u": ("yun-wu-lu-cha", "medium"),
    "sjao-chzhun-hua-sjan": ("chi-gan-xiao-zhong", "high"),
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


def _parse_iso_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


@lru_cache(maxsize=1)
def load_catalog_document() -> dict[str, Any]:
    """Load the catalog JSON envelope (meta + items). Empty dict if missing."""
    path = _catalog_path()
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {"items": raw, "count": len(raw)}
    return {}


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
    doc = load_catalog_document()
    items = doc.get("items") if doc else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def reload_catalog() -> list[dict[str, Any]]:
    load_catalog_document.cache_clear()
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
    """Map a shop product to a tea.support slug from the local encyclopedia."""
    url_key = _product_url_key(product_url)
    if url_key in URL_SLUG_OVERRIDES:
        return URL_SLUG_OVERRIDES[url_key]

    matches = resolve_query(product_name, limit=3)
    allowed = known_tea_slugs()
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


def _shop_fold(value: str) -> str:
    """Fold shop text; treat Latin GABA and Cyrillic габа as the same token."""
    return fold_text(value).replace("gaba", "габа")


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
    needle = _shop_fold(query or "")
    scored: list[tuple[int, dict[str, Any]]] = []

    for item in items:
        score = 0
        matched = (item.get("matched_slug") or "").strip().lower()
        if slug_n and matched == slug_n:
            score = max(score, 100)
        name = _shop_fold(str(item.get("product_name") or ""))
        url_fold = _shop_fold(_product_url_key(item.get("product_url")).replace("-", " "))
        hay = " ".join(part for part in (name, url_fold) if part)
        if needle and hay:
            if needle == name:
                score = max(score, 95)
            elif needle in hay or (name and name in needle):
                score = max(score, 75 if min(len(needle), len(hay)) >= 4 else 45)
            else:
                tokens = set(needle.split())
                hay_tokens = set(hay.split())
                overlap = tokens & hay_tokens
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


def catalog_last_checked() -> str | None:
    """Return catalog-level last_checked (prefer envelope, else newest item)."""
    doc = load_catalog_document()
    for key in ("last_checked", "generated_on"):
        value = doc.get(key) if doc else None
        if isinstance(value, str) and value.strip():
            return value.strip()[:10]
    items = load_catalog()
    dates = [
        d
        for d in (_parse_iso_date(item.get("last_checked")) for item in items)
        if d is not None
    ]
    if not dates:
        return None
    return max(dates).isoformat()


def catalog_freshness(
    *,
    today: date | None = None,
    refresh_interval_days: int = REFRESH_INTERVAL_DAYS,
) -> dict[str, Any]:
    """Report how fresh the partner catalog is and whether a refresh is due."""
    now = today or date.today()
    last_checked = catalog_last_checked()
    checked_on = _parse_iso_date(last_checked)
    age_days = (now - checked_on).days if checked_on else None
    needs_refresh = age_days is None or age_days >= refresh_interval_days
    return {
        "last_checked": last_checked,
        "age_days": age_days,
        "refresh_interval_days": refresh_interval_days,
        "needs_refresh": needs_refresh,
        "as_of": now.isoformat(),
    }


def catalog_meta() -> dict[str, Any]:
    path = _catalog_path()
    items = load_catalog()
    doc = load_catalog_document()
    freshness = catalog_freshness()
    return {
        "path": str(path),
        "count": len(items),
        "exists": path.exists(),
        "source": doc.get("source") if doc else None,
        "generated_on": doc.get("generated_on") if doc else None,
        "last_checked": freshness["last_checked"],
        "age_days": freshness["age_days"],
        "needs_refresh": freshness["needs_refresh"],
        "refresh_interval_days": freshness["refresh_interval_days"],
    }
