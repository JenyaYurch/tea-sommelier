"""Build data/tea_slugs.json from tea.support /teas (Chinese tea types in v1 scope)."""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "tea_slugs.json"
BASE = "https://api.thetea.app/api/v2/teas"
NAME_RE = re.compile(r"^(.*?)\s*\(([^,]+),\s*([^)]+)\)\s*$")

# v1 encyclopedia: white / yellow / green / red / puer (sheng+shu) / oolong.
# GABA is not a tea.support tea_type (smoke 2026-09: 0 slugs); shop still carries it.
# Dark / flowers stay out of the allow-list.
SCOPE_TEA_TYPES = frozenset({"white", "yellow", "green", "red", "puer", "oolong"})

EXTRA_ALIASES: dict[str, list[str]] = {
    "xihu-longjing": ["Лунцзин", "Лун Цзин", "Си Ху Лун Цзин", "Longjing", "Dragon Well"],
    "biluochun": ["Би Ло Чунь", "Билочунь", "Дунтин Би Ло Чунь", "Bi Luo Chun"],
    "anji-baicha": ["Аньцзи Бай Ча", "Аньцзи Байча", "Anji Bai Cha"],
    "taiping-hou-kui": ["Тай Пин Хоу Куй", "Тайпин Хоукуй", "Taiping Houkui"],
    "liu-an-guapian": ["Люань Гуапянь", "Лю Ань Гуа Пянь", "Liu An Gua Pian"],
    "zhu-ye-qing": ["Чжу Е Цин", "Чжуецин", "Zhu Ye Qing"],
    "mengding-gan-lu": ["Мэндин Гань Лу", "Mengding Ganlu"],
    "xinyang-mao-jian": ["Синь Ян Мао Цзянь", "Синьян Маоцзянь", "Xinyang Maojian"],
    "lushan-yun-wu": ["Лушань Юнь У", "Lushan Yunwu"],
    "huangshan-mao-feng": ["Хуаншань Мао Фэн", "Huangshan Maofeng"],
    "moli-longzhu": ["Моли Лун Чжу", "Жасминовая Жемчужина Дракона", "Moli Longzhu"],
    "bai-mudan": ["Бай Му Дань", "Баймудань", "Bai Mudan", "White Peony"],
    "baihao-yinzhen": ["Инь Чжэнь", "Бай Хао Инь Чжэнь", "Baihao Yinzhen", "Silver Needle"],
    "junshan-yin-zhen": ["Цзюнь Шань Инь Чжэнь", "Цзюньшань Инь Чжэнь", "Junshan Yinzhen"],
    "dianhong-gongfu": ["Дянь Хун", "Дяньхун", "Dian Hong", "Dianhong"],
    "7542": ["шен пуэр", "шэн пуэр", "шен пуер", "sheng puerh", "sheng pu-erh"],
    "7572-shu-bing": ["шу пуэр", "шу пуер", "shu puerh", "shu pu-erh", "ripe puerh"],
    "dong-ding-wulong": ["Дун Дин", "Дундин", "Dong Ding", "Tung Ting"],
}


def fetch_page(offset: int, limit: int = 100) -> dict:
    url = f"{BASE}?{urllib.parse.urlencode({'limit': limit, 'offset': offset})}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "tea-sommelier/0.1"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_name(name: str) -> tuple[str | None, str | None, str | None]:
    match = NAME_RE.match(name or "")
    if not match:
        return name or None, None, None
    return match.group(1).strip(), match.group(2).strip(), match.group(3).strip()


def in_scope(item: dict) -> bool:
    return item.get("tea_type") in SCOPE_TEA_TYPES


def main() -> int:
    items: list[dict] = []
    offset = 0
    while True:
        payload = fetch_page(offset)
        batch = payload.get("items") or []
        if not batch:
            break
        items.extend(batch)
        offset += len(batch)
        if len(batch) < 100:
            break
        if offset > 5000:
            break

    teas = []
    for item in items:
        if not in_scope(item):
            continue
        slug = item["slug"]
        name_en, name_zh, pinyin = parse_name(item.get("name") or "")
        aliases = list(EXTRA_ALIASES.get(slug, []))
        teas.append(
            {
                "slug": slug,
                "name_en": name_en,
                "name_ru": aliases[0] if aliases else None,
                "name_zh": name_zh,
                "pinyin": pinyin,
                "aliases": aliases,
                "tea_type": item.get("tea_type"),
                "origin_country": item.get("origin_country"),
                "category_code": item.get("category_code"),
                "province": item.get("province"),
            }
        )

    teas.sort(key=lambda row: row["slug"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(teas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_type: dict[str, int] = {}
    for row in teas:
        key = str(row.get("tea_type") or "unknown")
        by_type[key] = by_type.get(key, 0) + 1
    print(f"Wrote {len(teas)} teas to {OUT} ({by_type})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
