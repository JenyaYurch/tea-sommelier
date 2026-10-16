# ruff: noqa: RUF001
"""Cheapest in-stock PL/DE offers from the saved price index.

``find_best_price`` reads ``data/eu_green_price_index.json`` and does not
call shops. Prices are converted with the same session currency and the same
rate quote as ``/currency`` (EUR when the session has not chosen one).
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from tea_agent.currency import (
    SHOP_PRICE_CURRENCIES,
    convert_money,
    currency_from_state,
)
from tea_agent.eu_price_index import index_last_checked, load_offers
from tea_agent.eu_shops.classify import match_chinese_tea
from tea_agent.fx_rates import current_fx_quote
from tea_agent.slug_index import fold_text, load_teas, resolve_query

logger = logging.getLogger(__name__)

TOP_N = 3
SOURCE = "data/eu_green_price_index.json"
_YEAR = re.compile(r"(?:19|20)\d{2}")
_MIN_ID_KEY = 8

# Whole-query shelf names. ``pu-erh`` folds to ``pu erh``.
_BROWSE_INPUT: tuple[tuple[str, str], ...] = (
    ("green", "green"),
    ("зелёный", "green"),
    ("зелёная", "green"),
    ("зелёное", "green"),
    ("zielona", "green"),
    ("zielony", "green"),
    ("zielone", "green"),
    ("grüner tee", "green"),
    ("oolong", "oolong"),
    ("oolongs", "oolong"),
    ("wulong", "oolong"),
    ("улун", "oolong"),
    ("улуны", "oolong"),
    ("black", "black"),
    ("red", "black"),
    ("hong cha", "black"),
    ("hongcha", "black"),
    ("красный", "black"),
    ("красная", "black"),
    ("красное", "black"),
    ("czerwona", "black"),
    ("czerwony", "black"),
    ("czarna", "black"),
    ("czarny", "black"),
    ("white", "white"),
    ("белый", "white"),
    ("белая", "white"),
    ("белое", "white"),
    ("biała", "white"),
    ("biały", "white"),
    ("yellow", "yellow"),
    ("жёлтый", "yellow"),
    ("жёлтая", "yellow"),
    ("жёлтое", "yellow"),
    ("pu-erh", "puerh"),
    ("puerh", "puerh"),
    ("puer", "puerh"),
    ("пуэр", "puerh"),
    ("пуер", "puerh"),
    ("scented", "scented"),
    ("ароматизированный", "scented"),
    ("ароматизированная", "scented"),
)
_CATEGORY_INPUT: tuple[tuple[str, str], ...] = (
    ("green", "green"),
    ("oolong", "oolong"),
    ("wulong", "oolong"),
    ("black", "black"),
    ("red", "black"),
    ("white", "white"),
    ("yellow", "yellow"),
    ("scented", "scented"),
    ("pu-erh", "puerh"),
    ("puerh", "puerh"),
    ("puer", "puerh"),
)
# Tea words that are not a cultivar. ``cha`` stays, so ``hong cha`` can browse.
_TEA_STOP = frozenset(
    {
        "tea",
        "teas",
        "herbata",
        "herbaty",
        "herbate",
        "tee",
        "tees",
        "the",
        "and",
        "from",
        "with",
        "von",
        "aus",
        "und",
        "mit",
        "fur",
        "chinese",
        "china",
        "chinski",
        "chinska",
        "chinesisch",
        "chinesische",
        "chinesischer",
        "чай",
        "чая",
        "чаю",
        "чаем",
    }
)
# A lone fragment must not keyword-match half the index.
_WEAK_TOKENS = frozenset(
    {"hong", "bai", "hei", "red", "jin", "mei", "cha", "ya", "lu", "shan", "long"}
)
# Names the slug dictionary does not carry, matched as whole tokens.
_ALIASES: tuple[tuple[str, str], ...] = (
    ("те гуань инь", "tie-guanyin"),
    ("тегуаньинь", "tie-guanyin"),
    ("железный бодхисатва", "tie-guanyin"),
    ("дай хун пао", "da-hong-pao"),
    ("да хун пао", "da-hong-pao"),
    ("дайхунпао", "da-hong-pao"),
    ("большой красный халат", "da-hong-pao"),
    ("красный халат", "da-hong-pao"),
    ("шуй сянь", "shui-xian"),
    ("шуйсянь", "shui-xian"),
    ("вэньшань баочжун", "wenshan-baozhong-cha"),
    ("вэнь шань бао чжун", "wenshan-baozhong-cha"),
    ("баочжун", "wenshan-baozhong-cha"),
    ("бао чжун", "wenshan-baozhong-cha"),
    ("алишань", "alishan-wulong"),
    ("али шань", "alishan-wulong"),
    ("дун фан мэй жэнь", "dongfang-meiren"),
    ("восточная красавица", "dongfang-meiren"),
    ("жоу гуй", "rou-gui"),
    ("жу гуй", "rou-gui"),
    ("молочный улун", "jin-xuan"),
    ("milk oolong", "jin-xuan"),
    ("цзинь сюань", "jin-xuan"),
    ("цзиньсюань", "jin-xuan"),
    ("лао бань чжан", "laobanzhang"),
    ("лаобаньчжан", "laobanzhang"),
    ("бань чжан", "ban-zhang"),
    ("баньчжан", "ban-zhang"),
)


def _public_category(value: str | None) -> str | None:
    if not value:
        return None
    if value == "puerh":
        return "pu-erh"
    return value


@lru_cache(maxsize=1)
def _browse_table() -> dict[str, str]:
    return {fold_text(phrase): category for phrase, category in _BROWSE_INPUT}


@lru_cache(maxsize=1)
def _category_table() -> dict[str, str]:
    return {fold_text(phrase): category for phrase, category in _CATEGORY_INPUT}


@lru_cache(maxsize=1)
def _alias_table() -> tuple[tuple[tuple[str, ...], str], ...]:
    rows: list[tuple[tuple[str, ...], str]] = []
    for phrase, tea_id in _ALIASES:
        tokens = tuple(fold_text(phrase).split())
        if tokens:
            rows.append((tokens, tea_id))
    return tuple(rows)


@lru_cache(maxsize=1)
def _dictionary_words() -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for tea in load_teas():
        slug = str(tea.get("slug") or "")
        parts = [
            slug.replace("-", " "),
            str(tea.get("name_en") or ""),
            str(tea.get("name_ru") or ""),
            str(tea.get("pinyin") or ""),
            *[str(alias) for alias in tea.get("aliases") or []],
        ]
        words: list[str] = []
        for part in parts:
            words.extend(fold_text(part).split())
        table[slug] = words
    return table


def parse_category(value: str | None) -> tuple[str | None, str | None]:
    """Return ``(internal category, error)``. A blank value is no filter."""
    raw = " ".join(str(value or "").split())
    if not raw:
        return None, None
    found = _category_table().get(fold_text(raw))
    if found is None:
        return None, "invalid_category"
    return found, None


def browse_category(query: str) -> str | None:
    """The shelf when the whole query is a category, not a cultivar."""
    folded = fold_text(query)
    table = _browse_table()
    if folded in table:
        return table[folded]
    tokens = [token for token in folded.split() if token not in _TEA_STOP]
    text = " ".join(tokens)
    if text in table:
        return table[text]
    return None


def _content_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for token in fold_text(query).split():
        if token in _TEA_STOP or _YEAR.fullmatch(token):
            continue
        if len(token) < 3 and not token.isdigit():
            continue
        tokens.append(token)
    return tokens


def _keyword_ok(tokens: list[str]) -> bool:
    return any(token not in _WEAK_TOKENS and len(token) >= 4 for token in tokens)


def _token_aligns(token: str, words: list[str]) -> bool:
    if not token:
        return False
    stems = [token]
    if len(token) > 5 and token.endswith("e"):
        stems.append(token[:-1])
    for word in words:
        if not word:
            continue
        for stem in stems:
            if stem == word:
                return True
            if len(stem) < 5 or len(word) < 5:
                continue
            if word.startswith(stem) and 0 < len(word) - len(stem) <= 6:
                return True
            if stem.startswith(word) and 0 < len(stem) - len(word) <= 2:
                return True
    return False


def _tokens_align(tokens: list[str], words: list[str]) -> bool:
    return bool(tokens) and all(_token_aligns(token, words) for token in tokens)


def _close_word(token: str, words: list[str]) -> bool:
    if len(token) < 5:
        return False
    for word in words:
        if not word or word[0] != token[0]:
            continue
        if abs(len(word) - len(token)) > 1:
            continue
        if SequenceMatcher(None, token, word).ratio() >= 0.86:
            return True
    return False


def _fuzzy_hit(tokens: list[str], words: list[str]) -> bool:
    """One typo against this offer. Exact matches are not a fuzzy hit."""
    if not tokens:
        return False
    typos = 0
    for token in tokens:
        if _token_aligns(token, words):
            continue
        if not _close_word(token, words):
            return False
        typos += 1
        if typos > 1:
            return False
    return typos == 1


def _phrase_in(words: list[str], phrase: tuple[str, ...]) -> bool:
    size = len(phrase)
    if size == 0 or len(words) < size:
        return False
    phrase_list = list(phrase)
    return any(words[index : index + size] == phrase_list for index in range(len(words) - size + 1))


def _alias_ids(query: str) -> set[str]:
    words = fold_text(query).split()
    hits: list[tuple[int, str]] = []
    for phrase, tea_id in _alias_table():
        if _phrase_in(words, phrase):
            hits.append((len(phrase), tea_id))
    if not hits:
        return set()
    best = max(length for length, _tea_id in hits)
    return {tea_id for length, tea_id in hits if length == best}


def _variants(query: str) -> list[str]:
    folded = fold_text(query)
    variants = [query]
    joined = folded.replace(" ", "")
    if joined and joined != folded and len(joined) >= 4:
        variants.append(joined)
    return variants


def _resolve_ok(variant: str, match: dict[str, Any]) -> bool:
    score = int(match.get("score") or 0)
    # 100 is an exact alias. Above 80 means a long alias sits inside the query.
    # A flat 80 is only "the query is a substring of some longer name".
    if score > 80:
        return True
    if score < 80:
        return False
    tokens = [token for token in fold_text(variant).split() if len(token) >= 3]
    if not tokens:
        return False
    words = _dictionary_words().get(str(match.get("slug") or ""), [])
    return _tokens_align(tokens, words)


def _identity_ids(query: str) -> set[str]:
    ids = set(_alias_ids(query))
    classified = match_chinese_tea(query)
    if classified is not None:
        ids.add(classified.tea_id)
    for variant in _variants(query):
        for match in resolve_query(variant, limit=15):
            if _resolve_ok(variant, match):
                ids.add(str(match["slug"]))
    return ids


def _tea_id_hit(offer_id: str, wanted: set[str]) -> bool:
    if not offer_id:
        return False
    if offer_id in wanted:
        return True
    folded = offer_id.replace("-", "")
    for tea_id in wanted:
        key = tea_id.replace("-", "")
        if len(key) >= _MIN_ID_KEY and key in folded:
            return True
    return False


def _offer_words(offer: dict[str, Any]) -> list[str]:
    title = str(offer.get("title") or "")
    tea_id = str(offer.get("tea_id") or "").replace("-", " ")
    return fold_text(title).split() + fold_text(tea_id).split()


def _dedupe(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    chosen: list[dict[str, Any]] = []
    for offer in offers:
        key = (offer.get("url"), offer.get("tea_id"), offer.get("pack_size_g"))
        if key in seen:
            continue
        seen.add(key)
        chosen.append(offer)
    return chosen


def _keyword_offers(offers: list[dict[str, Any]], tokens: list[str]) -> list[dict[str, Any]]:
    if not _keyword_ok(tokens):
        return []
    return [offer for offer in offers if _tokens_align(tokens, _offer_words(offer))]


def _select(offers: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Name matches, including out-of-stock and unclear packs."""
    if browse_category(query):
        return list(offers)
    ids = _identity_ids(query)
    tokens = _content_tokens(query)
    keyword = _keyword_offers(offers, tokens)
    if ids:
        id_pool = [offer for offer in offers if _tea_id_hit(str(offer.get("tea_id") or ""), ids)]
        if id_pool:
            blob: list[str] = []
            words = _dictionary_words()
            for tea_id in ids:
                blob.extend(words.get(tea_id, []))
            extra = [token for token in tokens if not _token_aligns(token, blob)]
            if extra:
                narrowed = [offer for offer in id_pool if _tokens_align(extra, _offer_words(offer))]
                if narrowed:
                    return _dedupe([*narrowed, *keyword])
            else:
                return _dedupe([*id_pool, *keyword])
            return id_pool
    if keyword:
        return keyword
    if tokens:
        fuzzy = [offer for offer in offers if _fuzzy_hit(tokens, _offer_words(offer))]
        if fuzzy:
            return fuzzy
    return []


def _grams(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        grams = value
    elif isinstance(value, float) and value.is_integer():
        grams = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        grams = int(value.strip())
    else:
        return None
    if grams <= 0 or grams > 2000:
        return None
    return grams


def _positive(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        try:
            return float(value.replace(",", ".")) > 0
        except ValueError:
            return False
    return False


def _currency_code(value: Any) -> str | None:
    token = " ".join(str(value or "").split()).upper()
    if token in SHOP_PRICE_CURRENCIES:
        return token
    return None


def _sellable(offer: dict[str, Any]) -> bool:
    if offer.get("in_stock") is not True:
        return False
    if offer.get("pack_size_unclear") is True:
        return False
    if _grams(offer.get("pack_size_g")) is None:
        return False
    if not _positive(offer.get("price")) or not _positive(offer.get("price_per_100g")):
        return False
    if _currency_code(offer.get("currency")) is None:
        return False
    url = str(offer.get("url") or "").strip()
    title = str(offer.get("title") or "").strip()
    return url.startswith("http") and bool(title)


def _needs_fx(offers: list[dict[str, Any]], currency: str) -> bool:
    for offer in offers:
        code = _currency_code(offer.get("currency"))
        if code and code != currency:
            return True
    return False


def _load_quote(needed: bool) -> Any:
    if not needed:
        return None
    try:
        return current_fx_quote()
    except Exception:
        logger.warning("fx lookup failed for find_best_price", exc_info=True)
        return None


def _fx_payload(quote: Any, status: str) -> dict[str, Any] | None:
    if quote is None or status not in {"ok", "partial"}:
        return None
    pln_date = getattr(quote, "pln_date", None)
    return {
        "source": quote.source,
        "eur_date": quote.eur_date.isoformat(),
        "usd_date": quote.usd_date.isoformat(),
        "pln_date": pln_date.isoformat() if pln_date is not None else None,
    }


def _public_offer(offer: dict[str, Any], price: Any, per: Any, currency: str) -> dict[str, Any]:
    grams = _grams(offer.get("pack_size_g"))
    return {
        "shop": str(offer.get("shop") or offer.get("shop_id") or "Shop").strip() or "Shop",
        "product_name": str(offer.get("title") or "").strip(),
        "pack_size": f"{grams} g",
        "pack_size_g": grams,
        "price": price.text,
        "price_per_100g": per.text,
        "url": str(offer.get("url") or "").strip(),
        "currency": currency,
        "original_price": offer.get("price"),
        "original_currency": _currency_code(offer.get("currency")),
        "original_price_per_100g": offer.get("price_per_100g"),
        "category": _public_category(str(offer.get("category") or "")),
        "tea_id": offer.get("tea_id"),
        "in_stock": True,
    }


def _rank(
    sellable: list[dict[str, Any]], currency: str, quote: Any
) -> tuple[list[dict[str, Any]], int, bool]:
    ranked: list[tuple[Any, ...]] = []
    skipped = 0
    used = False
    for offer in sellable:
        price = convert_money(offer.get("price"), str(offer.get("currency") or ""), currency, quote)
        per = convert_money(
            offer.get("price_per_100g"), str(offer.get("currency") or ""), currency, quote
        )
        if price.amount is None or per.amount is None or not price.text or not per.text:
            skipped += 1
            continue
        if price.converted or per.converted:
            used = True
        ranked.append(
            (
                per.amount,
                price.amount,
                str(offer.get("shop") or "").casefold(),
                str(offer.get("url") or ""),
                offer,
                price,
                per,
            )
        )
    ranked.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _per, _price, _shop, url, offer, price, per in ranked:
        if url in seen:
            continue
        seen.add(url)
        chosen.append(_public_offer(offer, price, per, currency))
        if len(chosen) >= TOP_N:
            break
    return chosen, skipped, used


def _envelope(
    *,
    status: str,
    query: str,
    category: str | None,
    currency: str,
    offers: list[dict[str, Any]],
    hint: str,
    fx_status: str,
    fx: dict[str, Any] | None,
    matched: int = 0,
    skipped_fx: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "query": query,
        "category": _public_category(category) if category else None,
        "requested_currency": currency,
        "offers": offers,
        "matched": matched,
        "skipped_fx": skipped_fx,
        "hint": hint,
        "source": SOURCE,
        "index_date": index_last_checked(),
        "fx_status": fx_status,
        "fx": fx,
    }
    if error:
        payload["error"] = error
    return payload


def search_best_prices(
    query: str | None,
    category: str | None = None,
    state: Any = None,
) -> dict[str, Any]:
    """Rank saved in-stock offers. ``state`` is the ADK session, if any."""
    query_text = " ".join(str(query or "").split())
    currency = currency_from_state(state)
    if not query_text:
        return _envelope(
            status="error",
            query=query_text,
            category=None,
            currency=currency,
            offers=[],
            hint="Pass a tea name or keywords.",
            fx_status="not_needed",
            fx=None,
            error="need_query",
        )
    explicit, error = parse_category(category)
    if error:
        return _envelope(
            status="error",
            query=query_text,
            category=None,
            currency=currency,
            offers=[],
            hint=(
                "category must be green, oolong, black, white, pu-erh, yellow, "
                "or scented."
            ),
            fx_status="not_needed",
            fx=None,
            error="invalid_category",
        )
    browse = browse_category(query_text)
    if browse and explicit and browse != explicit:
        return _envelope(
            status="error",
            query=query_text,
            category=explicit,
            currency=currency,
            offers=[],
            hint=(
                f"Query matches shelf {_public_category(browse)} but category is "
                f"{_public_category(explicit)}. Leave category empty or use the same shelf."
            ),
            fx_status="not_needed",
            fx=None,
            error="category_mismatch",
        )
    shelf = explicit or browse
    catalog = load_offers()
    if not catalog:
        return _envelope(
            status="not_found",
            query=query_text,
            category=shelf,
            currency=currency,
            offers=[],
            hint=(
                "The saved PL/DE price index has no offers. Tell the user. "
                "Do not invent a shop, price, or link."
            ),
            fx_status="not_needed",
            fx=None,
        )
    pool = catalog
    if shelf:
        pool = [offer for offer in catalog if offer.get("category") == shelf]
    matched = _select(pool, query_text)
    sellable = [offer for offer in matched if _sellable(offer)]
    if not sellable:
        if matched:
            hint = (
                "The saved index has this tea, but no in-stock offer with a clear "
                "pack size to compare. Tell the user. Do not invent a price or link."
            )
        else:
            hint = (
                "No saved PL/DE offer matches this query. Tell the user. "
                "Do not invent a shop, price, or link."
            )
        return _envelope(
            status="not_found",
            query=query_text,
            category=shelf,
            currency=currency,
            offers=[],
            hint=hint,
            fx_status="not_needed",
            fx=None,
            matched=len(matched),
        )
    quote = _load_quote(_needs_fx(sellable, currency))
    offers, skipped, used = _rank(sellable, currency, quote)
    if not offers:
        return _envelope(
            status="fx_unavailable",
            query=query_text,
            category=shelf,
            currency=currency,
            offers=[],
            hint=(
                "The exchange rate is unavailable, so these offers cannot be ranked "
                "in the user's currency. Say so. Do not compare a raw PLN amount "
                "with an EUR amount."
            ),
            fx_status="unavailable",
            fx=None,
            matched=len(sellable),
            skipped_fx=skipped,
        )
    if skipped:
        fx_status = "partial"
        hint = (
            "Sorted by price per 100 g in the session currency. "
            f"{skipped} matching offers were omitted because their currency could "
            "not be converted. Do not claim this list is the full market. "
            "Do not recompute. The reply code inserts the block; do not type "
            "these prices or links yourself."
        )
    elif used:
        fx_status = "ok"
        hint = (
            "Sorted by price per 100 g in the session currency. Copy nothing into "
            "the reply: the code inserts «### В Польше и Германии». Do not recompute "
            "and do not fetch shops."
        )
    else:
        fx_status = "not_needed"
        hint = (
            "Sorted by price per 100 g. Amounts are already in the session currency. "
            "The reply code inserts «### В Польше и Германии». Do not type prices "
            "or links yourself and do not fetch shops."
        )
    return _envelope(
        status="success",
        query=query_text,
        category=shelf,
        currency=currency,
        offers=offers,
        hint=hint,
        fx_status=fx_status,
        fx=_fx_payload(quote, fx_status),
        matched=len(sellable),
        skipped_fx=skipped,
    )
