"""Function tools: tea.support retrieval + local Chinese-tea slug resolve."""

from __future__ import annotations

import logging
from typing import Any

from google.adk.tools import ToolContext

from tea_agent.currency import annotate_product, currency_from_state
from tea_agent.fx_rates import current_fx_quote
from tea_agent.shop_catalog import find_products
from tea_agent.slug_index import known_tea_slugs, resolve_query
from tea_agent.tea_support import compact_tea_card, get_json

logger = logging.getLogger(__name__)


def resolve_tea(query: str) -> dict[str, Any]:
    """Map a tea name (Russian, English, pinyin, or Chinese) to tea.support slugs.

    Call this before get_tea_card, similar_teas, or compare_teas when the user
    used a common name such as Лунцзин or Би Ло Чунь.

    Args:
        query: Tea name or alias to resolve.

    Returns:
        Dict with status and ranked slug matches from the local Chinese-tea dictionary.
    """
    matches = resolve_query(query, limit=5)
    if not matches:
        return {
            "status": "not_found",
            "query": query,
            "matches": [],
            "hint": "Ask for another spelling, province, or search by vibe with search_teas.",
        }
    return {"status": "success", "query": query, "matches": matches}


def search_teas(vibe: str) -> dict[str, Any]:
    """Semantic search over tea.support by taste/mood ('soft, no bitterness, morning').

    Prefer English vibe phrases. Results are filtered to teas in the local
    slug dictionary (Chinese white/yellow/green/red/puer/oolong). Do not use
    this for shop prices.

    Args:
        vibe: Natural-language taste/mood description, preferably English.

    Returns:
        Dict with status and ranked matches (slug, name, score).
    """
    payload = get_json("/api/v2/semantic", {"q": vibe})
    if payload.get("status") == "error":
        return payload
    allowed = known_tea_slugs()
    matches: list[dict[str, Any]] = []
    for item in payload.get("matches") or []:
        slug = item.get("slug")
        if slug not in allowed:
            continue
        matches.append(
            {
                "slug": slug,
                "name": item.get("name"),
                "score": item.get("score"),
                "tea_type": item.get("tea_type"),
                "province": item.get("province"),
            }
        )
        if len(matches) >= 8:
            break
    return {
        "status": "success",
        "query": payload.get("query", vibe),
        "matches": matches,
        "source": "tea.support /semantic",
    }


def get_tea_card(slug: str) -> dict[str, Any]:
    """Fetch a compact tea card: taste, brewing, terroir, caffeine/price tier.

    price_tier is not a shop price. Never invent BYN/EUR/USD amounts.

    Args:
        slug: tea.support slug from resolve_tea or search_teas.

    Returns:
        Dict with status and a compact card, or an error.
    """
    payload = get_json(f"/api/v2/tea/{slug}")
    if payload.get("status") == "error":
        return payload
    if payload.get("error") or not payload.get("slug"):
        return {"status": "error", "error": "tea_not_found", "slug": slug}
    return {"status": "success", "card": compact_tea_card(payload)}


def similar_teas(slug: str) -> dict[str, Any]:
    """Find similar teas by sensory vector. Use after resolve_tea.

    Args:
        slug: tea.support slug to find neighbors for.

    Returns:
        Dict with status and similar teas from the local encyclopedia when possible.
    """
    payload = get_json(f"/api/v2/tea/{slug}/similar")
    if payload.get("status") == "error":
        return payload
    allowed = known_tea_slugs()
    similar: list[dict[str, Any]] = []
    for item in payload.get("similar") or []:
        other = item.get("slug")
        if other not in allowed:
            continue
        similar.append(
            {
                "slug": other,
                "name": item.get("name"),
                "score": item.get("score"),
                "tea_type": item.get("tea_type"),
            }
        )
        if len(similar) >= 6:
            break
    return {
        "status": "success",
        "slug": payload.get("slug", slug),
        "similar": similar,
        "source": "tea.support /similar",
    }


def compare_teas(slug_a: str, slug_b: str) -> dict[str, Any]:
    """Compare two teas side by side. Resolve both names to slugs first.

    Args:
        slug_a: First tea.support slug.
        slug_b: Second tea.support slug.

    Returns:
        Dict with status and comparison fields from tea.support.
    """
    slugs = f"{slug_a},{slug_b}"
    payload = get_json("/api/v2/compare", {"slugs": slugs})
    if payload.get("status") == "error":
        return payload
    payload["source"] = "tea.support /compare"
    return payload


def ask_sommelier(question: str) -> dict[str, Any]:
    """Fallback tea.support /ask. Use ONLY if other tools returned empty or error.

    Args:
        question: Question in English (Free tier).

    Returns:
        Dict with answer and cited slugs. Treat as last resort, not primary retrieval.
    """
    payload = get_json("/api/v2/ask", {"q": question, "lang": "en"})
    if payload.get("status") == "error":
        return payload
    return {
        "status": "success",
        "query": payload.get("query", question),
        "answer": payload.get("answer"),
        "sources": payload.get("sources") or [],
        "source": "tea.support /ask fallback",
        "warning": "Fallback only. Prefer resolve_tea, search_teas, get_tea_card, compare_teas.",
    }


def _has_byn_price(row: dict[str, Any]) -> bool:
    return row.get("price_from_byn") is not None


def _price_rows(
    rows: list[dict[str, Any]], currency: str, quote: Any
) -> list[dict[str, Any]]:
    priced: list[dict[str, Any]] = []
    for row in rows:
        try:
            priced.append(annotate_product(row, currency, quote))
        except Exception:
            logger.warning("price annotation failed", exc_info=True)
            priced.append(row)
    return priced


def _fx_fields(currency: str, quote: Any, *, needed: bool) -> dict[str, Any]:
    """Rate metadata for the model. ``fx`` is null when there is nothing to convert."""
    fields: dict[str, Any] = {"requested_currency": currency, "fx": None}
    if not needed or currency == "BYN":
        fields["fx_status"] = "not_needed"
        return fields
    if quote is None:
        fields["fx_status"] = "unavailable"
        return fields
    fields["fx_status"] = "ok"
    fields["fx"] = {
        "source": quote.source,
        "byn_per_eur": format(quote.byn_per_eur, "f"),
        "eur_date": quote.eur_date.isoformat(),
        "byn_per_usd": format(quote.byn_per_usd, "f"),
        "usd_date": quote.usd_date.isoformat(),
    }
    return fields


def find_in_shop(
    slug: str = "",
    query: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Find teashop.by products to buy: price, availability, product URL.

    Call after resolve_tea / search_teas when recommending teas or when the user
    asks price / where to buy. Prefer slug from resolve_tea; query is a fallback
    Russian product name. Do not invent prices or URLs — only return tool data.
    ``price_display`` is already converted. Copy that string; do not recompute.
    Never attach it to a b2btea shop.

    Args:
        slug: tea.support slug (preferred), e.g. biluochun.
        query: Free-text shop/product name if slug is unknown.

    Returns:
        Dict with matching shop products including product_url, price_from_byn,
        and price_display. price_display is null when price_from_byn is missing.
    """
    slug_n = (slug or "").strip()
    query_n = (query or "").strip()
    if not slug_n and not query_n:
        return {
            "status": "error",
            "error": "need_slug_or_query",
            "hint": "Pass slug from resolve_tea or a product name query.",
        }
    state = None if tool_context is None else getattr(tool_context, "state", None)
    currency = currency_from_state(state)
    products = find_products(slug=slug_n or None, query=query_n or None, limit=5)
    if products:
        quote = _quote_for(currency, products)
        return {
            "status": "success",
            "slug": slug_n or None,
            "query": query_n or None,
            "products": _price_rows(products, currency, quote),
            "source": "teashop.by local catalog",
            **_fx_fields(currency, quote, needed=any(_has_byn_price(row) for row in products)),
            "note": (
                "Prices are teashop.by only. Copy price_display verbatim; do not "
                "recompute. price_from_byn is the original BYN amount. "
                "requested_currency is EUR when the user has not chosen one. "
                "fx.source and the dates are the only rate; never invent another. "
                "If price_display is null, there is no number. "
                "If fx_status is unavailable, price_display is BYN only. "
                "Do not attach this price to a b2btea shop. "
                "Always give the product_url for in-stock items. "
                "Taste/terroir facts still come from tea.support tools."
            ),
        }
    unavailable = find_products(
        slug=slug_n or None,
        query=query_n or None,
        limit=5,
        in_stock_only=False,
    )
    if unavailable:
        kinds = {item.get("availability") for item in unavailable}
        status = "out_of_stock" if kinds == {"out_of_stock"} else "unavailable"
        quote = _quote_for(currency, unavailable)
        return {
            "status": status,
            "slug": slug_n or None,
            "query": query_n or None,
            "products": [],
            "unavailable": _price_rows(unavailable, currency, quote),
            **_fx_fields(
                currency, quote, needed=any(_has_byn_price(row) for row in unavailable)
            ),
            "hint": (
                "Catalog matches are not in stock. Do not offer a buy link. "
                "If you mention a price, copy price_display and do not invent one. "
                "Say that teashop.by currently has no in-stock listing for this tea."
            ),
            "source": "teashop.by local catalog",
        }
    return {
        "status": "not_found",
        "slug": slug_n or None,
        "query": query_n or None,
        "products": [],
        "requested_currency": currency,
        "fx_status": "not_needed",
        "fx": None,
        "hint": (
            "No teashop.by match in the local catalog. "
            "Recommend the tea by taste/brewing without inventing a price."
        ),
        "source": "teashop.by local catalog",
    }


def _quote_for(currency: str, rows: list[dict[str, Any]]) -> Any:
    if currency == "BYN" or not any(_has_byn_price(row) for row in rows):
        return None
    try:
        return current_fx_quote()
    except Exception:
        logger.warning("fx lookup failed", exc_info=True)
        return None
