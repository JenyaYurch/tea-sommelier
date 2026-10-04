"""Local shops from the b2btea directory on the tea API.

``GET /api/v2/companies`` (``source: b2btea.com``). A hit is a shop, not a SKU
and not a price. Keyless calls return at most 10 rows and do not page, so this
module queries once per shop type and never sends ``offset``. ``china_focus``
is ignored by the server and filtered here. The public b2btea homepage is not
fetched.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from tea_agent.location import Place, remember_place, resolve_place
from tea_agent.slug_index import fold_text, load_teas
from tea_agent.tea_support import get_json

COMPANIES_PATH = "/api/v2/companies"
_MAX_SHOPS = 3
_KEYLESS_CAP = 10
_CITY_TYPES = ("retail", "tea_house", "ecommerce")
_ALLOWED_TYPES = frozenset(_CITY_TYPES)
_CHINA_FOCUS_OK = frozenset({"strong", "core"})
_DIRECTORY_FROM_CARD = {
    "red": "black",
    "puerh": "puer",
    "pu-erh": "puer",
}
_DIRECTORY_KEYS = frozenset({"green", "black", "white", "yellow", "oolong", "puer"})
_CLASS_LABEL_RU = {
    "green": "зелёный",
    "black": "красный",
    "white": "белый",
    "yellow": "жёлтый",
    "oolong": "улун",
    "puer": "пуэр",
}


def directory_tea_key(tea_type: str) -> str | None:
    """Map a tea-card class onto a directory ``tea`` key. ``red`` becomes ``black``."""
    key = (tea_type or "").strip().lower().replace(" ", "")
    mapped = _DIRECTORY_FROM_CARD.get(key, key)
    if mapped in _DIRECTORY_KEYS:
        return mapped
    return None


def _index_tea_type(slug: str) -> str | None:
    for tea in load_teas():
        if str(tea.get("slug") or "") == slug:
            value = tea.get("tea_type")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
            return None
    return None


def _card_tea_type(slug: str) -> str | None:
    try:
        payload = get_json(f"/api/v2/tea/{slug}")
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("status") == "error":
        return None
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    value = meta.get("tea_type") or payload.get("tea_type")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def resolve_tea_class(tea_slug: str) -> dict[str, Any]:
    """Class from the tea card, then the local slug index if the card fails.

    A cultivar such as biluochun matches its class (green), not the cultivar
    name. ``red`` is stored as directory key ``black``.
    """
    slug = (tea_slug or "").strip()
    card_type = _card_tea_type(slug) if slug else None
    source = "tea_card" if card_type else None
    tea_type = card_type
    if not tea_type and slug:
        tea_type = _index_tea_type(slug)
        if tea_type:
            source = "slug_index"
    directory = directory_tea_key(tea_type or "")
    return {
        "tea_slug": slug,
        "tea_type": tea_type,
        "tea_class": directory,
        "class_source": source,
        "class_label_ru": _CLASS_LABEL_RU.get(directory or "", ""),
    }


def _keys(value: Any) -> list[str]:
    keys: list[str] = []
    if not isinstance(value, list):
        return keys
    for item in value:
        if isinstance(item, dict) and item.get("key"):
            keys.append(str(item["key"]).strip().lower())
        elif isinstance(item, str) and item.strip():
            keys.append(item.strip().lower())
    return keys


def _http_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw or raw.lower() in {"null", "none"}:
        return ""
    if not raw.lower().startswith(("http://", "https://")):
        return ""
    return raw


def _company_params(
    *,
    country: str,
    tea: str,
    type_key: str,
    city: str = "",
) -> dict[str, str]:
    """Server filters that work. No offset, limit, china_focus, or country_code."""
    params = {"country": country, "type": type_key, "tea": tea}
    if city:
        params["city"] = city
    return params


def _load_companies(params: dict[str, str]) -> tuple[list[Any], str, str | None]:
    try:
        payload = get_json(COMPANIES_PATH, params)
    except Exception:
        return [], "en", "error"
    if not isinstance(payload, dict) or payload.get("status") == "error":
        error = "error"
        if isinstance(payload, dict) and payload.get("error"):
            error = str(payload["error"])
        return [], "en", error
    items = payload.get("items")
    if not isinstance(items, list):
        items = []
    lang = payload.get("lang") if isinstance(payload.get("lang"), str) else "en"
    return items[:_KEYLESS_CAP], lang or "en", None


def _shop_from_item(
    item: Any,
    *,
    directory_key: str,
    requested_city: str,
    lang: str,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    slug = str(item.get("slug") or "").strip()
    if not slug:
        return None
    types = _keys(item.get("types"))
    if not (set(types) & _ALLOWED_TYPES):
        return None
    china_focus = str(item.get("china_focus") or "").strip().lower()
    if china_focus not in _CHINA_FOCUS_OK:
        return None
    website = _http_url(item.get("website"))
    if not website:
        return None
    if directory_key not in _keys(item.get("teas")):
        return None
    url = _http_url(item.get("url"))
    if not url:
        url = f"https://b2btea.com/{lang}/c/{slug}/"
    city = str(item.get("city") or "").strip()
    name = str(item.get("name") or slug).strip() or slug
    return {
        "slug": slug,
        "name": name,
        "name_native": str(item.get("name_native") or "").strip(),
        "city": city,
        "country": str(item.get("country") or "").strip(),
        "types": types,
        "china_focus": china_focus,
        "website": website,
        "url": url,
        "same_city": bool(city) and fold_text(city) == fold_text(requested_city),
        "tea_class": directory_key,
    }


def _take(item: Any, seen: set[str], **kwargs: Any) -> dict[str, Any] | None:
    shop = _shop_from_item(item, **kwargs)
    if shop is None or shop["slug"] in seen:
        return None
    seen.add(shop["slug"])
    return shop


def select_shops(place: Place, directory_key: str) -> tuple[list[dict[str, Any]], bool]:
    """Same-city shops first, then online shops in that country. At most 3.

    One companies request per city type (retail, tea_house, ecommerce). The
    country-level ecommerce query runs only when fewer than 3 same-city shops
    remain. Returns ``(shops, any_response_ok)``.
    """
    seen: set[str] = set()
    same_city: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    any_ok = False
    for type_key in _CITY_TYPES:
        items, lang, error = _load_companies(
            _company_params(
                country=place.country,
                city=place.city,
                type_key=type_key,
                tea=directory_key,
            )
        )
        if error:
            continue
        any_ok = True
        for item in items:
            shop = _take(
                item,
                seen,
                directory_key=directory_key,
                requested_city=place.city,
                lang=lang,
            )
            if shop is None:
                continue
            if shop["same_city"]:
                same_city.append(shop)
            else:
                other.append(shop)
    online: list[dict[str, Any]] = []
    if len(same_city) < _MAX_SHOPS:
        items, lang, error = _load_companies(
            _company_params(
                country=place.country,
                type_key="ecommerce",
                tea=directory_key,
            )
        )
        if error:
            pass
        else:
            any_ok = True
            for item in items:
                shop = _take(
                    item,
                    seen,
                    directory_key=directory_key,
                    requested_city=place.city,
                    lang=lang,
                )
                if shop is None:
                    continue
                if shop["same_city"]:
                    same_city.append(shop)
                else:
                    online.append(shop)
    return (same_city + online + other)[:_MAX_SHOPS], any_ok


def _class_note(info: dict[str, Any]) -> str:
    label = info.get("class_label_ru") or info.get("tea_class") or ""
    return (
        "These shops carry this tea class "
        f"({info.get('tea_class')}, say «{label}» in Russian), "
        f"not the cultivar {info.get('tea_slug')}. "
        "Directory cards have no price. "
        "Show only shops[].url (b2btea card) and shops[].website. "
        "Do not invent links. Same-city shops are listed before online shops "
        "in the country."
    )


def _mark_prompted(state: Any) -> None:
    state["location_prompted"] = True
    state["user:location_prompted"] = True


def find_local_shops(
    country: str,
    city: str,
    tea_slug: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Find at most 3 b2btea shops near the user for a tea's class.

    Call when the user asks where to buy nearby, or taps «магазины рядом».
    This is not teashop.by and not a price. Pass ``country`` and ``city`` from
    session state when they are already known (English directory names such as
    Poland and Warsaw). Pass ``tea_slug`` from resolve_tea.

    The tea card supplies the class (biluochun → green, red → directory key
    black). Say the shop carries that class, not the exact tea. Each shop has
    ``url`` (https://b2btea.com/{lang}/c/{slug}/) and ``website``. No other
    links. If the directory errors, say it is unavailable and still answer the
    tea question.

    Args:
        country: English country, or empty to use the saved session country.
        city: City, or empty to use the saved session city.
        tea_slug: tea.support slug. The directory is queried by class, not by
            this cultivar name.

    Returns:
        Up to 3 shops, or need_location / not_found / unknown_class / error.
        There is never a price field.
    """
    slug = (tea_slug or "").strip()
    state = tool_context.state
    if not slug:
        return {
            "status": "error",
            "error": "need_tea_slug",
            "shops": [],
            "hint": "Pass tea_slug from resolve_tea. Do not invent a shop.",
        }
    place = resolve_place(city, country, state)
    if place is None:
        _mark_prompted(state)
        return {
            "status": "need_location",
            "shops": [],
            "tea_slug": slug,
            "hint": (
                "City is unknown. Ask once for a city (Warsaw, Варшава, or "
                "Warsaw, Poland). Do not ask for a Telegram location pin. "
                "After they answer, call save_user_location, then "
                "find_local_shops again. If location_prompted is already set "
                "and the city is still empty, do not ask again; suggest /city."
            ),
        }
    remember_place(state, place)
    try:
        info = resolve_tea_class(slug)
    except Exception:
        return {
            "status": "error",
            "error": "directory_unavailable",
            "shops": [],
            "tea_slug": slug,
            "city": place.city,
            "country": place.country,
            "hint": (
                "The directory failed. Say it is temporarily unavailable, "
                "invent nothing, and still give the tea recommendation."
            ),
        }
    if not info.get("tea_class"):
        return {
            "status": "unknown_class",
            "shops": [],
            "tea_slug": slug,
            "tea_type": info.get("tea_type"),
            "city": place.city,
            "country": place.country,
            "hint": (
                "No directory class for this tea. Do not invent shops. "
                "A teashop.by listing from find_in_shop is still allowed."
            ),
        }
    try:
        shops, any_ok = select_shops(place, str(info["tea_class"]))
    except Exception:
        shops, any_ok = [], False
    base = {
        "country": place.country,
        "city": place.city,
        "city_label": place.label,
        "tea_slug": slug,
        "tea_type": info.get("tea_type"),
        "tea_class": info.get("tea_class"),
        "class_label_ru": info.get("class_label_ru"),
        "class_source": info.get("class_source"),
        "shops": shops,
        "source": "b2btea.com",
    }
    if shops:
        return {
            **base,
            "status": "success",
            "note": _class_note(info),
        }
    if not any_ok:
        return {
            **base,
            "status": "error",
            "error": "directory_unavailable",
            "hint": (
                "The shop directory timed out or returned an error. "
                "Tell the user it is temporarily unavailable. Do not invent "
                "shops, links, or prices. Keep the tea recommendation."
            ),
        }
    return {
        **base,
        "status": "not_found",
        "note": (
            "No shops matched this class in that city or as online shops in "
            "the country. Say so. Do not invent shops, links, or prices."
        ),
    }
