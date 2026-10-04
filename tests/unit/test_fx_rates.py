"""NBRB then ExchangeRate-API. Fixture bodies only — no live network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tea_agent.fx_rates import (
    FX_TIMEOUT_SEC,
    SOURCE_ERAPI,
    SOURCE_NBRB,
    FxQuote,
    clear_fx_cache,
    current_fx_quote,
    fetch_fx_quote,
    parse_erapi,
    parse_nbrb,
    quote_is_fresh,
    set_fx_provider,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fx"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _body(name: str) -> bytes:
    return (FIXTURE / name).read_bytes()


def _json(name: str) -> dict:
    return json.loads(_body(name).decode("utf-8"), parse_float=Decimal)


def _nbrb_get(url: str, timeout: float) -> tuple[int, bytes]:
    assert timeout == FX_TIMEOUT_SEC
    assert timeout <= 5
    if "/EUR?" in url:
        return 200, _body("nbrb_eur.json")
    if "/USD?" in url:
        return 200, _body("nbrb_usd.json")
    raise AssertionError(url)


def test_timeout_is_short() -> None:
    assert FX_TIMEOUT_SEC <= 3


def test_parse_nbrb_divides_by_scale() -> None:
    per, as_of = parse_nbrb(_json("nbrb_eur.json"))
    assert per == Decimal("3.3818")
    assert as_of == date(2026, 10, 4)
    scaled = parse_nbrb(
        {
            "Cur_OfficialRate": Decimal("338.18"),
            "Cur_Scale": 100,
            "Date": "2026-10-04T00:00:00",
        }
    )
    assert scaled == (Decimal("3.3818"), date(2026, 10, 4))
    assert parse_nbrb({"Cur_OfficialRate": 0, "Cur_Scale": 1, "Date": "2026-10-04"}) is None


def test_parse_erapi_requires_success_and_both_rates() -> None:
    byn, usd, as_of = parse_erapi(_json("erapi_eur.json"))
    assert byn == Decimal("3.381805")
    assert usd == Decimal("1.125133")
    assert as_of == date(2026, 10, 4)
    broken = _json("erapi_eur.json")
    broken["result"] = "error"
    assert parse_erapi(broken) is None
    missing = _json("erapi_eur.json")
    del missing["rates"]["BYN"]
    assert parse_erapi(missing) is None


def test_nbrb_is_used_when_it_responds() -> None:
    seen: list[str] = []

    def getter(url: str, timeout: float) -> tuple[int, bytes]:
        seen.append(url)
        return _nbrb_get(url, timeout)

    quote = fetch_fx_quote(http_get=getter, now=NOW)
    assert quote is not None
    assert quote.source == SOURCE_NBRB
    assert quote.eur_date == date(2026, 10, 4)
    assert quote.usd_date == date(2026, 10, 4)
    assert any("api.nbrb.by" in url for url in seen)
    assert not any("open.er-api.com" in url for url in seen)


def test_erapi_is_the_fallback_when_nbrb_times_out() -> None:
    seen: list[str] = []

    def getter(url: str, timeout: float) -> tuple[int, bytes]:
        seen.append(url)
        assert timeout == FX_TIMEOUT_SEC
        if "nbrb.by" in url:
            raise TimeoutError("timed out")
        if "open.er-api.com" in url:
            return 200, _body("erapi_eur.json")
        raise AssertionError(url)

    quote = fetch_fx_quote(http_get=getter, now=NOW)
    assert quote is not None
    assert quote.source == SOURCE_ERAPI
    assert quote.eur_date == date(2026, 10, 4)
    assert quote.usd_date == date(2026, 10, 4)
    assert quote.byn_per_eur == Decimal("3.381805")
    assert any("api.nbrb.by" in url for url in seen)
    assert any("open.er-api.com" in url for url in seen)


def test_both_sources_failing_returns_none_without_raising() -> None:
    def getter(url: str, timeout: float) -> tuple[int, bytes]:
        del url, timeout
        raise TimeoutError("timed out")

    assert fetch_fx_quote(http_get=getter, now=NOW) is None


def test_cache_holds_a_quote_for_24_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[datetime] = []
    quote = FxQuote(
        source=SOURCE_NBRB,
        fetched_at=NOW,
        byn_per_eur=Decimal("3.3818"),
        eur_date=date(2026, 10, 4),
        byn_per_usd=Decimal("3.005"),
        usd_date=date(2026, 10, 4),
    )

    def fetch(*, http_get=None, now=None):
        del http_get
        calls.append(now)
        return quote

    set_fx_provider(None)
    clear_fx_cache()
    monkeypatch.setattr("tea_agent.fx_rates.fetch_fx_quote", fetch)
    assert current_fx_quote(now=NOW) == quote
    assert current_fx_quote(now=NOW + timedelta(hours=10)) == quote
    assert calls == [NOW]
    assert current_fx_quote(now=NOW + timedelta(hours=25)) == quote
    assert calls == [NOW, NOW + timedelta(hours=25)]


def test_cache_refreshes_when_the_rate_date_is_already_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monday = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    stale = FxQuote(
        source=SOURCE_NBRB,
        fetched_at=datetime(2026, 10, 4, 20, 0, tzinfo=UTC),
        byn_per_eur=Decimal("3.3"),
        eur_date=date(2026, 10, 4),
        byn_per_usd=Decimal("3.0"),
        usd_date=date(2026, 10, 4),
    )
    fresh = FxQuote(
        source=SOURCE_NBRB,
        fetched_at=monday,
        byn_per_eur=Decimal("3.4"),
        eur_date=date(2026, 10, 5),
        byn_per_usd=Decimal("3.1"),
        usd_date=date(2026, 10, 5),
    )
    assert not quote_is_fresh(stale, monday)
    calls: list[datetime | None] = []

    def fetch(*, http_get=None, now=None):
        del http_get
        calls.append(now)
        return fresh

    set_fx_provider(None)
    clear_fx_cache()
    monkeypatch.setattr("tea_agent.fx_rates.fetch_fx_quote", fetch)
    monkeypatch.setattr("tea_agent.fx_rates._cache", stale)
    assert current_fx_quote(now=monday).eur_date == date(2026, 10, 5)
    assert calls == [monday]


def test_failed_lookup_is_not_retried_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[datetime | None] = []

    def fetch(*, http_get=None, now=None):
        del http_get
        calls.append(now)
        return None

    set_fx_provider(None)
    clear_fx_cache()
    monkeypatch.setattr("tea_agent.fx_rates.fetch_fx_quote", fetch)
    assert current_fx_quote(now=NOW) is None
    assert current_fx_quote(now=NOW + timedelta(minutes=10)) is None
    assert calls == [NOW]
    assert current_fx_quote(now=NOW + timedelta(minutes=16)) is None
    assert calls == [NOW, NOW + timedelta(minutes=16)]
