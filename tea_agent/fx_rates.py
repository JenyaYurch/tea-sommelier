"""BYN exchange rates for the price line.

National Bank of Belarus first (``api.nbrb.by``), then keyless ExchangeRate-API
(``open.er-api.com``). A successful quote is reused for 24 hours, and sooner
when the calendar day has moved past the source date. If both sources fail,
callers show the BYN amount and do not invent a rate.

Nothing here is configured with an environment variable.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SOURCE_NBRB = "NBRB"
SOURCE_ERAPI = "ExchangeRate-API"
NBRB_URL = "https://api.nbrb.by/exrates/rates/{code}?parammode=2"
ERAPI_URL = "https://open.er-api.com/v6/latest/EUR"
FX_TIMEOUT_SEC = 3.0
CACHE_TTL = timedelta(hours=24)
# Refetch before the 24h mark once today's date is past the rate date,
# so a new NBRB day is not held until the exact fetch anniversary.
STALE_DATE_REFRESH = timedelta(hours=12)
FAILURE_COOLDOWN = timedelta(minutes=15)
_USER_AGENT = "tea-sommelier/0.1 (TeaBot; price display)"

HttpGet = Callable[[str, float], tuple[int, bytes]]

_lock = threading.Lock()
_cache: FxQuote | None = None
_failure_until: datetime | None = None
_provider: Callable[[datetime], FxQuote | None] | None = None


@dataclass(frozen=True)
class FxQuote:
    """One source's BYN-per-unit rates and the dates that source published."""

    source: str
    fetched_at: datetime
    byn_per_eur: Decimal
    eur_date: date
    byn_per_usd: Decimal
    usd_date: date

    def conversion_for(self, currency: str) -> tuple[Decimal, date] | None:
        if currency == "EUR":
            return self.byn_per_eur, self.eur_date
        if currency == "USD":
            return self.byn_per_usd, self.usd_date
        return None


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def quote_is_fresh(quote: FxQuote, now: datetime) -> bool:
    """True while the quote is inside 24h and not a stale previous rate date."""
    moment = _aware(now)
    fetched = _aware(quote.fetched_at)
    age = moment - fetched
    if age >= CACHE_TTL:
        return False
    rate_day = min(quote.eur_date, quote.usd_date)
    if moment.date() > rate_day and age >= STALE_DATE_REFRESH:
        return False
    return True


def clear_fx_cache() -> None:
    """Drop the in-process quote and the failure cooldown. Tests use this."""
    global _cache, _failure_until
    with _lock:
        _cache = None
        _failure_until = None


def set_fx_provider(
    provider: Callable[[datetime], FxQuote | None] | None,
) -> None:
    """Replace the live fetch. ``None`` restores NBRB then ExchangeRate-API.

    Tests install a fixture here so ``find_in_shop`` does not open a socket.
    """
    global _provider
    with _lock:
        _provider = provider


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, bool) or value is None:
        return None
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(str(value))
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip().replace(",", "."))
        except Exception:
            return None
    else:
        return None
    if not number.is_finite() or number <= 0:
        return None
    return number


def _parse_date(value: Any) -> date | None:
    raw = str(value or "")
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def parse_nbrb(payload: Any) -> tuple[Decimal, date] | None:
    """``Cur_OfficialRate`` / ``Cur_Scale`` and ``Date``. None if unusable."""
    if not isinstance(payload, dict):
        return None
    rate = _decimal(payload.get("Cur_OfficialRate"))
    raw_scale = payload.get("Cur_Scale")
    scale = Decimal(1) if raw_scale in (None, "") else _decimal(raw_scale)
    as_of = _parse_date(payload.get("Date"))
    if rate is None or scale is None or as_of is None:
        return None
    return rate / scale, as_of


def parse_erapi(payload: Any) -> tuple[Decimal, Decimal, date] | None:
    """Return (BYN per EUR, USD per EUR, rate date) from a success payload."""
    if not isinstance(payload, dict) or payload.get("result") != "success":
        return None
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        return None
    byn_per_eur = _decimal(rates.get("BYN"))
    usd_per_eur = _decimal(rates.get("USD"))
    stamp = payload.get("time_last_update_utc")
    if byn_per_eur is None or usd_per_eur is None or not isinstance(stamp, str):
        return None
    try:
        parsed = parsedate_to_datetime(stamp)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed is None:
        return None
    return byn_per_eur, usd_per_eur, _aware(parsed).date()


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return int(exc.code), body if isinstance(body, bytes) else b""


def _read_json(getter: HttpGet, url: str) -> Any | None:
    host = urlparse(url).netloc
    try:
        status, body = getter(url, FX_TIMEOUT_SEC)
    except Exception as err:
        logger.warning("fx GET failed host=%s (%s)", host, type(err).__name__)
        return None
    if status != 200 or not body:
        logger.warning("fx GET status=%s host=%s", status, host)
        return None
    try:
        return json.loads(body.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("fx invalid JSON host=%s", host)
        return None


def _nbrb_url(code: str) -> str:
    return NBRB_URL.format(code=code)


def _fetch_nbrb(getter: HttpGet, now: datetime) -> FxQuote | None:
    payloads: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(_read_json, getter, _nbrb_url(code)): code
            for code in ("EUR", "USD")
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                payloads[code] = future.result()
            except Exception as err:
                logger.warning("fx NBRB %s crashed (%s)", code, type(err).__name__)
                payloads[code] = None
    eur = parse_nbrb(payloads.get("EUR"))
    usd = parse_nbrb(payloads.get("USD"))
    if eur is None or usd is None:
        return None
    return FxQuote(
        source=SOURCE_NBRB,
        fetched_at=now,
        byn_per_eur=eur[0],
        eur_date=eur[1],
        byn_per_usd=usd[0],
        usd_date=usd[1],
    )


def _fetch_erapi(getter: HttpGet, now: datetime) -> FxQuote | None:
    parsed = parse_erapi(_read_json(getter, ERAPI_URL))
    if parsed is None:
        return None
    byn_per_eur, usd_per_eur, as_of = parsed
    return FxQuote(
        source=SOURCE_ERAPI,
        fetched_at=now,
        byn_per_eur=byn_per_eur,
        eur_date=as_of,
        byn_per_usd=byn_per_eur / usd_per_eur,
        usd_date=as_of,
    )


def fetch_fx_quote(
    *,
    http_get: HttpGet | None = None,
    now: datetime | None = None,
) -> FxQuote | None:
    """Try NBRB, then ExchangeRate-API. None if both fail. Does not raise."""
    getter = http_get or _http_get
    moment = _aware(now or datetime.now(UTC))
    try:
        quote = _fetch_nbrb(getter, moment)
    except Exception as err:
        logger.warning("fx NBRB crashed (%s)", type(err).__name__)
        quote = None
    if quote is not None:
        return quote
    logger.info("fx NBRB unavailable; trying ExchangeRate-API")
    try:
        quote = _fetch_erapi(getter, moment)
    except Exception as err:
        logger.warning("fx ExchangeRate-API crashed (%s)", type(err).__name__)
        quote = None
    if quote is None:
        logger.warning("fx ExchangeRate-API unavailable")
    return quote


def current_fx_quote(now: datetime | None = None) -> FxQuote | None:
    """Cached quote for this process. None when both sources are down.

    A failed lookup is not retried for ``FAILURE_COOLDOWN``, so one outage
    does not add a timeout to every tea in the same reply. Never raises.
    """
    global _cache, _failure_until
    moment = _aware(now or datetime.now(UTC))
    with _lock:
        provider = _provider
        cached = _cache
        failure_until = _failure_until
    if provider is not None:
        try:
            return provider(moment)
        except Exception as err:
            logger.warning("fx provider failed (%s)", type(err).__name__)
            return None
    if cached is not None and quote_is_fresh(cached, moment):
        return cached
    if failure_until is not None and moment < failure_until:
        return None
    quote = fetch_fx_quote(now=moment)
    with _lock:
        if quote is None:
            _failure_until = moment + FAILURE_COOLDOWN
            logger.warning("fx rates unavailable; shop prices will stay in BYN")
            return None
        _cache = quote
        _failure_until = None
        logger.info(
            "fx quote cached source=%s eur_date=%s usd_date=%s",
            quote.source,
            quote.eur_date.isoformat(),
            quote.usd_date.isoformat(),
        )
        return quote
