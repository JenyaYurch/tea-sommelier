"""Conservative match of a shop title to a Chinese green tea id.

Ids are tea.support slugs (``xihu-longjing``, ``biluochun``, …). A title is
kept only when it names one Chinese green. Teaware, other tea types, and
non-Chinese origins (Nepal Mao Feng, Formosa Pi Lo Chun, matcha) are dropped.
Ambiguous leftovers such as ``Qi Shan Mao Feng`` are dropped too.

``mao feng`` with no other place name maps to Huangshan Mao Feng, the tea
shops mean by that name. A different place (Yunnan, Emei, Qi Shan, Nepal)
does not.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from tea_agent.slug_index import load_teas

# Folded Latin phrases. ``generic`` aliases (bare Mao Feng) are rejected when
# another place name is still in the title.
_MANUAL: tuple[tuple[str, str, bool], ...] = (
    ("long jing", "xihu-longjing", False),
    ("longjing", "xihu-longjing", False),
    ("lung ching", "xihu-longjing", False),
    ("lungching", "xihu-longjing", False),
    ("lung jing", "xihu-longjing", False),
    ("dragon well", "xihu-longjing", False),
    ("dragonwell", "xihu-longjing", False),
    ("xi hu long jing", "xihu-longjing", False),
    ("xihu longjing", "xihu-longjing", False),
    ("xihu long jing", "xihu-longjing", False),
    ("west lake longjing", "xihu-longjing", False),
    ("west lake long jing", "xihu-longjing", False),
    ("shi feng long jing", "xihu-longjing", False),
    ("shifeng long jing", "xihu-longjing", False),
    ("da fo long jing", "dafu-long-jing", False),
    ("dafo long jing", "dafu-long-jing", False),
    ("da fu long jing", "dafu-long-jing", False),
    ("dafo longjing", "dafu-long-jing", False),
    ("da fo longjing", "dafu-long-jing", False),
    ("san xia long jing", "sanxia-long-jing", False),
    ("sanxia long jing", "sanxia-long-jing", False),
    ("sanxia longjing", "sanxia-long-jing", False),
    ("three gorges long jing", "sanxia-long-jing", False),
    ("yue xiang long jing", "yuexiang-long-jing", False),
    ("yuexiang longjing", "yuexiang-long-jing", False),
    ("bi luo chun", "biluochun", False),
    ("biluochun", "biluochun", False),
    ("pi lo chun", "biluochun", False),
    ("pilochun", "biluochun", False),
    ("pi luo chun", "biluochun", False),
    ("dong ting bi luo chun", "dongting-biluochun", False),
    ("dongting biluochun", "dongting-biluochun", False),
    ("dong ting biluochun", "dongting-biluochun", False),
    ("san xia bi luo chun", "sanxia-biluochun", False),
    ("sanxia bi luo chun", "sanxia-biluochun", False),
    ("sanxia biluochun", "sanxia-biluochun", False),
    ("huangshan mao feng", "huangshan-mao-feng", False),
    ("huang shan mao feng", "huangshan-mao-feng", False),
    ("huangshan maofeng", "huangshan-mao-feng", False),
    ("huang shan maofeng", "huangshan-mao-feng", False),
    ("mao feng", "huangshan-mao-feng", True),
    ("maofeng", "huangshan-mao-feng", True),
    ("yunnan mao feng", "yun-nan-mao-feng", False),
    ("yun nan mao feng", "yun-nan-mao-feng", False),
    ("yunnan maofeng", "yun-nan-mao-feng", False),
    ("emei mao feng", "emei-mao-feng", False),
    ("emei maofeng", "emei-mao-feng", False),
    ("mengding mao feng", "mengding-mao-feng", False),
    ("meng ding mao feng", "mengding-mao-feng", False),
    ("mengding maofeng", "mengding-mao-feng", False),
    ("yandang mao feng", "yandang-mao-feng", False),
    ("yan dang mao feng", "yandang-mao-feng", False),
    ("tai ping hou kui", "taiping-hou-kui", False),
    ("taiping houkui", "taiping-hou-kui", False),
    ("tai ping houkui", "taiping-hou-kui", False),
    ("taiping hou kui", "taiping-hou-kui", False),
    ("hou kui", "taiping-hou-kui", False),
    ("houkui", "taiping-hou-kui", False),
    ("hou kwei", "taiping-hou-kui", False),
    ("anji bai cha", "anji-baicha", False),
    ("anji baicha", "anji-baicha", False),
    ("anji white tea", "anji-baicha", False),
    ("anji biala", "anji-baicha", False),
    ("anji bai cha mao feng", "anji-baicha", False),
    ("anji baicha mao feng", "anji-baicha", False),
    ("anji bai cha maofeng", "anji-baicha", False),
    ("liu an gua pian", "liu-an-guapian", False),
    ("liuan guapian", "liu-an-guapian", False),
    ("lu an gua pian", "liu-an-guapian", False),
    ("luan gua pian", "liu-an-guapian", False),
    ("gua pian", "liu-an-guapian", False),
    ("guapian", "liu-an-guapian", False),
    ("melon seed tea", "liu-an-guapian", False),
    ("lu shan yun wu", "lushan-yun-wu", False),
    ("lushan yunwu", "lushan-yun-wu", False),
    ("lushan yun wu", "lushan-yun-wu", False),
    ("lu shan yunwu", "lushan-yun-wu", False),
    ("zhu ye qing", "zhu-ye-qing", False),
    ("zhuyeqing", "zhu-ye-qing", False),
    ("meng ding gan lu", "mengding-gan-lu", False),
    ("mengding ganlu", "mengding-gan-lu", False),
    ("mengding gan lu", "mengding-gan-lu", False),
    ("xin yang mao jian", "xinyang-mao-jian", False),
    ("xinyang maojian", "xinyang-mao-jian", False),
    ("xinyang mao jian", "xinyang-mao-jian", False),
    ("gunpowder", "pingshui-zhucha", False),
    ("gun powder", "pingshui-zhucha", False),
    ("ping shui zhu cha", "pingshui-zhucha", False),
    ("chun mee", "meicha", False),
    ("chunmee", "meicha", False),
    ("zhen mei", "meicha", False),
    ("zhenmei", "meicha", False),
    ("huangshan yun wu", "huangshan-yun-wu-cha", False),
    ("huang shan yun wu", "huangshan-yun-wu-cha", False),
    ("en shi yu lu", "enshi-yulu", False),
    ("enshi yulu", "enshi-yulu", False),
    ("jasmine pearl", "moli-longzhu", False),
    ("jasmine pearls", "moli-longzhu", False),
    ("jasmine dragon pearl", "moli-longzhu", False),
    ("dragon pearl", "moli-longzhu", False),
    ("mo li long zhu", "moli-longzhu", False),
    ("moli longzhu", "moli-longzhu", False),
)

_MANUAL_CJK: tuple[tuple[str, str], ...] = (
    ("龙井", "xihu-longjing"),
    ("西湖龙井", "xihu-longjing"),
    ("大佛龙井", "dafu-long-jing"),
    ("碧螺春", "biluochun"),
    ("洞庭碧螺春", "dongting-biluochun"),
    ("黄山毛峰", "huangshan-mao-feng"),
    ("太平猴魁", "taiping-hou-kui"),
    ("安吉白茶", "anji-baicha"),
    ("六安瓜片", "liu-an-guapian"),
    ("庐山云雾", "lushan-yun-wu"),
    ("竹叶青", "zhu-ye-qing"),
    ("蒙顶甘露", "mengding-gan-lu"),
    ("信阳毛尖", "xinyang-mao-jian"),
    ("云南毛峰", "yun-nan-mao-feng"),
)

# When a general tea matched, a place token in the title picks the narrower id.
_REFINEMENTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("biluochun", ("dongting",), "dongting-biluochun"),
    ("biluochun", ("dong", "ting"), "dongting-biluochun"),
    ("biluochun", ("sanxia",), "sanxia-biluochun"),
    ("biluochun", ("san", "xia"), "sanxia-biluochun"),
    ("xihu-longjing", ("dafo",), "dafu-long-jing"),
    ("xihu-longjing", ("da", "fo"), "dafu-long-jing"),
    ("xihu-longjing", ("da", "fu"), "dafu-long-jing"),
    ("xihu-longjing", ("sanxia",), "sanxia-long-jing"),
    ("xihu-longjing", ("san", "xia"), "sanxia-long-jing"),
    ("xihu-longjing", ("yuexiang",), "yuexiang-long-jing"),
    ("xihu-longjing", ("yue", "xiang"), "yuexiang-long-jing"),
    ("huangshan-mao-feng", ("yunnan",), "yun-nan-mao-feng"),
    ("huangshan-mao-feng", ("yun", "nan"), "yun-nan-mao-feng"),
    ("huangshan-mao-feng", ("emei",), "emei-mao-feng"),
    ("huangshan-mao-feng", ("mengding",), "mengding-mao-feng"),
    ("huangshan-mao-feng", ("meng", "ding"), "mengding-mao-feng"),
    ("huangshan-mao-feng", ("yandang",), "yandang-mao-feng"),
    ("huangshan-mao-feng", ("yan", "dang"), "yandang-mao-feng"),
)

_FILLERS = frozenset(
    {
        "organic",
        "bio",
        "demeter",
        "naturland",
        "green",
        "tea",
        "teas",
        "tee",
        "tees",
        "herbata",
        "herbaty",
        "herbate",
        "herbat",
        "china",
        "chinese",
        "chinesisch",
        "chinesische",
        "chinesischer",
        "chinski",
        "chinska",
        "chinskie",
        "chinskich",
        "gruner",
        "gruntee",
        "gruener",
        "zielona",
        "zielonej",
        "zielony",
        "zielone",
        "spring",
        "harvest",
        "ernte",
        "wiosna",
        "fruhling",
        "fruehling",
        "ming",
        "qian",
        "mingqian",
        "pre",
        "qingming",
        "premium",
        "imperial",
        "classic",
        "special",
        "extra",
        "selected",
        "choice",
        "loose",
        "leaf",
        "leaves",
        "sypana",
        "sypanej",
        "sypany",
        "handgemacht",
        "handmade",
        "first",
        "flush",
        "early",
        "new",
        "fresh",
        "garden",
        "natural",
        "the",
        "and",
        "from",
        "aus",
        "von",
        "of",
        "de",
        "di",
        "z",
        "w",
        "na",
        "do",
        "year",
        "rocznik",
        "jahrgang",
        "grade",
        "aaa",
        "aa",
        "high",
    }
)

_ORIGIN_PHRASES: tuple[tuple[str, ...], ...] = (
    ("nepal",),
    ("nepalski",),
    ("nepalska",),
    ("nepalskie",),
    ("india",),
    ("indien",),
    ("indyjska",),
    ("indyjski",),
    ("indyjskie",),
    ("darjeeling",),
    ("assam",),
    ("ceylon",),
    ("sri", "lanka"),
    ("japan",),
    ("japanese",),
    ("japon",),
    ("japonska",),
    ("japonski",),
    ("japonskie",),
    ("japonia",),
    ("korea",),
    ("korean",),
    ("koreanska",),
    ("koreanski",),
    ("vietnam",),
    ("wietnam",),
    ("wietnamska",),
    ("wietnamski",),
    ("taiwan",),
    ("tajwan",),
    ("formosa",),
    ("taiwanese",),
    ("georgia",),
    ("gruzja",),
    ("gruzinski",),
    ("kenya",),
    ("matcha",),
    ("sencha",),
    ("gyokuro",),
    ("hojicha",),
    ("genmaicha",),
    ("bancha",),
    ("kukicha",),
)

_TYPE_PHRASES: tuple[tuple[str, ...], ...] = (
    ("oolong",),
    ("wulong",),
    ("ulun",),
    ("puer",),
    ("puerh",),
    ("pu", "erh"),
    ("pu", "er"),
    ("hong", "cha"),
    ("hongcha",),
    ("hong", "mei"),
    ("red", "tea"),
    ("black", "tea"),
    ("schwarzer", "tee"),
    ("schwarztee",),
    ("roter", "tee"),
    ("czerwona", "herbata"),
    ("czerwony",),
    ("czerwona",),
    ("czerwonej",),
    ("yellow", "tea"),
    ("gelber", "tee"),
    ("zolta", "herbata"),
    ("huang", "ya"),
    ("dian", "hong"),
    ("dianhong",),
    ("jin", "jun", "mei"),
    ("keemun",),
    ("qimen",),
    ("lapsang",),
    ("souchong",),
    ("dancong",),
    ("dan", "cong"),
    ("yan", "cha"),
    ("yancha",),
    ("tie", "guan", "yin"),
    ("da", "hong", "pao"),
    ("gaba",),
    ("bai", "mu", "dan"),
    ("baimudan",),
    ("silver", "needle"),
    ("baihao", "yinzhen"),
    ("white", "tea"),
    ("weisser", "tee"),
    ("weisse", "tee"),
    ("biala", "herbata"),
)

_WHITE_TOKENS = frozenset({"white", "weiss", "weisser", "weisse", "biala", "bialy"})

_WARE_PHRASES: tuple[tuple[str, ...], ...] = (
    ("gaiwan",),
    ("guywan",),
    ("teapot",),
    ("tea", "pot"),
    ("teekanne",),
    ("teekannen",),
    ("czajnik",),
    ("czajniczek",),
    ("dzbanek",),
    ("yixing",),
    ("czarka",),
    ("czarki",),
    ("filizanka",),
    ("filizanki",),
    ("teetasse",),
    ("teeschale",),
    ("teeschalen",),
    ("tasse",),
    ("tassen",),
    ("geschirr",),
    ("teegeschirr",),
    ("zubehor",),
    ("zubehoer",),
    ("akcesoria",),
    ("akcesorium",),
    ("teaware",),
    ("tea", "ware"),
    ("zaparzacz",),
    ("infuser",),
    ("teedose",),
    ("teedosen",),
    ("tea", "caddy"),
    ("caddy",),
    ("puszka",),
    ("puszki",),
    ("chawan",),
    ("chaxi",),
    ("porzellan",),
    ("porcelain",),
    ("ceramika",),
    ("ceramic",),
    ("keramik",),
    ("kubek",),
    ("kubki",),
    ("pitcher",),
)

_TRAD_TO_SIMP = str.maketrans(
    {
        "黃": "黄",
        "龍": "龙",
        "盧": "卢",
        "廬": "庐",
        "雲": "云",
        "霧": "雾",
        "葉": "叶",
        "綠": "绿",
        "陽": "阳",
        "銀": "银",
        "針": "针",
        "鳳": "凤",
        "龜": "龟",
        "門": "门",
        "東": "东",
        "島": "岛",
        "絲": "丝",
        "馬": "马",
        "烏": "乌",
        "長": "长",
        "華": "华",
        "國": "国",
        "寶": "宝",
        "紅": "红",
        "壺": "壶",
        "頭": "头",
        "陳": "陈",
        "嶺": "岭",
        "峯": "峰",
    }
)

_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_GENERIC_AUTO_TOKENS = frozenset(
    {"green", "tea", "teas", "tee", "china", "chinese", "lu", "cha", "lv"}
)
_MAX_FILLER_GAP = 3


@dataclass(frozen=True)
class _Alias:
    tea_id: str
    tokens: tuple[str, ...]
    generic: bool

    @property
    def score(self) -> tuple[int, int]:
        return (len(self.tokens), sum(len(token) for token in self.tokens))


@dataclass(frozen=True)
class _Hit:
    tea_id: str
    score: tuple[int, int]
    span: tuple[int, int] | None


def fold_latin(value: str) -> str:
    """Lowercase Latin fold. Polish ł and German ß are kept as letters."""
    text = value.replace("ß", "ss").replace("Ł", "L").replace("ł", "l")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def cjk_norm(value: str) -> str:
    """Map a few traditional characters so 黃山毛峰 matches 黄山毛峰."""
    return value.translate(_TRAD_TO_SIMP)


def match_chinese_green(title: str, context: str = "") -> str | None:
    """Return a Chinese green slug, or None when the title is not one tea."""
    if not title or not str(title).strip():
        return None
    tokens = fold_latin(title).split()
    ctx_tokens = fold_latin(f"{title} {context}").split()
    if _contains_any(ctx_tokens, _ORIGIN_PHRASES):
        return None
    if _contains_any(ctx_tokens, _WARE_PHRASES):
        return None
    if _contains_any(ctx_tokens, _TYPE_PHRASES):
        return None
    white = bool(set(ctx_tokens) & _WHITE_TOKENS) or _contains_any(
        ctx_tokens, (("white", "tea"), ("biala", "herbata"), ("weisser", "tee"))
    )
    latin, cjk = _indexes()
    latin_hit = _best_latin(tokens, latin)
    cjk_hit = _best_cjk(cjk_norm(title), cjk)
    chosen = _choose(latin_hit, cjk_hit)
    if chosen is None:
        return None
    tea_id = _refine(chosen, tokens)
    if white and tea_id != "anji-baicha":
        return None
    return tea_id


def manual_tea_ids() -> set[str]:
    """Slugs named by the hand-written alias table."""
    ids = {tea_id for _, tea_id, _ in _MANUAL}
    ids.update(tea_id for _, tea_id in _MANUAL_CJK)
    ids.update(refined for _, _, refined in _REFINEMENTS)
    return ids


def encyclopedia_green_ids() -> set[str]:
    """Chinese green slugs from the local tea.support list. Taiwanese greens are out."""
    ids: set[str] = set()
    for tea in load_teas():
        slug = str(tea.get("slug") or "")
        if tea.get("tea_type") != "green" or tea.get("origin_country") != "CN":
            continue
        if slug.startswith("taiwan") or "taiwan" in slug:
            continue
        ids.add(slug)
    return ids


def _refine(tea_id: str, tokens: list[str]) -> str:
    token_set = set(tokens)
    for base, key, refined in _REFINEMENTS:
        if tea_id == base and set(key) <= token_set:
            return refined
    return tea_id


def _choose(latin: _Hit | None, cjk: _Hit | None) -> str | None:
    if latin is None and cjk is None:
        return None
    if latin is None:
        return cjk.tea_id if cjk else None
    if cjk is None:
        return latin.tea_id
    if latin.tea_id == cjk.tea_id:
        return latin.tea_id
    if latin.score[0] == cjk.score[0]:
        return None
    return latin.tea_id if latin.score > cjk.score else cjk.tea_id


def _best_latin(tokens: list[str], aliases: tuple[_Alias, ...]) -> _Hit | None:
    hits: list[tuple[_Alias, tuple[int, int]]] = []
    for alias in aliases:
        span = _find_span(tokens, alias.tokens)
        if span is None:
            continue
        if alias.generic and not _generic_ok(alias.tea_id, tokens, span):
            continue
        hits.append((alias, span))
    if not hits:
        return None
    hits.sort(key=lambda item: (item[0].score, item[0].tea_id), reverse=True)
    best_alias, best_span = hits[0]
    for alias, span in hits[1:]:
        if alias.tea_id == best_alias.tea_id:
            continue
        if alias.score == best_alias.score or not _overlaps(span, best_span):
            return None
    return _Hit(best_alias.tea_id, best_alias.score, best_span)


def _best_cjk(title: str, aliases: tuple[tuple[str, str, int], ...]) -> _Hit | None:
    hits: list[tuple[str, int]] = []
    for tea_id, text, length in aliases:
        if text and text in title:
            hits.append((tea_id, length))
    if not hits:
        return None
    hits.sort(key=lambda item: (item[1], item[0]), reverse=True)
    best_id, best_len = hits[0]
    for tea_id, length in hits[1:]:
        if tea_id != best_id and length == best_len:
            return None
    return _Hit(best_id, (best_len, best_len), None)


def _generic_ok(tea_id: str, tokens: list[str], span: tuple[int, int]) -> bool:
    residual = [
        token
        for index, token in enumerate(tokens)
        if not (span[0] <= index < span[1])
        and token not in _FILLERS
        and not _YEAR.match(token)
        and len(token) > 1
    ]
    if not residual:
        return True
    residual_set = set(residual)
    for base, key, _refined in _REFINEMENTS:
        if base == tea_id and set(key) == residual_set:
            return True
    return False


def _find_span(hay: list[str], alias: tuple[str, ...]) -> tuple[int, int] | None:
    """Alias tokens in order. Filler words may sit between them (at most a few)."""
    if not alias:
        return None
    first = alias[0]
    for start, token in enumerate(hay):
        if token != first:
            continue
        pos = start
        ok = True
        for needed in alias[1:]:
            pos += 1
            gap = 0
            while pos < len(hay) and hay[pos] != needed:
                if hay[pos] not in _FILLERS or gap >= _MAX_FILLER_GAP:
                    ok = False
                    break
                gap += 1
                pos += 1
            if not ok or pos >= len(hay) or hay[pos] != needed:
                ok = False
                break
        if ok:
            return (start, pos + 1)
    return None


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _contains_any(tokens: list[str], phrases: tuple[tuple[str, ...], ...]) -> bool:
    if not tokens:
        return False
    token_set = set(tokens)
    for phrase in phrases:
        if len(phrase) == 1:
            if phrase[0] in token_set:
                return True
            continue
        width = len(phrase)
        for index in range(len(tokens) - width + 1):
            if tuple(tokens[index : index + width]) == phrase:
                return True
    return False


def _usable_auto(phrase: str) -> bool:
    tokens = phrase.split()
    if not tokens or set(tokens) <= _GENERIC_AUTO_TOKENS:
        return False
    if any(len(token) < 2 for token in tokens):
        return False
    if len(tokens) >= 2 and len(phrase) >= 12:
        return True
    return len(tokens) == 1 and len(phrase) >= 8


@lru_cache(maxsize=1)
def _indexes() -> tuple[tuple[_Alias, ...], tuple[tuple[str, str, int], ...]]:
    green_ids = encyclopedia_green_ids()
    missing = manual_tea_ids() - green_ids
    if missing:
        joined = ", ".join(sorted(missing))
        raise RuntimeError(f"manual tea ids are not Chinese greens: {joined}")

    latin: dict[tuple[str, ...], _Alias] = {}
    for phrase, tea_id, generic in _MANUAL:
        tokens = tuple(fold_latin(phrase).split())
        if tokens:
            latin[tokens] = _Alias(tea_id, tokens, generic)

    claimed = {" ".join(tokens) for tokens in latin}
    auto_bucket: dict[str, set[str]] = {}
    cjk_bucket: dict[str, set[str]] = {}
    for tea in load_teas():
        slug = str(tea.get("slug") or "")
        if slug not in green_ids:
            continue
        for key in ("name_en", "pinyin"):
            phrase = fold_latin(str(tea.get(key) or ""))
            if _usable_auto(phrase) and phrase not in claimed:
                auto_bucket.setdefault(phrase, set()).add(slug)
        slug_phrase = fold_latin(slug.replace("-", " "))
        if _usable_auto(slug_phrase) and slug_phrase not in claimed:
            auto_bucket.setdefault(slug_phrase, set()).add(slug)
        zh = cjk_norm(str(tea.get("name_zh") or ""))
        zh = "".join(ch for ch in zh if "\u4e00" <= ch <= "\u9fff")
        if len(zh) >= 2:
            cjk_bucket.setdefault(zh, set()).add(slug)

    for phrase, slugs in auto_bucket.items():
        if len(slugs) != 1:
            continue
        tokens = tuple(phrase.split())
        latin[tokens] = _Alias(next(iter(slugs)), tokens, False)

    cjk: dict[str, str] = {}
    for text, tea_id in _MANUAL_CJK:
        normalized = "".join(
            ch for ch in cjk_norm(text) if "\u4e00" <= ch <= "\u9fff"
        )
        if len(normalized) >= 2:
            cjk[normalized] = tea_id
    for text, slugs in cjk_bucket.items():
        if text in cjk or len(slugs) != 1:
            continue
        cjk[text] = next(iter(slugs))

    cjk_rows = tuple(
        (tea_id, text, len(text))
        for text, tea_id in sorted(cjk.items(), key=lambda item: len(item[0]), reverse=True)
    )
    return tuple(latin.values()), cjk_rows
