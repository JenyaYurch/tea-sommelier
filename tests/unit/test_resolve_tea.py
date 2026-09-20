from tea_agent.slug_index import resolve_query
from tea_agent.tools import resolve_tea, search_teas, similar_teas


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


def test_resolve_bai_mudan() -> None:
    result = resolve_tea("Бай Му Дань")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "bai-mudan"
    assert result["matches"][0].get("tea_type") == "white"


def test_resolve_yin_zhen() -> None:
    result = resolve_tea("Инь Чжэнь")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "baihao-yinzhen"


def test_resolve_junshan() -> None:
    result = resolve_tea("Цзюнь Шань Инь Чжэнь")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "junshan-yin-zhen"


def test_resolve_dianhong() -> None:
    result = resolve_tea("Дянь Хун")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "dianhong-gongfu"


def test_resolve_sheng_puerh() -> None:
    result = resolve_tea("шен пуэр")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "7542"


def test_resolve_shu_puerh() -> None:
    result = resolve_tea("шу пуэр")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "7572-shu-bing"


def test_resolve_dong_ding() -> None:
    result = resolve_tea("Дун Дин")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "dong-ding-wulong"


def test_resolve_jasmine() -> None:
    result = resolve_tea("жасмин")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "moli-longzhu"


def test_gaba_has_no_tea_support_slug() -> None:
    """tea.support has no tea_type=gaba and no GABA slug (2026-09 smoke)."""
    result = resolve_tea("GABA")
    assert result["status"] == "not_found"
    assert result["matches"] == []


def test_resolve_yulan_chi_gan() -> None:
    result = resolve_tea("Ю Лань Чи Гань")
    assert result["status"] == "success"
    assert result["matches"][0]["slug"] == "chi-gan-xiao-zhong"


def test_ye_shen_sichuan_does_not_map_to_red_dianhong() -> None:
    result = resolve_tea("Е Шен Сычуань")
    slugs = [row["slug"] for row in result.get("matches") or []]
    assert "dianhong-ye-sheng" not in slugs


def test_search_teas_keeps_non_green_in_dictionary(monkeypatch) -> None:
    def fake_get_json(path, params=None):
        del path, params
        return {
            "status": "success",
            "query": "dianhong",
            "matches": [
                {
                    "slug": "dianhong-gongfu",
                    "name": "Dianhong",
                    "score": 0.9,
                    "tea_type": "red",
                },
                {
                    "slug": "not-in-dict-xyz",
                    "name": "Nope",
                    "score": 0.8,
                    "tea_type": "green",
                },
            ],
        }

    monkeypatch.setattr("tea_agent.tools.get_json", fake_get_json)
    result = search_teas("dianhong")
    slugs = [row["slug"] for row in result["matches"]]
    assert "dianhong-gongfu" in slugs
    assert "not-in-dict-xyz" not in slugs


def test_similar_teas_keeps_non_green_in_dictionary(monkeypatch) -> None:
    def fake_get_json(path, params=None):
        del path, params
        return {
            "status": "success",
            "slug": "dianhong-gongfu",
            "similar": [
                {
                    "slug": "bai-mudan",
                    "name": "Bai Mudan",
                    "score": 0.7,
                    "tea_type": "white",
                },
                {"slug": "missing-tea", "name": "X", "score": 0.6, "tea_type": "green"},
            ],
        }

    monkeypatch.setattr("tea_agent.tools.get_json", fake_get_json)
    result = similar_teas("dianhong-gongfu")
    slugs = [row["slug"] for row in result["similar"]]
    assert "bai-mudan" in slugs
    assert "missing-tea" not in slugs
