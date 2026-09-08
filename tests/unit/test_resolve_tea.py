from tea_agent.slug_index import resolve_query
from tea_agent.tools import resolve_tea


def test_resolve_longjing() -> None:
    matches = resolve_query("Лунцзин", limit=5)
    slugs = [row["slug"] for row in matches]
    assert slugs, "expected at least one Longjing match"
    assert any("longjing" in slug for slug in slugs)


def test_resolve_biluochun() -> None:
    result = resolve_tea("Би Ло Чунь")
    assert result["status"] == "success"
    slugs = [row["slug"] for row in result["matches"]]
    assert any("biluochun" in slug or "biluo" in slug for slug in slugs)


def test_resolve_gua_pian() -> None:
    result = resolve_tea("Люань Гуапянь")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "liu-an-guapian"


def test_resolve_unknown() -> None:
    result = resolve_tea("xyz-not-a-tea-zzz")
    assert result["status"] == "not_found"
    assert result["matches"] == []
