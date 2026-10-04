# ruff: noqa: RUF001, RUF002
"""Canonical next-step chips after recommendations.

Playground shows this block as text. Telegram (TEA-11) parses it into InlineKeyboard.

Canonical block at the end of a recommendation reply:

    ### Что дальше
    [мягче] [дешевле] [без горечи] [подарок] [подробнее] [магазины рядом]
    [Купить: <name>](<product_url from find_in_shop>)

«Купить» is a markdown link to a teashop.by URL from the local catalog — never invented.
«магазины рядом» is a separate chip. Shop card and website links come only from
find_local_shops and are rendered under «### Где рядом», not as «Купить».
A successful payload is also stored on the session (``local_shops_last``) so a
later turn can rebuild that same block when the tool does not run again.
«### На витрине» is the teashop.by price, built from price_display. It is not
the price of a b2btea shop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import BaseTool, ToolContext
from google.genai import types

from tea_agent.currency import annotate_product, currency_from_state
from tea_agent.fx_rates import current_fx_quote
from tea_agent.location import LOCAL_SHOPS_LAST_KEY, LOCAL_SHOPS_SAVED_KEY
from tea_agent.shop_catalog import find_products, is_buyable, load_catalog
from tea_agent.slug_index import fold_text, resolve_query

LOCAL_SHOPS_CHIP = "магазины рядом"
ACTION_LABELS: tuple[str, ...] = (
    "мягче",
    "дешевле",
    "без горечи",
    "подарок",
    "подробнее",
    LOCAL_SHOPS_CHIP,
)
HEADING = "### Что дальше"
LOCAL_SHOPS_HEADING = "### Где рядом"
VITRINE_PRICE_HEADING = "### На витрине"
VITRINE_PRICE_NOTE = "Витрина teashop.by. Это не цена магазина рядом."
SHOP_HITS_KEY = "temp:shop_hits_turn"
LOCAL_SHOPS_KEY = "temp:local_shops_turn"
_B2BTEA_HOSTS = frozenset({"b2btea.com", "www.b2btea.com"})
_MARKETPLACE_BITS = ("amazon.", "wildberries.", "ozon.", "aliexpress.")
_LOCAL_BLOCK = re.compile(r"(?is)\n*###\s*Где рядом\b.*?(?=\n###\s|\Z)")
_PRICE_BLOCK = re.compile(r"(?is)\n*###\s*На витрине\b.*?(?=\n###\s|\Z)")
_MODEL_PRICE = re.compile(
    # ``до 20 EUR`` is a budget. ``(?<![\d.,])`` stops ``20 EUR`` from
    # backtracking into ``0 EUR`` and leaving a stray digit.
    r"(?<!до\s)(?<![\d.,])"
    r"(?:~\s*)?\d+(?:[.,]\d+)?\s*(?:EUR|USD|BYN)\b"
    r"(?:\s*\([^)]{0,120}\))?",
    re.IGNORECASE,
)
_BLOCK_START = re.compile(
    r"(?is)\n*(?:###\s*Что дальше|<!--\s*tea_next_steps\s*-->)\s*"
)
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"https?://[^\s)>\]]+")
_NUMBERED = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+\S+")
_BUY_LABEL = re.compile(r"(?i)^купить(?:\s*[:—-]\s*|\s+)?(.*)$")
_REC_LINE = re.compile(
    r"(?m)^\s*(?:#{1,3}\s*)?(?:\d+[.)]\s+|[-*•]\s+)(.+)$"
)
_TEASHOP_HOSTS = frozenset({"teashop.by", "www.teashop.by"})


@dataclass(frozen=True)
class NextStep:
    kind: str  # "action" | "buy"
    label: str
    url: str | None = None


def normalize_product_url(url: str | None) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    raw = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    raw = re.sub(r"^http://", "https://", raw, count=1, flags=re.I)
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host == "teashop.by":
        host = "www.teashop.by"
        raw = parsed._replace(netloc=host).geturl().rstrip("/")
    return raw


@lru_cache(maxsize=1)
def _catalog_url_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, item in _catalog_items().items():
        original = str(item.get("product_url") or "").strip()
        if key and original:
            mapping[key] = original
    return mapping


@lru_cache(maxsize=1)
def _catalog_items() -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in load_catalog():
        original = str(item.get("product_url") or "").strip()
        key = normalize_product_url(original)
        if key:
            mapping[key] = item
    return mapping


def catalog_product_urls() -> set[str]:
    return set(_catalog_url_map().values()) | set(_catalog_url_map().keys())


def canonical_catalog_url(url: str | None) -> str | None:
    key = normalize_product_url(url)
    if not key:
        return None
    return _catalog_url_map().get(key)


def is_catalog_url(url: str | None) -> bool:
    return canonical_catalog_url(url) is not None


def is_teashop_url(url: str | None) -> bool:
    parsed = urlparse(normalize_product_url(url))
    return parsed.netloc.lower() in _TEASHOP_HOSTS


def strip_next_steps_block(text: str) -> str:
    match = _BLOCK_START.search(text)
    if not match:
        return text.rstrip()
    return text[: match.start()].rstrip()


def format_next_steps_block(products: list[dict[str, Any]] | None = None) -> str:
    lines = [HEADING, " ".join(f"[{label}]" for label in ACTION_LABELS)]
    buy_lines = _buy_markdown_lines(products or [])
    if buy_lines:
        lines.extend(buy_lines)
    else:
        lines.append("[купить]")
    return "\n".join(lines)


def parse_next_steps(text: str) -> list[NextStep]:
    match = _BLOCK_START.search(text)
    if not match:
        return []
    block = text[match.end() :]
    steps: list[NextStep] = []
    seen: set[tuple[str, str, str]] = set()

    for label, url in _MD_LINK.findall(block):
        buy = _BUY_LABEL.match(label.strip())
        if buy:
            name = (buy.group(1) or "").strip() or label.strip()
            key = ("buy", name, normalize_product_url(url))
            if key not in seen:
                seen.add(key)
                steps.append(
                    NextStep("buy", name, canonical_catalog_url(url) or normalize_product_url(url))
                )
        else:
            key = ("action", label.strip(), "")
            if key not in seen:
                seen.add(key)
                steps.append(NextStep("action", label.strip(), None))

    leftover = _MD_LINK.sub("", block)
    for label in re.findall(r"\[([^\]\n]+)\]", leftover):
        cleaned = label.strip()
        if not cleaned:
            continue
        kind = "buy" if _BUY_LABEL.match(cleaned) else "action"
        display = cleaned
        buy = _BUY_LABEL.match(cleaned)
        if buy and (buy.group(1) or "").strip():
            display = buy.group(1).strip()
        elif kind == "buy":
            display = "купить"
        key = (kind, display, "")
        if key in seen:
            continue
        seen.add(key)
        steps.append(NextStep(kind, display, None))
    return steps


def harvest_catalog_products(text: str) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _label, url in _MD_LINK.findall(text):
        canonical = canonical_catalog_url(url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        catalog_item = _catalog_items().get(normalize_product_url(canonical))
        if catalog_item:
            products.append({**catalog_item, "product_url": canonical})
    return products


def normalize_directory_url(url: str | None) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    raw = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    raw = re.sub(r"^http://", "https://", raw, count=1, flags=re.I)
    return raw


def _is_b2btea_url(url: str | None) -> bool:
    return urlparse(normalize_directory_url(url)).netloc.lower() in _B2BTEA_HOSTS


def _is_marketplace(host: str) -> bool:
    return any(bit in host for bit in _MARKETPLACE_BITS)


def remove_local_shops_section(text: str) -> str:
    return _LOCAL_BLOCK.sub("\n", text).strip()


def remove_vitrine_price_section(text: str) -> str:
    return _PRICE_BLOCK.sub("\n", text).strip()


def strip_model_shop_prices(text: str) -> str:
    """Drop model-written amounts so the code block is the only shop price.

    ``до 20 EUR`` is left alone (a budget, not a vitrine price). ``20 евро``
    does not match. A tilde or a decimal amount in EUR, USD, or BYN is removed.
    """
    cleaned = _MODEL_PRICE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"(?m)[ \t]*[—–-][ \t]*([.!?])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def format_vitrine_price_section(products: list[dict[str, Any]] | None) -> str:
    """Grounded teashop.by prices. Lines lead with ``price_display``."""
    lines: list[str] = []
    seen: set[str] = set()
    for item in products or []:
        if not isinstance(item, dict):
            continue
        text = item.get("price_display")
        if not isinstance(text, str) or not text.strip():
            continue
        name = str(item.get("product_name") or "чай").strip() or "чай"
        line = f"{text.strip()} — {name}"
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= 3:
            break
    if not lines:
        return ""
    return "\n".join([VITRINE_PRICE_HEADING, VITRINE_PRICE_NOTE, *lines])


def _has_price_display(item: dict[str, Any]) -> bool:
    text = item.get("price_display")
    return isinstance(text, str) and bool(text.strip())


def fill_price_displays(
    products: list[dict[str, Any]], currency: str | None
) -> list[dict[str, Any]]:
    """Attach ``price_display`` where the catalog has ``price_from_byn``.

    An existing ``price_display`` is kept, so the rate from ``find_in_shop``
    is not recomputed. A missing BYN amount stays a missing display.
    """
    code = currency_from_state({"currency": currency} if currency else None)
    needs_quote = code != "BYN" and any(
        isinstance(item, dict)
        and not _has_price_display(item)
        and item.get("price_from_byn") is not None
        for item in products
    )
    quote = None
    if needs_quote:
        try:
            quote = current_fx_quote()
        except Exception:
            quote = None
    filled: list[dict[str, Any]] = []
    for item in products:
        if not isinstance(item, dict):
            continue
        if item.get("price_from_byn") is None:
            filled.append(
                {
                    **item,
                    "price_display": None,
                    "display_currency": None,
                    "rate_source": None,
                    "rate_date": None,
                }
            )
            continue
        if _has_price_display(item):
            filled.append(item)
            continue
        filled.append(annotate_product(item, code, quote))
    return filled


def _split_local_shops_section(text: str) -> tuple[str, str]:
    match = _LOCAL_BLOCK.search(text)
    if not match:
        return text, ""
    outside = (text[: match.start()] + text[match.end() :]).strip()
    return outside, match.group(0)


def invented_buy_urls(text: str) -> list[str]:
    """teashop.by (or other shop) URLs that are not in the local catalog.

    b2btea card links and shop websites are allowed only inside the grounded
    «Где рядом» block. Marketplace hosts are never allowed.
    """
    outside, inside = _split_local_shops_section(text)
    found = _invented_in_chunk(outside, directory_section=False)
    found.extend(_invented_in_chunk(inside, directory_section=True))
    return found


def _invented_in_chunk(text: str, *, directory_section: bool) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    candidates = [url for _, url in _MD_LINK.findall(text)]
    candidates.extend(_BARE_URL.findall(text))
    for url in candidates:
        normalized = normalize_product_url(url)
        if not normalized or normalized in seen:
            continue
        if is_teashop_url(normalized) and not is_catalog_url(normalized):
            seen.add(normalized)
            found.append(normalized)
            continue
        host = urlparse(normalized).netloc.lower()
        if host and _is_marketplace(host):
            seen.add(normalized)
            found.append(normalized)
            continue
        if directory_section:
            continue
        if _is_b2btea_url(normalized):
            seen.add(normalized)
            found.append(normalized)
            continue
        if host and host not in _TEASHOP_HOSTS and _looks_like_shop_host(host):
            seen.add(normalized)
            found.append(normalized)
    return found


def looks_like_recommendations(text: str) -> bool:
    body = strip_next_steps_block(text)
    if len(_NUMBERED.findall(body)) >= 3:
        return True
    lowered = body.lower()
    return "рекоменд" in lowered and any(
        token in lowered for token in ("сорт", "чай", "лунцзин", "би ло")
    )


def should_attach_next_steps(
    text: str, products: list[dict[str, Any]] | None = None
) -> bool:
    if products:
        return True
    return looks_like_recommendations(text)


def extract_recommended_names(text: str) -> list[str]:
    """Tea names from a numbered/bulleted recommendation list, in order."""
    body = strip_next_steps_block(text)
    names: list[str] = []
    seen: set[str] = set()
    for match in _REC_LINE.finditer(body):
        name = _clean_recommended_name(match.group(1))
        key = fold_text(name)
        if not name or not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= 3:
            break
    return names


def products_for_reply(
    text: str, products: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Catalog products for the teas named in this reply (max 3, one SKU each)."""
    pool = _merge_products(
        _allowed_products(products or []),
        harvest_catalog_products(strip_next_steps_block(text)),
    )
    names = extract_recommended_names(text)
    if names:
        selected = _select_products_for_names(names, pool)
        if selected:
            return selected
    return _unique_products_by_url(pool)[:3]


def format_local_shops_section(payload: dict[str, Any] | None) -> str:
    """Grounded shop block. Only URLs from the tool payload are included."""
    if not isinstance(payload, dict):
        return ""
    status = payload.get("status")
    shops = [
        shop
        for shop in (payload.get("shops") or [])
        if isinstance(shop, dict) and shop.get("url") and shop.get("website")
    ]
    if status == "success" and shops:
        label = str(payload.get("class_label_ru") or "этот").strip() or "этот"
        lines = [
            LOCAL_SHOPS_HEADING,
            (
                f"Класс чая: {label}. Справочник показывает магазины с этим классом, "
                "не конкретный сорт. Цен на карточках нет."
            ),
        ]
        for shop in shops[:3]:
            where = "тот же город" if shop.get("same_city") else "онлайн в стране"
            city = str(shop.get("city") or "").strip()
            name = str(shop.get("name") or "Магазин").strip() or "Магазин"
            place = f"{city}, {where}" if city else where
            lines.append(
                f"{name} — {place}. "
                f"[Карточка]({shop['url']}) [Сайт]({shop['website']})"
            )
        return "\n".join(lines)
    if status == "not_found":
        return (
            f"{LOCAL_SHOPS_HEADING}\n"
            "Рядом магазинов с этим классом чая не нашлось. Ссылок нет."
        )
    if status == "error":
        return (
            f"{LOCAL_SHOPS_HEADING}\n"
            "Справочник магазинов сейчас недоступен. "
            "Рекомендация чая от этого не отменяется."
        )
    return ""


def _local_shop_urls(payload: dict[str, Any] | None) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    urls: set[str] = set()
    for shop in payload.get("shops") or []:
        if not isinstance(shop, dict):
            continue
        for key in ("url", "website"):
            value = str(shop.get(key) or "").strip()
            if value:
                urls.add(value)
    return urls


def _strip_url_set(text: str, urls: set[str]) -> str:
    keys = {normalize_directory_url(url) for url in urls if url}
    if not keys:
        return text

    def _replace_md(match: re.Match[str]) -> str:
        if normalize_directory_url(match.group(2)) in keys:
            return match.group(1)
        return match.group(0)

    cleaned = _MD_LINK.sub(_replace_md, text)

    def _replace_bare(match: re.Match[str]) -> str:
        if normalize_directory_url(match.group(0)) in keys:
            return ""
        return match.group(0)

    return re.sub(r"https?://[^\s)>\]]+", _replace_bare, cleaned)


_SHOP_ASK = re.compile(r"магазин|ссылк|где\s+рядом|b2btea", re.IGNORECASE)
_CARD_SITE = re.compile(
    r"карточк|официальн|(?<![0-9a-zа-яё])сайт(?![0-9a-zа-яё])",
    re.IGNORECASE,
)
_SHOP_BULLET = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.*)$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _stored_shop_names(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    names: list[str] = []
    for shop in payload.get("shops") or []:
        if not isinstance(shop, dict):
            continue
        folded = fold_text(str(shop.get("name") or ""))
        if len(folded) >= 3 and folded not in names:
            names.append(folded)
    return names


def _shop_name_hits(text: str, names: list[str]) -> int:
    folded = fold_text(text)
    hits = 0
    for name in names:
        if re.search(
            rf"(?<![0-9a-zа-я]){re.escape(name)}(?![0-9a-zа-я])",
            folded,
        ):
            hits += 1
    return hits


def _is_shop_list_fragment(text: str, names: list[str]) -> bool:
    hits = _shop_name_hits(text, names)
    if hits >= 2:
        return True
    return hits >= 1 and _CARD_SITE.search(text) is not None


def _line_is_only_a_shop_name(line: str, names: list[str]) -> bool:
    match = _SHOP_BULLET.match(line)
    raw = match.group(1) if match else line
    return fold_text(raw) in names


def _strip_free_text_shop_list(text: str, payload: dict[str, Any] | None) -> str:
    """Drop the model's own shop list so the code block is the only one.

    A line or sentence goes when it names two saved shops, or one saved shop
    together with card/site wording («карточка магазина», «официальный сайт»).
    A line that is only a shop name goes too. One other sentence may stay.
    """
    names = _stored_shop_names(payload)
    if not names:
        return text
    kept_lines: list[str] = []
    for line in text.splitlines():
        if _line_is_only_a_shop_name(line, names):
            continue
        if not _is_shop_list_fragment(line, names):
            kept_lines.append(line)
            continue
        sentences = [
            part.strip() for part in _SENTENCE_SPLIT.split(line) if part.strip()
        ]
        kept = [part for part in sentences if not _is_shop_list_fragment(part, names)]
        if kept:
            kept_lines.append(" ".join(kept))
    cleaned = "\n".join(kept_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _should_restore_local_shops(
    reply: str, stored: dict[str, Any] | None, user_text: str
) -> bool:
    if not isinstance(stored, dict) or stored.get("status") != "success":
        return False
    if not format_local_shops_section(stored):
        return False
    if _SHOP_ASK.search(user_text):
        return True
    return _shop_name_hits(reply, _stored_shop_names(stored)) >= 1


def _is_success_shop_payload(payload: dict[str, Any] | None) -> bool:
    return isinstance(payload, dict) and payload.get("status") == "success"


def _local_shops_for_reply(
    reply: str,
    turn: dict[str, Any] | None,
    stored: dict[str, Any] | None,
    user_text: str | None,
) -> dict[str, Any] | None:
    """This turn's tool payload wins, including not_found and error.

    Stored shops are used only when the tool did not run and the reply names
    those shops, or the user asked about shops or links.
    """
    if isinstance(turn, dict):
        return turn
    if _should_restore_local_shops(reply, stored, user_text or ""):
        return stored
    return None


def stored_local_shops_match_session(stored: dict[str, Any], state: Any) -> bool:
    """False when the saved list belongs to a different city than the session."""
    if not isinstance(stored, dict) or stored.get("status") != "success":
        return False
    session_city = str(state.get("city") or state.get("user:city") or "").strip()
    session_country = str(
        state.get("country") or state.get("user:country") or ""
    ).strip()
    stored_city = str(stored.get("city") or "").strip()
    stored_country = str(stored.get("country") or "").strip()
    if stored_city and not session_city:
        return False
    if stored_city and session_city and fold_text(stored_city) != fold_text(session_city):
        return False
    if (
        stored_country
        and session_country
        and fold_text(stored_country) != fold_text(session_country)
    ):
        return False
    return True


def ensure_next_steps(
    text: str,
    products: list[dict[str, Any]] | None = None,
    local_shops: dict[str, Any] | None = None,
    currency: str | None = None,
    *,
    stored_shops: dict[str, Any] | None = None,
    user_text: str | None = None,
) -> str:
    cleaned = remove_vitrine_price_section(remove_local_shops_section(text))
    section_payload = _local_shops_for_reply(
        text, local_shops, stored_shops, user_text
    )
    section = format_local_shops_section(section_payload)
    # Drop the model's shop list before tea names are read, so "1. Teasome"
    # is not treated as a recommended cultivar.
    if _is_success_shop_payload(section_payload) and section:
        cleaned = _strip_free_text_shop_list(cleaned, section_payload)
    selected = fill_price_displays(
        products_for_reply(cleaned, products), currency
    )
    price_section = format_vitrine_price_section(selected)
    if not should_attach_next_steps(cleaned, selected) and not section and not price_section:
        return text
    body = _strip_invented_shop_links(strip_next_steps_block(cleaned))
    if _is_success_shop_payload(section_payload) and section:
        body = _strip_free_text_shop_list(body, section_payload)
    if section:
        body = _strip_url_set(body, _local_shop_urls(section_payload)).strip()
    if selected:
        body = strip_model_shop_prices(body)
    parts = [body]
    if price_section:
        parts.append(price_section)
    if section:
        parts.append(section)
    parts.append(format_next_steps_block(selected))
    return "\n\n".join(part for part in parts if part)


def extract_response_text(response: Any) -> str:
    if isinstance(response, dict):
        parts = response.get("parts") or []
        chunks = [
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict)
        ]
        if chunks:
            return "".join(chunks)
        return str(response.get("text") or "")
    return str(response or "")


def prompt_needs_next_steps(prompt: str) -> bool:
    lowered = prompt.lower()
    markers = (
        "3 рекоменда",
        "три рекоменда",
        "хочу мягкий",
        "без горечи утром",
        "что можно взять",
        "что можно купить",
        "подарить",
        "у партнёра",
        "у партнера",
        "купить у партн",
    )
    return any(marker in lowered for marker in markers)


def collect_shop_hits(
    tool: BaseTool, args: dict, tool_context: ToolContext, tool_response: dict
) -> dict | None:
    """after_tool_callback: remember catalog hits from find_in_shop. Return None."""
    del args
    if getattr(tool, "name", "") != "find_in_shop":
        return None
    if not isinstance(tool_response, dict):
        return None
    incoming = _unique_products_by_url(
        _allowed_products(tool_response.get("products") or [])
    )
    if not incoming:
        return None
    existing = list(tool_context.state.get(SHOP_HITS_KEY) or [])
    tool_context.state[SHOP_HITS_KEY] = _merge_products(existing, incoming)
    return None


def collect_local_shop_hits(
    tool: BaseTool, args: dict, tool_context: ToolContext, tool_response: dict
) -> dict | None:
    """Remember find_local_shops hits so the reply can only show those URLs.

    ``temp:local_shops_turn`` is this invocation. A successful list is also
    copied to ``local_shops_last`` for a later turn that does not call the tool.
    ``not_found`` and ``error`` do not replace that saved list.
    """
    del args
    if getattr(tool, "name", "") != "find_local_shops":
        return None
    if not isinstance(tool_response, dict):
        return None
    shops: list[dict[str, Any]] = []
    for item in tool_response.get("shops") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        website = str(item.get("website") or "").strip()
        if not url or not website:
            continue
        shops.append(
            {
                "slug": item.get("slug"),
                "name": item.get("name") or "Магазин",
                "city": item.get("city") or "",
                "country": item.get("country") or "",
                "same_city": bool(item.get("same_city")),
                "url": url,
                "website": website,
                "tea_class": item.get("tea_class") or "",
            }
        )
    record = {
        "status": tool_response.get("status"),
        "class_label_ru": tool_response.get("class_label_ru") or "",
        "tea_class": tool_response.get("tea_class") or "",
        "city": tool_response.get("city") or "",
        "country": tool_response.get("country") or "",
        "shops": shops[:3],
    }
    tool_context.state[LOCAL_SHOPS_KEY] = record
    if record["status"] == "success" and record["shops"]:
        tool_context.state[LOCAL_SHOPS_LAST_KEY] = {
            **record,
            "shops": list(record["shops"]),
        }
        tool_context.state[LOCAL_SHOPS_SAVED_KEY] = "yes"
    return None


def collect_turn_hits(
    tool: BaseTool, args: dict, tool_context: ToolContext, tool_response: dict
) -> dict | None:
    """after_tool_callback for catalog buys and local directory shops."""
    collect_shop_hits(tool, args, tool_context, tool_response)
    collect_local_shop_hits(tool, args, tool_context, tool_response)
    return None


def attach_next_steps_to_response(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    """after_model_callback: append/rewrite the next-steps block on final text."""
    if llm_response.partial or llm_response.error_code:
        return None
    if llm_response.get_function_calls():
        return None
    content = llm_response.content
    if not content or not content.parts:
        return None
    texts = [part.text for part in content.parts if part.text]
    if not texts:
        return None
    original = "".join(texts)
    products = list(callback_context.state.get(SHOP_HITS_KEY) or [])
    local_shops = callback_context.state.get(LOCAL_SHOPS_KEY)
    if not isinstance(local_shops, dict):
        local_shops = None
    stored = callback_context.state.get(LOCAL_SHOPS_LAST_KEY)
    if not isinstance(stored, dict) or not stored_local_shops_match_session(
        stored, callback_context.state
    ):
        stored = None
    updated = ensure_next_steps(
        original,
        products,
        local_shops=local_shops,
        currency=currency_from_state(callback_context.state),
        stored_shops=stored,
        user_text=_callback_user_text(callback_context),
    )
    if updated == original:
        return None
    new_parts: list[types.Part] = []
    replaced = False
    for part in content.parts:
        if part.text:
            if not replaced:
                new_parts.append(types.Part(text=updated))
                replaced = True
            continue
        new_parts.append(part)
    return llm_response.model_copy(
        update={"content": types.Content(role=content.role or "model", parts=new_parts)}
    )


def _callback_user_text(callback_context: Any) -> str:
    try:
        content = getattr(callback_context, "user_content", None)
    except Exception:
        return ""
    if content is None:
        return ""
    parts = getattr(content, "parts", None) or []
    chunks: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if text:
            chunks.append(text)
    return "".join(chunks)


def _buy_markdown_lines(products: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for item in _allowed_products(products):
        url = str(item.get("product_url") or "")
        key = normalize_product_url(url)
        if not url or key in seen:
            continue
        seen.add(key)
        name = str(item.get("product_name") or "чай").strip() or "чай"
        lines.append(f"[Купить: {name}]({url})")
        if len(lines) >= 3:
            break
    return lines


def _allowed_products(products: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in products:
        if not isinstance(item, dict):
            continue
        url = canonical_catalog_url(str(item.get("product_url") or ""))
        if not url:
            continue
        catalog_item = _catalog_items().get(normalize_product_url(url)) or {}
        normalized = {**item, **catalog_item, "product_url": url}
        display = item.get("price_display")
        if isinstance(display, str) and display.strip():
            normalized["price_display"] = display
            for key in ("display_currency", "rate_source", "rate_date"):
                if key in item:
                    normalized[key] = item[key]
        if not normalized.get("matched_slug"):
            normalized["matched_slug"] = item.get("matched_slug")
        normalized["product_name"] = (
            str(normalized.get("product_name") or "").strip() or "чай"
        )
        if not is_buyable(normalized.get("availability")):
            continue
        out.append(normalized)
    return out


def _merge_products(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in [*first, *second]:
        url = normalize_product_url(str(item.get("product_url") or ""))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        merged.append(item)
    return merged


def _unique_products_by_url(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _merge_products(products, [])


def _clean_recommended_name(raw: str) -> str:
    text = _MD_LINK.sub(r"\1", raw)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"[*_`]+", "", text)
    text = re.split(r"\s*[—–]\s*|\s+-\s+", text, maxsplit=1)[0]
    text = text.split(":", 1)[0]
    text = text.strip(" \t.,;!?»«\"'")
    text = re.sub(r"\s+", " ", text)
    if len(text) > 80:
        text = text[:80].rsplit(" ", 1)[0]
    return text


def _select_products_for_names(
    names: list[str], pool: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_urls: set[str] = set()
    for name in names:
        item = _product_for_recommended_name(name, pool, used_urls)
        if not item:
            continue
        url = normalize_product_url(str(item.get("product_url") or ""))
        if url:
            used_urls.add(url)
        selected.append(item)
    return selected


def _product_for_recommended_name(
    name: str,
    pool: list[dict[str, Any]],
    used_urls: set[str],
) -> dict[str, Any] | None:
    best = _best_product_name_match(name, pool, used_urls)
    if best is not None:
        return best

    matches = resolve_query(name, limit=3)
    slugs: list[str] = []
    if matches and int(matches[0].get("score") or 0) >= 50:
        slugs = [
            str(row["slug"])
            for row in matches
            if int(row.get("score") or 0) >= 50
        ]
    for slug in slugs:
        for item in pool:
            item_slug = str(item.get("matched_slug") or "").strip().lower()
            url = normalize_product_url(str(item.get("product_url") or ""))
            if item_slug == slug and url and url not in used_urls:
                return item
        found = _allowed_products(find_products(slug=slug, limit=5))
        best = _best_product_name_match(name, found, used_urls)
        if best is not None:
            return best
        for item in found:
            url = normalize_product_url(str(item.get("product_url") or ""))
            if url and url not in used_urls:
                return item
    return None


def _best_product_name_match(
    name: str,
    products: list[dict[str, Any]],
    used_urls: set[str],
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0
    for item in products:
        url = normalize_product_url(str(item.get("product_url") or ""))
        if not url or url in used_urls:
            continue
        score = _product_name_match_score(
            name, str(item.get("product_name") or "")
        )
        if score > best_score:
            best_score = score
            best = item
    return best if best is not None and best_score >= 60 else None


def _product_name_match_score(recommended_name: str, product_name: str) -> int:
    needle = fold_text(recommended_name)
    candidate = fold_text(product_name)
    if not needle or not candidate:
        return 0
    if needle == candidate:
        return 100
    if needle in candidate or candidate in needle:
        return 90 if min(len(needle), len(candidate)) >= 4 else 45

    generic = {"чай", "пуэр", "шу", "шен"}
    needle_specific = [token for token in needle.split() if token not in generic]
    candidate_specific = [
        token for token in candidate.split() if token not in generic
    ]
    needle_compact = "".join(needle_specific)
    candidate_compact = "".join(candidate_specific)
    if (
        len(needle_compact) >= 4
        and needle_compact in candidate_compact
    ) or (
        len(candidate_compact) >= 4
        and candidate_compact in needle_compact
    ):
        return 80

    overlap = set(needle_specific) & set(candidate_specific)
    if overlap and len(overlap) >= max(1, len(set(needle_specific)) - 1):
        return 60 + min(15, 5 * len(overlap))
    return 0


def _strip_invented_shop_links(text: str) -> str:
    def _replace_md(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        normalized = normalize_product_url(url)
        if is_catalog_url(normalized):
            return match.group(0)
        if is_teashop_url(normalized) or _is_b2btea_url(normalized):
            return label
        host = urlparse(normalized).netloc.lower()
        if host and _looks_like_shop_host(host):
            return label
        return match.group(0)

    cleaned = _MD_LINK.sub(_replace_md, text)

    def _replace_bare(match: re.Match[str]) -> str:
        url = match.group(0)
        normalized = normalize_product_url(url)
        if is_catalog_url(normalized):
            return url
        if is_teashop_url(normalized) or _is_b2btea_url(normalized):
            return ""
        return url

    return re.sub(r"https?://[^\s)>\]]+", _replace_bare, cleaned)


def _looks_like_shop_host(host: str) -> bool:
    shop_bits = (
        "amazon.",
        "wildberries.",
        "ozon.",
        "aliexpress.",
        "shop.",
        "store.",
        "tea-shop",
        "teashop",
    )
    return any(bit in host for bit in shop_bits)
