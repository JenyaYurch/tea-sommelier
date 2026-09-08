"""HTTP client for tea.support (api.thetea.app). Free tier, English, no key."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = os.getenv("TEA_SUPPORT_BASE_URL", "https://api.thetea.app").rstrip("/")
TIMEOUT_SEC = 25
_USER_AGENT = "tea-sommelier/0.1 (TeaBot; +https://tea.support)"


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET JSON from tea.support. Returns {status, ...} on HTTP/network errors."""
    url = f"{BASE_URL}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body)
            if not isinstance(data, dict):
                return {"status": "error", "error": "unexpected_payload", "url": url}
            data.setdefault("status", "success")
            return data
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return {
            "status": "error",
            "error": f"http_{exc.code}",
            "detail": detail,
            "url": url,
        }
    except urllib.error.URLError as exc:
        return {"status": "error", "error": "network", "detail": str(exc.reason), "url": url}
    except TimeoutError:
        return {"status": "error", "error": "timeout", "url": url}
    except json.JSONDecodeError:
        return {"status": "error", "error": "invalid_json", "url": url}


def _section_value(card: dict[str, Any], section: str, field: str) -> str | None:
    sections = card.get("sections") or {}
    block = sections.get(section) or {}
    item = block.get(field) or {}
    value = item.get("value")
    return value if isinstance(value, str) and value.strip() else None


def compact_tea_card(card: dict[str, Any]) -> dict[str, Any]:
    """Keep sommelier-useful fields; drop health claims and 72-language name maps."""
    names = card.get("names") or {}
    meta = card.get("meta") or {}
    enrichment = card.get("enrichment")
    compact_enrichment = None
    if isinstance(enrichment, dict):
        compact_enrichment = {
            key: enrichment.get(key)
            for key in (
                "one_liner",
                "summary",
                "tasting_note",
                "food_pairing",
                "caffeine",
                "difficulty",
                "price_tier",
            )
            if enrichment.get(key) is not None
        }
    return {
        "source": "tea.support",
        "slug": card.get("slug"),
        "name": card.get("name"),
        "name_ru": names.get("ru"),
        "name_zh": names.get("zh") or names.get("zh-tw"),
        "tea_type": meta.get("tea_type") or card.get("tea_type"),
        "origin_country": meta.get("origin_country"),
        "category_code": meta.get("category_code"),
        "province": meta.get("province"),
        "taste": _section_value(card, "organoleptic", "taste"),
        "dry_leaf_aroma": _section_value(card, "organoleptic", "dry_leaf_aroma"),
        "liquor_aroma": _section_value(card, "organoleptic", "liquor_aroma"),
        "liquor_color": _section_value(card, "organoleptic", "liquor_color"),
        "origin": _section_value(card, "classification_origin", "origin"),
        "type": _section_value(card, "classification_origin", "type"),
        "water_temp": _section_value(card, "brewing", "water_temp"),
        "tea_amount": _section_value(card, "brewing", "tea_amount"),
        "teaware": _section_value(card, "brewing", "teaware"),
        "recipe": card.get("recipe") or [],
        "harvest": card.get("harvest") or [],
        "enrichment": compact_enrichment,
        "price_tier_note": (
            "enrichment.price_tier is a relative band from tea.support, "
            "not a shop price in BYN/EUR/USD"
        ),
    }
