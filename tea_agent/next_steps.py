# ruff: noqa: RUF001
"""Canonical next-step chips after recommendations.

Playground shows this block as text. Telegram (TEA-11) parses it into InlineKeyboard.

Canonical block at the end of a recommendation reply:

    ### Что дальше
    [мягче] [дешевле] [без горечи] [подарок] [подробнее]
    [Купить: <name>](<product_url from find_in_shop>)

«Купить» is a markdown link to a teashop.by URL from the local catalog — never invented.
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

from tea_agent.shop_catalog import load_catalog

ACTION_LABELS: tuple[str, ...] = (
    "мягче",
    "дешевле",
    "без горечи",
    "подарок",
    "подробнее",
)
HEADING = "### Что дальше"
SHOP_HITS_KEY = "temp:shop_hits_turn"
_BLOCK_START = re.compile(
    r"(?is)\n*(?:###\s*Что дальше|<!--\s*tea_next_steps\s*-->)\s*"
)
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"https?://[^\s)>\]]+")
_NUMBERED = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+\S+")
_BUY_LABEL = re.compile(r"(?i)^купить(?:\s*[:—-]\s*|\s+)?(.*)$")
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
    for item in load_catalog():
        original = str(item.get("product_url") or "").strip()
        key = normalize_product_url(original)
        if key:
            mapping[key] = original
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
    for label, url in _MD_LINK.findall(text):
        canonical = canonical_catalog_url(url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        buy = _BUY_LABEL.match(label.strip())
        name = (buy.group(1) if buy else label).strip() or label.strip()
        products.append({"product_name": name, "product_url": canonical})
    return products


def invented_buy_urls(text: str) -> list[str]:
    """teashop.by (or other shop) URLs that are not in the local catalog."""
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


def ensure_next_steps(
    text: str, products: list[dict[str, Any]] | None = None
) -> str:
    allowed = _allowed_products(products or [])
    harvested = harvest_catalog_products(strip_next_steps_block(text))
    merged = _merge_products(allowed, harvested)
    if not should_attach_next_steps(text, merged):
        return text
    body = _strip_invented_shop_links(strip_next_steps_block(text))
    return f"{body}\n\n{format_next_steps_block(merged)}"


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
    incoming = _allowed_products(tool_response.get("products") or [])
    if not incoming:
        return None
    existing = list(tool_context.state.get(SHOP_HITS_KEY) or [])
    tool_context.state[SHOP_HITS_KEY] = _merge_products(existing, incoming)
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
    updated = ensure_next_steps(original, products)
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
        name = str(item.get("product_name") or "").strip() or "чай"
        out.append({"product_name": name, "product_url": url})
    return out


def _merge_products(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*first, *second]:
        url = normalize_product_url(str(item.get("product_url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        merged.append(item)
    return merged


def _strip_invented_shop_links(text: str) -> str:
    def _replace_md(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        normalized = normalize_product_url(url)
        if is_catalog_url(normalized):
            return match.group(0)
        if is_teashop_url(normalized):
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
        if is_teashop_url(normalized):
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
