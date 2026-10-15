"""Listing parsers for shops that publish HTML instead of a JSON catalog.

Product pages are parsed only for Czajownia, where the tile has no stock
and the weight prices are percentage modifiers of the gross price.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from tea_agent.eu_shops.pack import parse_major_price

_MONEY = re.compile(r"\d[\d\s\u00a0.,]*\d|\d")
_MULTI_PACK = re.compile(r"(\d+)\s*g\s*x\s*(\d+)", re.IGNORECASE)
_WEIGHT_LABEL = re.compile(r"\d+(?:[.,]\d+)?\s*(?:kg|g|gr)\b", re.IGNORECASE)
_FORMATTED_GRAMS = re.compile(r"(\d+(?:[.,]\d+)?)\s*g\b", re.IGNORECASE)


@dataclass(frozen=True)
class ListingItem:
    """One product row, or one weight of a product, before the tea filter."""

    title: str
    url: str
    context: str = ""
    price: Decimal | None = None
    currency: str | None = None
    pack_text: str = ""
    in_stock: bool | None = None
    needs_detail: bool = False


@dataclass(frozen=True)
class ParsedPage:
    recognized: bool
    items: tuple[ListingItem, ...] = ()
    next_urls: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def parse_listing(parser: str, html: str, page_url: str) -> ParsedPage:
    if parser == "czajownia":
        return parse_czajownia_listing(html, page_url)
    if parser == "yoshien":
        return parse_yoshien_listing(html, page_url)
    if parser == "nanyi":
        return parse_nanyi_listing(html, page_url)
    if parser == "eherbata":
        return parse_eherbata_listing(html, page_url)
    return ParsedPage(False)


def parse_product(parser: str, html: str, page_url: str) -> ParsedPage:
    if parser == "czajownia":
        return parse_czajownia_product(html, page_url)
    return ParsedPage(False)


def parse_czajownia_listing(html: str, page_url: str) -> ParsedPage:
    if not html or not str(html).strip():
        return ParsedPage(False)
    soup = BeautifulSoup(html, "html.parser")
    tiles = [tile for tile in soup.select("product-tile") if tile.get("name")]
    if not tiles and soup.select_one(".pagination") is None:
        return ParsedPage(False)
    items: list[ListingItem] = []
    seen: set[str] = set()
    for tile in tiles:
        title = " ".join(str(tile.get("name") or "").split())
        link = tile.select_one("a[href]")
        if not title or link is None:
            continue
        url = urljoin(page_url, str(link.get("href") or ""))
        url = url.split("#", 1)[0]
        if not url or url in seen:
            continue
        seen.add(url)
        items.append(
            ListingItem(
                title=title,
                url=url,
                context=str(tile.get("producer") or ""),
                needs_detail=True,
            )
        )
    return ParsedPage(True, tuple(items), tuple(_czajownia_next(soup, page_url)))


def parse_czajownia_product(html: str, page_url: str) -> ParsedPage:
    if not html or not str(html).strip():
        return ParsedPage(False)
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h1")
    if heading is None:
        return ParsedPage(False)
    title = " ".join(heading.get_text(" ", strip=True).split())
    if not title:
        return ParsedPage(False)
    price = _czajownia_gross(soup)
    if price is None:
        return ParsedPage(True, warnings=("price missing",))
    stock = _czajownia_stock(soup)
    group = _weight_group(soup)
    if group is None:
        return ParsedPage(
            True,
            (
                ListingItem(
                    title=title,
                    url=_clean_url(page_url),
                    price=price,
                    pack_text=title,
                    in_stock=stock,
                ),
            ),
        )
    option_name = str(group.get("option-name") or "option")
    items: list[ListingItem] = []
    for option in group.select("h-option"):
        label = str(option.get("data-user-value") or "").strip()
        value = str(option.get("value") or "").strip()
        if not label or not value:
            continue
        variant_price = (price * _modifier_factor(option.get("data-price-modifier"))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        items.append(
            ListingItem(
                title=title,
                url=_option_url(page_url, option_name, value),
                price=variant_price,
                pack_text=label,
                in_stock=stock,
            )
        )
    if not items:
        return ParsedPage(True, warnings=("no weight options",))
    return ParsedPage(True, tuple(items))


def parse_yoshien_listing(html: str, page_url: str) -> ParsedPage:
    if not html or not str(html).strip():
        return ParsedPage(False)
    soup = BeautifulSoup(html, "html.parser")
    tiles = [tile for tile in soup.select(".cs-product-tile") if tile.select_one("a.product-item-link")]
    if not tiles and "catalog-category-view" not in html:
        return ParsedPage(False)
    items: list[ListingItem] = []
    seen: set[str] = set()
    for tile in tiles:
        link = tile.select_one("a.product-item-link")
        if link is None:
            continue
        name = tile.select_one(".cs-product-tile__name")
        title = name.get_text(" ", strip=True) if name is not None else ""
        title = " ".join(title.split())
        amount = tile.select_one("[data-price-amount]")
        price = parse_major_price(amount.get("data-price-amount")) if amount is not None else None
        if not title or price is None:
            continue
        unit = tile.select_one(".cs-product-tile__unit-value")
        pack_text = unit.get_text(" ", strip=True) if unit is not None else ""
        subtitle = tile.select_one(".cs-product-tile__category-subtitle")
        context = subtitle.get_text(" ", strip=True) if subtitle is not None else ""
        url = urljoin(page_url, str(link.get("href") or ""))
        if url in seen:
            continue
        seen.add(url)
        items.append(
            ListingItem(
                title=title,
                url=url,
                context=context,
                price=price,
                pack_text=pack_text,
                in_stock=tile.select_one(".cs-product-tile__out-of-stock") is None,
            )
        )
    return ParsedPage(True, tuple(items), tuple(_same_path_next(soup, page_url)))


def parse_nanyi_listing(html: str, page_url: str) -> ParsedPage:
    state = _epages_state(html)
    if state is None:
        return ParsedPage(False)
    blocks = state.get("categoryProductData")
    if not isinstance(blocks, dict) or not blocks:
        return ParsedPage(False)
    products: list[dict] = []
    total = 0
    for block in blocks.values():
        if not isinstance(block, dict):
            continue
        rows = block.get("products")
        if isinstance(rows, list):
            products.extend(row for row in rows if isinstance(row, dict))
        try:
            total = max(total, int(block.get("totalNumberOfProducts") or 0))
        except (TypeError, ValueError):
            continue
    items, warnings = nanyi_products_to_items(products, page_url)
    next_urls: tuple[str, ...] = ()
    if products and total > len(products):
        next_urls = (_with_page(page_url, _page_number(page_url) + 1),)
    return ParsedPage(True, items, next_urls, warnings)


def nanyi_products_to_items(
    products: list[dict],
    page_url: str,
) -> tuple[tuple[ListingItem, ...], tuple[str, ...]]:
    """Map epages product dicts. Sets and variation masters are skipped."""
    items: list[ListingItem] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for product in products:
        if product.get("hasVariations") is True or product.get("isVariationMaster") is True:
            if "skipped a variation master" not in warnings:
                warnings.append("skipped a variation master")
            continue
        title = " ".join(str(product.get("name") or "").split())
        href = str(product.get("href") or "").strip()
        if not title or not href:
            continue
        if _nanyi_skip(product, title):
            continue
        price_row = product.get("price") if isinstance(product.get("price"), dict) else {}
        price = parse_major_price(price_row.get("amount"))
        if price is None:
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        currency = str(price_row.get("currency") or "").strip() or None
        items.append(
            ListingItem(
                title=title,
                url=url,
                price=price,
                currency=currency,
                pack_text=_nanyi_pack_text(product),
                in_stock=_nanyi_stock(product),
            )
        )
    return tuple(items), tuple(warnings)


def parse_eherbata_listing(html: str, page_url: str) -> ParsedPage:
    """Schema.org Product markup. Live eHerbata is blocked, so this stays unused there."""
    if not html or "schema.org/Product" not in html:
        return ParsedPage(False)
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.select('[itemtype*="schema.org/Product"]')
    if not nodes:
        return ParsedPage(False)
    items: list[ListingItem] = []
    for node in nodes:
        title = _itemprop(node, "name")
        href = _itemprop(node, "url")
        if not title:
            continue
        url = urljoin(page_url, href) if href else page_url
        price = parse_major_price(_itemprop(node, "price"))
        currency = _itemprop(node, "priceCurrency") or None
        availability = _itemprop(node, "availability")
        in_stock = "OutOfStock" not in availability
        items.append(
            ListingItem(
                title=title,
                url=url,
                price=price,
                currency=currency,
                pack_text=title,
                in_stock=in_stock,
            )
        )
    return ParsedPage(True, tuple(items))


def _czajownia_next(soup: BeautifulSoup, page_url: str) -> list[str]:
    parts = urlsplit(page_url)
    root = re.sub(r"/\d+$", "", parts.path.rstrip("/"))
    found: list[str] = []
    seen: set[str] = set()
    pattern = re.compile("^" + re.escape(root) + r"/\d+$")
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        target = urlsplit(urljoin(page_url, href))
        path = target.path.rstrip("/")
        if not pattern.match(path) or "/default/" in path:
            continue
        url = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        if url in seen or path == parts.path.rstrip("/"):
            continue
        seen.add(url)
        found.append(url)
    return found


def _same_path_next(soup: BeautifulSoup, page_url: str) -> list[str]:
    base = urlsplit(page_url)
    found: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        classes = " ".join(link.get("class") or [])
        rel = " ".join(link.get("rel") or [])
        label = link.get_text(" ", strip=True).lower()
        full = urljoin(page_url, href)
        target = urlsplit(full)
        if target.path.rstrip("/") != base.path.rstrip("/"):
            continue
        interesting = (
            "next" in rel
            or "next" in classes
            or label in {"next", "weiter"}
            or "p=" in target.query
        )
        if not interesting or full in seen or full == page_url:
            continue
        seen.add(full)
        found.append(full)
    return found


def _czajownia_gross(soup: BeautifulSoup) -> Decimal | None:
    root = soup.select_one(".js__product-price-gross") or soup
    special_box = root.select_one(".js__special-offer-container")
    special = root.select_one(".js__special-offer-price-value")
    if special is not None and special_box is not None and not special_box.has_attr("hidden"):
        amount = _shop_price(special.get_text(" ", strip=True))
        if amount is not None:
            return amount
    regular = root.select_one(".js__no-special-offer-container .js__price-value")
    if regular is None:
        regular = root.select_one(".js__price-value")
    if regular is None:
        return None
    return _shop_price(regular.get_text(" ", strip=True))


_SCHEMA_IN_STOCK = ("/instock", "/limitedavailability")
_SCHEMA_OUT_OF_STOCK = (
    "/outofstock",
    "/discontinued",
    "/soldout",
    "/preorder",
    "/backorder",
)
_QUANTITY_IN_STOCK = frozenset({"mala", "srednia", "duza"})


def _czajownia_stock(soup: BeautifulSoup) -> bool:
    """Stock for the variant the product page renders.

    Shoper prints one availability block for the selected variant and the same
    flag is applied to every weight option. In-stock pages often omit the
    ``_available`` class and say ``średnia ilość`` next to schema.org/InStock.
    """
    node = soup.select_one(".product-availability")
    if node is None:
        return False
    host = node.parent if node.name == "product-availability" and node.parent else node
    schema = _schema_in_stock(host)
    if schema is not None:
        return schema
    if node.select_one(".product-availability__description_unavailable"):
        return False
    if node.select_one(".product-availability__description_available"):
        return True
    tokens = set(_fold_words(node.get_text(" ", strip=True)))
    if "brak" in tokens or any(token.startswith("niedost") for token in tokens):
        return False
    if tokens & {"dostepny", "dostepna", "dostepne"}:
        return True
    return "ilosc" in tokens and bool(tokens & _QUANTITY_IN_STOCK)


def _schema_in_stock(node) -> bool | None:
    for script in node.find_all("script"):
        raw = script.string or ""
        if "availability" not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        value = _availability_value(data)
        if not value:
            continue
        folded = value.strip().lower()
        if any(folded.endswith(suffix) for suffix in _SCHEMA_IN_STOCK):
            return True
        if any(folded.endswith(suffix) for suffix in _SCHEMA_OUT_OF_STOCK):
            return False
    return None


def _availability_value(data: object) -> str | None:
    if isinstance(data, dict):
        offers = data.get("offers")
        if isinstance(offers, dict):
            value = offers.get("availability")
            if isinstance(value, str) and value.strip():
                return value
        for child in data.values():
            found = _availability_value(child)
            if found:
                return found
    elif isinstance(data, list):
        for child in data:
            found = _availability_value(child)
            if found:
                return found
    return None


def _weight_group(soup: BeautifulSoup):
    for group in soup.select("select-variant-option"):
        for option in group.select("h-option"):
            label = str(option.get("data-user-value") or "")
            if _WEIGHT_LABEL.search(label):
                return group
    return None


def _modifier_factor(raw: object) -> Decimal:
    if not raw:
        return Decimal(1)
    match = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*%", str(raw))
    if not match:
        return Decimal(1)
    percent = Decimal(match.group(1).replace(",", "."))
    return Decimal(1) + (percent / Decimal(100))


def _option_url(page_url: str, option_name: str, value: str) -> str:
    parts = urlsplit(page_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode({option_name: value}), ""))


def _clean_url(page_url: str) -> str:
    parts = urlsplit(page_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _shop_price(text: str) -> Decimal | None:
    match = _MONEY.search(text or "")
    if not match:
        return None
    raw = match.group(0).replace("\u00a0", "").replace(" ", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    return parse_major_price(raw)


def _fold_words(text: str) -> list[str]:
    from tea_agent.eu_shops.classify import fold_latin

    return fold_latin(text).split()


def _epages_state(html: str) -> dict | None:
    if not html:
        return None
    marker = "storeInitialState:"
    start = html.find(marker)
    if start < 0:
        return None
    quote = html.find('"', start + len(marker))
    if quote < 0:
        return None
    index = quote + 1
    chars: list[str] = []
    while index < len(html):
        char = html[index]
        if char == "\\":
            if index + 1 >= len(html):
                return None
            chars.append(char)
            chars.append(html[index + 1])
            index += 2
            continue
        if char == '"':
            break
        chars.append(char)
        index += 1
    else:
        return None
    try:
        decoded = json.loads('"' + "".join(chars) + '"')
        data = json.loads(decoded)
    except (json.JSONDecodeError, TypeError, UnicodeError):
        return None
    if isinstance(data, dict):
        return data
    return None


def _page_number(url: str) -> int:
    raw = dict(parse_qsl(urlsplit(url).query)).get("page")
    try:
        return max(1, int(raw or "1"))
    except ValueError:
        return 1


def _with_page(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _nanyi_skip(product: dict, title: str) -> bool:
    formatted = ""
    price = product.get("price")
    if isinstance(price, dict):
        formatted = str(price.get("formatted") or "")
    if re.search(r"\bsets?\b", formatted, re.IGNORECASE):
        return True
    folded = title.casefold()
    if "portionen" in folded:
        return True
    match = _MULTI_PACK.search(folded.replace("\u00d7", "x"))
    return bool(match and int(match.group(2)) > 1)


def _nanyi_pack_text(product: dict) -> str:
    base = product.get("basePrice")
    if isinstance(base, dict):
        quantity = base.get("quantity")
        if isinstance(quantity, dict):
            unit = str(quantity.get("unit") or "").lower()
            if unit in {"g", "gram", "grams", "gramm"}:
                text = _grams_text(quantity.get("amount"))
                if text:
                    return text
    price = product.get("price")
    formatted = str(price.get("formatted") or "") if isinstance(price, dict) else ""
    match = _FORMATTED_GRAMS.search(formatted.replace("\u00a0", " "))
    if match:
        return f"{match.group(1)} g"
    return ""


def _grams_text(amount: object) -> str | None:
    if amount in (None, ""):
        return None
    try:
        number = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None
    if number <= 0:
        return None
    if number == number.to_integral_value():
        return f"{int(number)} g"
    return f"{number} g"


def _nanyi_stock(product: dict) -> bool:
    if product.get("outOfStock") is True:
        return False
    micro = str(product.get("stockLevelMicrodata") or "")
    if "OutOfStock" in micro:
        return False
    if product.get("onStock") is False:
        return False
    return True


def _itemprop(node, name: str) -> str:
    element = node.select_one(f'[itemprop="{name}"]')
    if element is None:
        return ""
    for attr in ("content", "href"):
        value = element.get(attr)
        if value:
            return " ".join(str(value).split())
    return " ".join(element.get_text(" ", strip=True).split())
