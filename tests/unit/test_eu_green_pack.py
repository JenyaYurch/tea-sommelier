"""Pack size and price-per-100g. No network."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tea_agent.eu_shops.pack import (
    parse_major_price,
    parse_pack_text,
    price_per_100g,
    resolve_pack,
    woo_major_price,
)


@pytest.mark.parametrize(
    ("text", "grams"),
    [
        ("50g", 50),
        ("50 g", 50),
        ("100 G", 100),
        ("25 g", 25),
        ("50 Gramm", 50),
        ("100 gramów", 100),
        ("1 kg", 1000),
        ("0,25 kg", 250),
        ("0.05kg", 50),
        ("100-g", 100),
        ("20-g-washipapier-aromaschutztuete", 20),
        ("80 g Geschenkdose mit Holzdeckel", 80),
        ("Waga: 10 g", 10),
    ],
)
def test_single_weight(text: str, grams: int) -> None:
    parsed = parse_pack_text(text)
    assert parsed is not None
    assert parsed.unclear is False
    assert parsed.grams == grams


@pytest.mark.parametrize(
    "text",
    [
        "50-100 g",
        "50/100g",
        "50 g / 100 g",
        "50 g - 100 g",
        "50\u2013100 g",
        "ab 50 g",
        "from 10g",
        "ca. 50 g",
        "15x2 gr",
        "15 x 2g",
    ],
)
def test_range_or_minimum_is_unclear(text: str) -> None:
    parsed = parse_pack_text(text)
    assert parsed is not None
    assert parsed.unclear is True
    assert parsed.grams is None


def test_no_weight_is_absent() -> None:
    assert parse_pack_text("Long Jing Ming Qian") is None
    assert parse_pack_text("") is None


def test_variant_weight_wins_over_a_title_that_lists_several() -> None:
    pack = resolve_pack("50g", "Long Jing 50 g / 100 g")
    assert pack.grams == 50
    assert pack.unclear is False


def test_default_variant_falls_back_to_the_title() -> None:
    pack = resolve_pack("Default Title", "Lu Shan Yun Wu 50 g")
    assert pack.grams == 50
    assert pack.unclear is False


def test_missing_weight_stays_unclear() -> None:
    pack = resolve_pack("Default Title", "Bi Luo Chun")
    assert pack.unclear is True
    assert pack.grams is None


def test_absurd_weight_is_unclear() -> None:
    parsed = parse_pack_text("9000 g")
    assert parsed is not None
    assert parsed.unclear is True


@pytest.mark.parametrize(
    ("price", "grams", "expected"),
    [
        (Decimal("45.00"), 50, Decimal("90.00")),
        (Decimal("160.00"), 250, Decimal("64.00")),
        (Decimal("15.99"), 20, Decimal("79.95")),
        (Decimal("10.00"), 10, Decimal("100.00")),
        (Decimal("39.99"), 80, Decimal("49.99")),
        (Decimal("1.00"), 6, Decimal("16.67")),
        (Decimal("10.00"), 30, Decimal("33.33")),
    ],
)
def test_price_per_100g(price: Decimal, grams: int, expected: Decimal) -> None:
    assert price_per_100g(price, grams) == expected


def test_price_per_100g_empty_without_grams() -> None:
    assert price_per_100g(Decimal("10.00"), None) is None
    assert price_per_100g(Decimal("10.00"), 0) is None


def test_woo_minor_units_and_major_prices() -> None:
    amount, currency = woo_major_price(
        {"price": "1599", "currency_code": "EUR", "currency_minor_unit": 2}
    )
    assert amount == Decimal("15.99")
    assert currency == "EUR"
    assert parse_major_price("25.00") == Decimal("25.00")
    assert parse_major_price("0.00") is None
    assert parse_major_price("") is None
