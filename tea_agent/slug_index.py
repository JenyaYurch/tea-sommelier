"""Local slug dictionary for Chinese green teas (tea.support)."""

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
}


def _data_path() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / "data" / "green_teas_slugs.json",
        here.parent / "data" / "green_teas_slugs.json",
    ]
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


def china_green_slugs() -> set[str]:
    return {tea["slug"] for tea in load_teas()}


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
            overlap = tokens & hay
            if overlap and len(overlap) >= max(1, len(tokens) - 1):
                score = 40 + 10 * len(overlap)
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
