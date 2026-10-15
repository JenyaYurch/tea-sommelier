"""Pack size and price-per-100g. Titles and variant labels only.

Shipping weight (Shopify ``grams``) is not a pack size: shops put the packed
parcel weight there, which is often heavier than the tea.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_UNIT = r"(?:kilograms?|kg|grammes?|gramow|gramy|gramms?|grams?|gr|g)"
_WEIGHT = re.compile(
    rf"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*-?\s*({_UNIT})\b",
    re.IGNORECASE,
)
_RANGE = re.compile(
    rf"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*[-/\u2013]\s*(\d+(?:[.,]\d+)?)\s*(?:{_UNIT})\b",
    re.IGNORECASE,
)
_COUNT_PACK = re.compile(
    rf"\d+\s*[x\u00d7]\s*\d+(?:[.,]\d+)?\s*(?:{_UNIT})\b",
    re.IGNORECASE,
)
_APPROX = re.compile(
    r"\b(?:ab|od|from|vanaf|ca|circa|approx)\b\.?\s*\d",
    re.IGNORECASE,
)
_DEFAULT_LABELS = {"", "default", "default title", "title"}
_MAX_GRAMS = 2000


@dataclass(frozen=True)
class PackParse:
    """A single pack weight, or an unclear one that must not get a per-100g price."""

    grams: int | None
    unclear: bool


def parse_major_price(value: object) -> Decimal | None:
    """Parse a major-unit price such as ``\"15.99\"``. Non-positive prices are dropped."""
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def woo_major_price(prices: object) -> tuple[Decimal | None, str | None]:
    """WooCommerce Store API prices are minor units (``1599`` + minor 2 → 15.99)."""
    if not isinstance(prices, dict):
        return None, None
    raw_currency = prices.get("currency_code")
    currency = str(raw_currency).strip() if raw_currency else None
    raw = prices.get("price")
    if raw in (None, ""):
        return None, currency or None
    text = str(raw).strip()
    try:
        minor = prices.get("currency_minor_unit")
        minor_n = int(minor) if minor is not None else 2
    except (TypeError, ValueError):
        minor_n = 2
    try:
        if any(sep in text for sep in (".", ",")):
            amount = Decimal(text.replace(",", "."))
        else:
            amount = Decimal(int(text)) / (Decimal(10) ** minor_n)
    except (InvalidOperation, ValueError):
        return None, currency or None
    if amount <= 0:
        return None, currency or None
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), currency or None


def price_per_100g(price: Decimal, grams: int | None) -> Decimal | None:
    """Return price per 100 g, or None when the pack size is missing or not positive."""
    if grams is None or grams <= 0:
        return None
    return (price * Decimal(100) / Decimal(grams)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def money_number(amount: Decimal | None) -> float | None:
    """JSON number with two decimal places (``15.99``, not a binary-looking float)."""
    if amount is None:
        return None
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(format(quantized, "f"))


def resolve_pack(variant_text: str, product_title: str) -> PackParse:
    """Prefer the variant label. Fall back to the product title when the variant has no weight.

    A variant label of ``Default Title`` is ignored. Several different weights,
    a range (``50-100 g``), or a "from" weight (``ab 50 g``) are unclear.
    """
    variant = parse_pack_text(_blank_default(variant_text))
    if variant is not None:
        return variant
    title = parse_pack_text(product_title or "")
    if title is not None:
        return title
    return PackParse(grams=None, unclear=True)


def parse_pack_text(text: str) -> PackParse | None:
    """Parse one string. None means it never mentions a weight."""
    if not text or not str(text).strip():
        return None
    normalized = (
        str(text)
        .replace("ł", "l")
        .replace("Ł", "l")
        .replace("ó", "o")
        .replace("Ó", "o")
    )
    if (
        _RANGE.search(normalized)
        or _APPROX.search(normalized)
        or _COUNT_PACK.search(normalized)
    ):
        return PackParse(grams=None, unclear=True)
    found: list[int] = []
    absurd = False
    for match in _WEIGHT.finditer(normalized):
        grams = _to_grams(match.group(1), match.group(2))
        if grams is None:
            absurd = True
        else:
            found.append(grams)
    unique = set(found)
    if absurd or len(unique) > 1:
        return PackParse(grams=None, unclear=True)
    if len(unique) == 1:
        return PackParse(grams=next(iter(unique)), unclear=False)
    return None


def _blank_default(text: str) -> str:
    if text.strip().lower() in _DEFAULT_LABELS:
        return ""
    return text


def _to_grams(number: str, unit: str) -> int | None:
    try:
        value = Decimal(number.replace(",", "."))
    except InvalidOperation:
        return None
    if unit.lower().startswith("k"):
        value *= 1000
    try:
        whole = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None
    if whole < 1 or whole > _MAX_GRAMS:
        return None
    return whole
