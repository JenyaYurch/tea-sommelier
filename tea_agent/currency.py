"""Display currency for teashop.by prices.

EUR, USD, and BYN. A missing session value is EUR. The city does not pick it.
Conversion uses a rate quote from ``tea_agent.fx_rates``; this module only
formats. A missing ``price_from_byn`` stays missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

SUPPORTED_CURRENCIES: tuple[str, ...] = ("EUR", "USD", "BYN")
DEFAULT_CURRENCY = "EUR"
_MONEY = Decimal("0.01")
_SUPPORTED = frozenset(SUPPORTED_CURRENCIES)


class RateQuote(Protocol):
    """BYN per one unit of EUR or USD, plus the source's own date."""

    source: str

    def conversion_for(self, currency: str) -> tuple[Decimal, date] | None:
        """Return (BYN per 1 unit, rate date) or None when this quote cannot."""


@dataclass(frozen=True)
class ShownPrice:
    """User-facing price. ``text`` is None when the catalog has no BYN amount."""

    text: str | None
    display_currency: str | None
    rate_source: str | None
    rate_date: str | None


def parse_currency(value: str | None) -> str | None:
    """Return EUR, USD, or BYN. Anything else, including a city name, is None."""
    token = " ".join((value or "").split()).upper()
    if token in _SUPPORTED:
        return token
    return None


def currency_from_state(state: Any) -> str:
    """Session currency, or EUR when unset or unreadable. Never inferred from city."""
    if state is None:
        return DEFAULT_CURRENCY
    try:
        getter = state.get
    except AttributeError:
        return DEFAULT_CURRENCY
    for key in ("currency", "user:currency"):
        parsed = parse_currency(str(getter(key) or ""))
        if parsed:
            return parsed
    return DEFAULT_CURRENCY


def currency_state_delta(code: str) -> dict[str, str]:
    """Session keys, including ``user:`` so the instruction can read them."""
    parsed = parse_currency(code)
    if parsed is None:
        raise ValueError(f"unsupported currency: {code!r}")
    return {"currency": parsed, "user:currency": parsed}


def format_amount(amount: Decimal) -> str:
    """Two decimals, comma separator, half-up. ``25`` becomes ``25,00``."""
    quantized = amount.quantize(_MONEY, rounding=ROUND_HALF_UP)
    return f"{quantized:.2f}".replace(".", ",")


def _amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, float):
        amount = Decimal(str(value))
    elif isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            amount = Decimal(text)
        except Exception:
            return None
    else:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _conversion(quote: RateQuote | None, currency: str) -> tuple[Decimal, date] | None:
    if quote is None:
        return None
    try:
        found = quote.conversion_for(currency)
    except Exception:
        return None
    if not found:
        return None
    per, as_of = found
    if not isinstance(as_of, date) or not isinstance(per, Decimal):
        return None
    if not per.is_finite() or per <= 0:
        return None
    return per, as_of


def show_price(
    price_from_byn: Any,
    currency: str | None,
    quote: RateQuote | None = None,
) -> ShownPrice:
    """Format one catalog amount.

    EUR and USD lead, with the original BYN, the rate source, and that rate's
    date in parentheses. BYN is the amount alone. If ``quote`` is missing, the
    text is the BYN amount and nothing is invented.
    """
    amount = _amount(price_from_byn)
    if amount is None:
        return ShownPrice(None, None, None, None)
    code = parse_currency(currency) or DEFAULT_CURRENCY
    if code == "BYN":
        return ShownPrice(f"{format_amount(amount)} BYN", "BYN", None, None)
    converted = _conversion(quote, code)
    if converted is None or quote is None:
        return ShownPrice(f"{format_amount(amount)} BYN", "BYN", None, None)
    per, as_of = converted
    value = (amount / per).quantize(_MONEY, rounding=ROUND_HALF_UP)
    text = (
        f"~{format_amount(value)} {code} "
        f"({format_amount(amount)} BYN, курс {quote.source} {as_of.strftime('%d.%m.%Y')})"
    )
    return ShownPrice(text, code, quote.source, as_of.isoformat())


def price_display(
    price_from_byn: Any,
    currency: str | None,
    quote: RateQuote | None = None,
) -> str | None:
    """The string to show, or None when ``price_from_byn`` is missing."""
    return show_price(price_from_byn, currency, quote).text


def annotate_product(
    product: dict[str, Any],
    currency: str | None,
    quote: RateQuote | None = None,
) -> dict[str, Any]:
    """Copy ``product`` and set display fields from ``price_from_byn`` only."""
    shown = dict(product)
    price = show_price(product.get("price_from_byn"), currency, quote)
    shown["price_display"] = price.text
    shown["display_currency"] = price.display_currency
    shown["rate_source"] = price.rate_source
    shown["rate_date"] = price.rate_date
    return shown
