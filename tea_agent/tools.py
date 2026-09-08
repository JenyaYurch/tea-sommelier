"""Function tools: tea.support retrieval + local Chinese-green slug resolve."""

from __future__ import annotations

from typing import Any

from tea_agent.slug_index import china_green_slugs, resolve_query
from tea_agent.tea_support import compact_tea_card, get_json


def resolve_tea(query: str) -> dict[str, Any]:
    """Map a tea name (Russian, English, pinyin, or Chinese) to tea.support slugs.

    Call this before get_tea_card, similar_teas, or compare_teas when the user
    used a common name such as Лунцзин or Би Ло Чунь.

    Args:
        query: Tea name or alias to resolve.

    Returns:
        Dict with status and ranked slug matches from the local China-green dictionary.
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

    Prefer English vibe phrases. Results are filtered to Chinese green teas
    from the local slug dictionary. Do not use this for shop prices.

    Args:
        vibe: Natural-language taste/mood description, preferably English.

    Returns:
        Dict with status and ranked matches (slug, name, score).
    """
    payload = get_json("/api/v2/semantic", {"q": vibe})
    if payload.get("status") == "error":
        return payload
    allowed = china_green_slugs()
    matches: list[dict[str, Any]] = []
    for item in payload.get("matches") or []:
        slug = item.get("slug")
        if slug not in allowed:
            continue
        if item.get("tea_type") and item.get("tea_type") != "green":
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
        Dict with status and similar Chinese green teas when possible.
    """
    payload = get_json(f"/api/v2/tea/{slug}/similar")
    if payload.get("status") == "error":
        return payload
    allowed = china_green_slugs()
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
