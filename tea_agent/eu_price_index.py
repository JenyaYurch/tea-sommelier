"""Saved PL/DE Chinese tea prices (TEA-51).

The file is ``data/eu_green_price_index.json``, copied into the tea-agent
image the same way as ``data/teashop_catalog.json``. ``find_best_price`` reads
this module and does not call shops during a chat.

Offer fields: ``tea_id``, ``category``, ``title``, ``shop_id``, ``shop``,
``url``, ``price``, ``currency``, ``pack_size_g``, ``pack_size_unclear``,
``price_per_100g``, ``in_stock``, ``fetched_at``. ``category`` is green, white,
yellow, oolong, black, puerh, dark, or scented. Skip rows with
``pack_size_unclear`` or ``in_stock`` false. ``price_per_100g`` is null when
the pack size is unclear.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

# Daily snapshot. Same freshness shape as the teashop.by catalog.
REFRESH_INTERVAL_DAYS = 1
INDEX_FILENAME = "eu_green_price_index.json"


def index_path() -> Path:
    here = Path(__file__).resolve()
    candidates = (
        here.parent.parent / "data" / INDEX_FILENAME,
        here.parent / "data" / INDEX_FILENAME,
    )
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
def load_index_document() -> dict[str, Any]:
    """Load the index envelope. Empty dict when the file is missing."""
    path = index_path()
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return raw
    return {}


@lru_cache(maxsize=1)
def load_offers() -> list[dict[str, Any]]:
    """Flat offer list. This is what a price tool should read."""
    offers = load_index_document().get("offers")
    if not isinstance(offers, list):
        return []
    return [offer for offer in offers if isinstance(offer, dict)]


def reload_index() -> list[dict[str, Any]]:
    load_index_document.cache_clear()
    load_offers.cache_clear()
    return load_offers()


def index_last_checked() -> str | None:
    doc = load_index_document()
    for key in ("last_checked", "generated_on"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:10]
    return None


def index_freshness(
    *,
    today: date | None = None,
    refresh_interval_days: int = REFRESH_INTERVAL_DAYS,
) -> dict[str, Any]:
    """Report whether the daily snapshot is due for a refresh."""
    now = today or date.today()
    last_checked = index_last_checked()
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


def index_meta() -> dict[str, Any]:
    path = index_path()
    doc = load_index_document()
    offers = load_offers()
    freshness = index_freshness()
    shops = doc.get("shops") if isinstance(doc.get("shops"), list) else []
    return {
        "path": str(path),
        "exists": path.exists(),
        "count": len(offers),
        "shops": len(shops),
        "generated_on": doc.get("generated_on"),
        "fetched_at": doc.get("fetched_at"),
        "last_checked": freshness["last_checked"],
        "age_days": freshness["age_days"],
        "needs_refresh": freshness["needs_refresh"],
        "refresh_interval_days": freshness["refresh_interval_days"],
    }
