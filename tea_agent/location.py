"""Session city and country for the b2btea directory.

No geocoding and no Telegram location pins. A known city implies its country.
``City, Country`` (including «Варшава, Польша») wins when both are given.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.adk.tools import ToolContext

from tea_agent.slug_index import fold_text


@dataclass(frozen=True)
class Place:
    """Directory names are English. ``label`` is what we say in Russian."""

    city: str
    country: str
    label: str


# Country strings the companies API accepts (English name, any case).
_COUNTRY_ALIASES: dict[str, str] = {
    "poland": "Poland",
    "polska": "Poland",
    "польша": "Poland",
    "belarus": "Belarus",
    "беларусь": "Belarus",
    "белоруссия": "Belarus",
    "germany": "Germany",
    "deutschland": "Germany",
    "германия": "Germany",
    "czech republic": "Czech Republic",
    "czechia": "Czech Republic",
    "чехия": "Czech Republic",
    "lithuania": "Lithuania",
    "литва": "Lithuania",
    "latvia": "Latvia",
    "латвия": "Latvia",
    "estonia": "Estonia",
    "эстония": "Estonia",
    "ukraine": "Ukraine",
    "украина": "Ukraine",
    "france": "France",
    "франция": "France",
    "austria": "Austria",
    "австрия": "Austria",
    "slovakia": "Slovakia",
    "словакия": "Slovakia",
    "hungary": "Hungary",
    "венгрия": "Hungary",
    "romania": "Romania",
    "румыния": "Romania",
    "portugal": "Portugal",
    "португалия": "Portugal",
    "spain": "Spain",
    "испания": "Spain",
    "italy": "Italy",
    "италия": "Italy",
    "netherlands": "Netherlands",
    "нидерланды": "Netherlands",
    "belgium": "Belgium",
    "бельгия": "Belgium",
}

# API city spelling, country, Russian label, extra aliases.
# Latin «Brest» is not listed: «Brest, France» must not become Belarus.
_CITY_ROWS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("Warsaw", "Poland", "Варшава", ("warsaw", "варшава", "warszawa")),
    ("Krakow", "Poland", "Краков", ("krakow", "краков", "cracow")),
    ("Gdańsk", "Poland", "Гданьск", ("gdansk", "гданьск")),
    ("Wrocław", "Poland", "Вроцлав", ("wroclaw", "вроцлав")),
    ("Poznan", "Poland", "Познань", ("poznan", "познань")),
    ("Lodz", "Poland", "Лодзь", ("lodz", "лодзь")),
    ("Szczecin", "Poland", "Щецин", ("szczecin", "щецин")),
    ("Katowice", "Poland", "Катовице", ("katowice", "катовице")),
    ("Minsk", "Belarus", "Минск", ("minsk", "минск")),
    ("Grodno", "Belarus", "Гродно", ("grodno", "hrodna", "гродно")),
    ("Gomel", "Belarus", "Гомель", ("gomel", "homiel", "гомель")),
    ("Vitebsk", "Belarus", "Витебск", ("vitebsk", "витебск")),
    ("Brest", "Belarus", "Брест", ("брест",)),
    ("Berlin", "Germany", "Берлин", ("berlin", "берлин")),
    ("Prague", "Czech Republic", "Прага", ("prague", "praha", "прага")),
    ("Vilnius", "Lithuania", "Вильнюс", ("vilnius", "вильнюс")),
    ("Riga", "Latvia", "Рига", ("riga", "рига")),
    ("Tallinn", "Estonia", "Таллин", ("tallinn", "таллин", "таллинн")),
    ("Kyiv", "Ukraine", "Киев", ("kyiv", "kiev", "киев")),
    ("Lviv", "Ukraine", "Львов", ("lviv", "львов")),
    ("Vienna", "Austria", "Вена", ("vienna", "wien", "вена")),
)

_CITIES_BY_ALIAS: dict[str, Place] = {}
for _city, _country, _label, _aliases in _CITY_ROWS:
    _place = Place(_city, _country, _label)
    _CITIES_BY_ALIAS[fold_text(_city)] = _place
    _CITIES_BY_ALIAS[fold_text(_label)] = _place
    for _alias in _aliases:
        _CITIES_BY_ALIAS[fold_text(_alias)] = _place


def known_country(value: str) -> str | None:
    """English directory country, or None when the name is not in the alias list."""
    return _COUNTRY_ALIASES.get(fold_text(value))


def _title_place(value: str) -> str:
    return " ".join(part[:1].upper() + part[1:] for part in value.split())


def _is_latin_place(value: str) -> bool:
    folded = fold_text(value)
    if not folded:
        return False
    return all(("a" <= char <= "z") or char.isdigit() or char == " " for char in folded)


def canonical_country(value: str) -> str | None:
    """Map a country the user typed onto the English directory name."""
    known = known_country(value)
    if known:
        return known
    raw = " ".join((value or "").split())
    if raw and _is_latin_place(raw):
        return _title_place(raw)
    return None


def _city_for_country(city_raw: str, country: str) -> Place | None:
    alias = _CITIES_BY_ALIAS.get(fold_text(city_raw))
    if alias and alias.country == country:
        return alias
    if alias and alias.country != country and _is_latin_place(city_raw):
        titled = _title_place(city_raw.strip())
        return Place(titled, country, titled)
    if alias and alias.country != country:
        return None
    if _is_latin_place(city_raw):
        titled = _title_place(city_raw.strip())
        return Place(titled, country, titled)
    return None


def parse_place(text: str) -> Place | None:
    """Parse «Warsaw», «Варшава», or «Warsaw, Poland».

    A single known city supplies the country. An unknown Cyrillic city without
    a country does not guess. No network.
    """
    raw = " ".join((text or "").replace(";", ",").split())
    if not raw:
        return None
    if "," in raw:
        city_raw, country_raw = raw.split(",", 1)
        country = canonical_country(country_raw.strip())
        if not country or not city_raw.strip():
            return None
        return _city_for_country(city_raw.strip(), country)
    alias = _CITIES_BY_ALIAS.get(fold_text(raw))
    if alias:
        return alias
    parts = raw.split()
    if len(parts) >= 2:
        for size in (3, 2, 1):
            if len(parts) <= size:
                continue
            country = known_country(" ".join(parts[-size:]))
            if not country:
                continue
            return _city_for_country(" ".join(parts[:-size]), country)
    return None


def place_state_delta(place: Place) -> dict[str, str]:
    """Session keys, including ``user:`` copies so the instruction can read them."""
    return {
        "city": place.city,
        "user:city": place.city,
        "country": place.country,
        "user:country": place.country,
        "city_label": place.label,
        "user:city_label": place.label,
    }


def place_from_state(state: Any) -> Place | None:
    if state is None:
        return None
    city = str(state.get("city") or state.get("user:city") or "").strip()
    country = str(state.get("country") or state.get("user:country") or "").strip()
    if city and country:
        return parse_place(f"{city}, {country}")
    if city:
        return parse_place(city)
    return None


def resolve_place(city: str, country: str, state: Any = None) -> Place | None:
    """Prefer explicit arguments. Empty arguments fall back to session state."""
    city_arg = (city or "").strip()
    country_arg = (country or "").strip()
    if city_arg or country_arg:
        # «Warsaw, Poland» already names the country. A repeated country
        # argument must not become «Poland, Poland».
        if city_arg and "," in city_arg:
            parsed = parse_place(city_arg)
            if parsed:
                return parsed
        if city_arg and country_arg:
            return parse_place(f"{city_arg}, {country_arg}")
        if city_arg:
            return parse_place(city_arg)
        return None
    return place_from_state(state)


def _write(state: Any, key: str, value: Any) -> None:
    state[key] = value
    state[f"user:{key}"] = value


def remember_place(state: Any, place: Place) -> None:
    for key, value in place_state_delta(place).items():
        if key.startswith("user:"):
            state[key] = value
        else:
            _write(state, key, value)


def save_user_location(
    city: str,
    country: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Remember the user's city and country in this session.

    Call when they name a city, including after you asked once.
    ``country`` may be empty for a common city (Warsaw, Варшава, Minsk):
    the country is filled in. Do not geocode and do not ask for a map pin.

    Args:
        city: City name, or ``City, Country`` in this one field.
        country: English or Russian country, or empty when the city is enough.

    Returns:
        The directory city and country that were saved, or need_location.
    """
    place = resolve_place(city, country, None)
    if place is None:
        return {
            "status": "need_location",
            "hint": (
                "Ask once for a city the directory can use, such as Warsaw, "
                "Варшава, or Warsaw, Poland. No Telegram location pin."
            ),
        }
    remember_place(tool_context.state, place)
    return {
        "status": "success",
        "city": place.city,
        "country": place.country,
        "label": place.label,
    }
