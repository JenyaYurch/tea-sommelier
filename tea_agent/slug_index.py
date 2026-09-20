"""Local slug dictionary for Chinese teas in tea.support (v1 types)."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

# Famous Russian / Latin aliases that the list endpoint does not provide on Free.
EXTRA_ALIASES: dict[str, list[str]] = {
    "xihu-longjing": [
        "Лунцзин",
        "Лун Цзин",
        "Си Ху Лун Цзин",
        "Сиху Лунцзин",
        "Си Ху Лунцзин",
        "Longjing",
        "Long Jing",
        "Dragon Well",
        "西湖龙井",
        "龙井",
    ],
    "biluochun": [
        "Би Ло Чунь",
        "Билочунь",
        "Дунтин Би Ло Чунь",
        "Bi Luo Chun",
        "Biluochun",
        "碧螺春",
    ],
    "anji-baicha": [
        "Аньцзи Бай Ча",
        "Аньцзи Байча",
        "Anji Bai Cha",
        "安吉白茶",
    ],
    "taiping-hou-kui": [
        "Тай Пин Хоу Куй",
        "Тайпин Хоукуй",
        "Хоу Куй",
        "Taiping Houkui",
        "太平猴魁",
    ],
    "liu-an-guapian": [
        "Люань Гуапянь",
        "Лю Ань Гуа Пянь",
        "Гуапянь",
        "Liu An Gua Pian",
        "六安瓜片",
    ],
    "zhu-ye-qing": [
        "Чжу Е Цин",
        "Чжуецин",
        "Zhu Ye Qing",
        "竹叶青",
    ],
    "mengding-gan-lu": [
        "Мэндин Гань Лу",
        "Мэндин Ганьлу",
        "Mengding Ganlu",
        "蒙顶甘露",
    ],
    "xinyang-mao-jian": [
        "Синь Ян Мао Цзянь",
        "Синьян Маоцзянь",
        "Xinyang Maojian",
        "信阳毛尖",
    ],
    "lushan-yun-wu": [
        "Лушань Юнь У",
        "Лушань Юньу",
        "Lushan Yunwu",
        "庐山云雾",
    ],
    "huangshan-mao-feng": [
        "Хуаншань Мао Фэн",
        "Хуаншань Маофэн",
        "Хуан Шань Мао Фэн",
        "Huangshan Maofeng",
        "黄山毛峰",
    ],
    "bai-mao-hou": [
        "Бай Мао Хоу",
        "Белая обезьяна",
        "Bai Mao Hou",
        "白毛猴",
    ],
    "meicha": [
        "Чжэнь Мэй",
        "Чжен Мэй",
        "Брови Дракона",
        "Zhen Mei",
        "Chun Mee",
        "珍眉",
        "眉茶",
    ],
    "queshe-lucha": [
        "Цюэ Шэ",
        "Цюе Шэ",
        "Воробьиные язычки",
        "Que She",
        "雀舌绿茶",
    ],
    "ziyang-mao-jian": [
        "Цзыян Фуси Мао Цзянь",
        "Цзыян Мао Цзянь",
        "Ziyang Maojian",
        "紫阳毛尖",
    ],
    "ziyang-lucha": [
        "Цзыян Цуйфэн",
        "Цзыян Цуйфэн Гао Шань",
        "Ziyang Cuifeng",
        "紫阳绿茶",
    ],
    "yin-si-lu-cha": [
        "Инь Сы",
        "Серебряные Нити",
        "Yin Si",
        "银丝绿茶",
    ],
    "moli-longzhu": [
        "Моли Лун Чжу",
        "Жасминовая Жемчужина Дракона",
        "Бай Лун Чжу",
        "Белая Жемчужина Дракона",
        "Жасмин",
        "Moli Longzhu",
        "茉莉龙珠",
    ],
    "moli-feng-yan": [
        "Моли Фэн Янь",
        "Жасминовый Глаз Феникса",
        "Moli Feng Yan",
        "茉莉凤眼",
    ],
    "moli-yin-hao": [
        "Серебряный жасминовый пух",
        "Моли Люй Ча Бай Хао",
        "Moli Yin Hao",
        "茉莉银毫",
    ],
    "yun-nan-mao-feng": [
        "Мао Фэн Тоу Чунь",
        "Yunnan Maofeng",
        "云南毛峰",
    ],
    "pingshui-zhucha": [
        "Ганпаудер",
        "Gunpowder",
        "Круглый чай",
        "Люй Чжу",
        "Зеленая жемчужина",
        "珠茶",
    ],
    "bai-mudan": [
        "Бай Му Дань",
        "Баймудань",
        "Bai Mudan",
        "Bai Mu Dan",
        "White Peony",
        "白牡丹",
    ],
    "baihao-yinzhen": [
        "Инь Чжэнь",
        "Бай Хао Инь Чжэнь",
        "Байхао Иньчжэнь",
        "Baihao Yinzhen",
        "Bai Hao Yin Zhen",
        "Silver Needle",
        "白毫银针",
    ],
    "junshan-yin-zhen": [
        "Цзюнь Шань Инь Чжэнь",
        "Цзюньшань Инь Чжэнь",
        "Junshan Yinzhen",
        "Jun Shan Yin Zhen",
        "君山银针",
    ],
    "dianhong-gongfu": [
        "Дянь Хун",
        "Дяньхун",
        "Дянь Хун Гунфу",
        "Dian Hong",
        "Dianhong",
        "Dian Hong Gongfu",
        "滇红工夫",
        "滇红",
    ],
    "dianhong-ye-sheng": [
        "Дянь Хун Е Шен",
        "Dianhong Ye Sheng",
    ],
    "chi-gan-xiao-zhong": [
        "Ю Лань Чи Гань",
        "Чи Гань",
        "Чи Гань Сяо Чжун",
        "Chi Gan Xiaozhong",
        "赤甘小种",
    ],
    "7542": [
        "шен пуэр",
        "шэн пуэр",
        "шен пуер",
        "шэн пуер",
        "sheng puerh",
        "sheng pu-erh",
        "sheng puer",
        "raw puerh",
        "сырьевой пуэр",
    ],
    "7572-shu-bing": [
        "шу пуэр",
        "шу пуер",
        "shu puerh",
        "shu pu-erh",
        "shu puer",
        "ripe puerh",
        "готовый пуэр",
        "7572",
    ],
    "dong-ding-wulong": [
        "Дун Дин",
        "Дундин",
        "Дун Дин Улун",
        "Dong Ding",
        "Tung Ting",
        "凍頂烏龍",
        "冻顶乌龙",
    ],
}


def _data_path() -> Path:
    here = Path(__file__).resolve()
    names = ("tea_slugs.json", "green_teas_slugs.json")
    bases = (here.parent.parent / "data", here.parent / "data")
    candidates = [base / name for name in names for base in bases]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def fold_text(value: str) -> str:
    text = value.lower().replace("ё", "е")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9а-я]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _slug_folded(slug: str) -> str:
    return fold_text(slug.replace("-", " "))


@lru_cache(maxsize=1)
def load_teas() -> list[dict[str, Any]]:
    path = _data_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "items" in raw:
        items = raw["items"]
    else:
        items = raw
    teas: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        slug = str(item["slug"])
        aliases = list(item.get("aliases") or [])
        aliases.extend(EXTRA_ALIASES.get(slug, []))
        teas.append({**item, "aliases": aliases})
    return teas


def known_tea_slugs() -> set[str]:
    """Allow-list of tea.support slugs in the local encyclopedia."""
    return {tea["slug"] for tea in load_teas()}


def china_green_slugs() -> set[str]:
    """Backward-compatible alias of known_tea_slugs (no longer green-only)."""
    return known_tea_slugs()


def resolve_query(query: str, limit: int) -> list[dict[str, Any]]:
    needle = fold_text(query)
    if not needle:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for tea in load_teas():
        blob_parts = [
            tea.get("slug") or "",
            _slug_folded(str(tea.get("slug") or "")),
            tea.get("name_en") or "",
            tea.get("name_ru") or "",
            tea.get("name_zh") or "",
            tea.get("pinyin") or "",
            *list(tea.get("aliases") or []),
        ]
        folded_parts = [fold_text(str(part)) for part in blob_parts if part]
        score = 0
        for part in folded_parts:
            if not part:
                continue
            if part == needle:
                score = max(score, 100)
            elif needle in part or part in needle:
                score = max(score, 80 if min(len(needle), len(part)) >= 4 else 50)
        if score == 0:
            tokens = set(needle.split())
            hay = set(" ".join(folded_parts).split())
            if tokens and tokens <= hay:
                score = 40 + 10 * len(tokens)
        if score:
            scored.append(
                (
                    score,
                    {
                        "slug": tea["slug"],
                        "name_en": tea.get("name_en"),
                        "name_ru": tea.get("name_ru"),
                        "name_zh": tea.get("name_zh"),
                        "pinyin": tea.get("pinyin"),
                        "tea_type": tea.get("tea_type"),
                        "score": score,
                    },
                )
            )
    scored.sort(key=lambda row: (-row[0], str(row[1]["slug"])))
    # Deduplicate slugs, keep best score
    seen: set[str] = set()
    matches: list[dict[str, Any]] = []
    for score, row in scored:
        slug = row["slug"]
        if slug in seen:
            continue
        seen.add(slug)
        matches.append(row)
        if len(matches) >= limit:
            break
    return matches
