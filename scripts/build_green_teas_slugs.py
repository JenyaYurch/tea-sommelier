"""Build data/green_teas_slugs.json from tea.support /teas (China green only)."""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "green_teas_slugs.json"
BASE = "https://api.thetea.app/api/v2/teas"
NAME_RE = re.compile(r"^(.*?)\s*\(([^,]+),\s*([^)]+)\)\s*$")

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
}


def fetch_page(offset: int, limit: int = 100) -> dict:
    url = f"{BASE}?{urllib.parse.urlencode({'tea_type': 'green', 'limit': limit, 'offset': offset})}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "tea-sommelier/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_name(name: str) -> tuple[str | None, str | None, str | None]:
    match = NAME_RE.match(name or "")
    if not match:
        return name or None, None, None
    return match.group(1).strip(), match.group(2).strip(), match.group(3).strip()


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
        if offset > 1000:
            break

    teas = []
    for item in items:
        if item.get("origin_country") != "CN":
            continue
        if item.get("category_code") != "CHINA-GREEN TEA":
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
    print(f"Wrote {len(teas)} teas to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
