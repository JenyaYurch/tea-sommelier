"""Conservative match of a shop title to one Chinese tea.

Ids are tea.support slugs. A title is kept only when it names one tea from
China or Taiwan: green, white, yellow, oolong, black (hong cha), pu-erh, or
a jasmine / osmanthus / ginseng-scented tea. Teaware, herbal infusions,
flavoured blends, gift sets, and non-Chinese origins (Japan, India, Nepal,
Ceylon, Kenya, …) are dropped. Ambiguous leftovers such as ``Qi Shan Mao
Feng`` are dropped too.

``mao feng`` with no other place name maps to Huangshan Mao Feng. A different
place (Yunnan, Emei, Qi Shan, Nepal) does not. A type word outside the tea
name (``Huangshan Mao Feng Hong Cha``, ``Czerwony Long Jing``) drops the row
instead of filing it under the green tea.
"""

from __future__ import annotations

import itertools
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
    ("an ji bai cha", "anji-baicha", False),
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
    ("jasmin pearl", "moli-longzhu", False),
    ("jasmin pearls", "moli-longzhu", False),
    ("jasmine dragon pearl", "moli-longzhu", False),
    ("jasmine dragon pearls", "moli-longzhu", False),
    ("jasmin dragon pearl", "moli-longzhu", False),
    ("jasmin dragon pearls", "moli-longzhu", False),
    ("mo li long zhu", "moli-longzhu", False),
    ("moli longzhu", "moli-longzhu", False),
    ("tie guan yin", "tie-guanyin", False),
    ("tieguanyin", "tie-guanyin", False),
    ("iron goddess", "tie-guanyin", False),
    ("da hong pao", "da-hong-pao", False),
    ("dahongpao", "da-hong-pao", False),
    ("big red robe", "da-hong-pao", False),
    ("dong ding", "dong-ding-wulong", False),
    ("dongding", "dong-ding-wulong", False),
    ("tung ting", "dong-ding-wulong", False),
    ("ali shan hong cha", "alishan-hongcha", False),
    ("alishan hong cha", "alishan-hongcha", False),
    ("ali shan", "alishan-wulong", False),
    ("alishan", "alishan-wulong", False),
    ("dong fang mei ren", "dongfang-meiren", False),
    ("dongfang meiren", "dongfang-meiren", False),
    ("oriental beauty", "dongfang-meiren", False),
    ("jin xuan", "jin-xuan", False),
    ("jinxuan", "jin-xuan", False),
    ("nai xiang", "nai-xiang-wulong", False),
    ("naixiang", "nai-xiang-wulong", False),
    ("rou gui", "rou-gui", False),
    ("rougui", "rou-gui", False),
    ("shui xian", "shui-xian", False),
    ("shuixian", "shui-xian", False),
    ("bao zhong", "wenshan-baozhong-cha", False),
    ("baozhong", "wenshan-baozhong-cha", False),
    ("pouchong", "wenshan-baozhong-cha", False),
    ("bai mu dan", "bai-mudan", False),
    ("baimudan", "bai-mudan", False),
    ("pai mu tan", "bai-mudan", False),
    ("white peony", "bai-mudan", False),
    ("bai hao yin zhen", "baihao-yinzhen", False),
    ("silver needle", "baihao-yinzhen", False),
    ("srebrne szpilki", "baihao-yinzhen", False),
    ("shou mei", "shou-mei", False),
    ("shoumei", "shou-mei", False),
    ("jin jun mei", "jin-jun-mei", False),
    ("jinjunmei", "jin-jun-mei", False),
    ("dian hong", "dianhong-gongfu", False),
    ("dianhong", "dianhong-gongfu", False),
    ("keemun", "qimen-hongcha", False),
    ("qimen", "qimen-hongcha", False),
    ("qi hong", "qimen-hongcha", False),
    ("qi men", "qimen-hongcha", False),
    ("zheng shan xiao zhong", "tongmu-yesheng-zheng-shan-xiao-zhong", False),
    ("lapsang souchong", "tongmu-yesheng-zheng-shan-xiao-zhong", False),
    ("lapsang", "tongmu-yesheng-zheng-shan-xiao-zhong", False),
    ("jun shan yin zhen", "junshan-yin-zhen", False),
    ("junshan yinzhen", "junshan-yin-zhen", False),
    ("huo shan huang ya", "huoshan-huang-ya", False),
    ("huoshan huang ya", "huoshan-huang-ya", False),
    ("meng ding huang ya", "mengding-huang-ya", False),
    ("lao cha tou", "lao-cha-tou", False),
    ("gong ting", "gongting-pu-er", False),
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
    ("铁观音", "tie-guanyin"),
    ("大红袍", "da-hong-pao"),
    ("白牡丹", "bai-mudan"),
    ("白毫银针", "baihao-yinzhen"),
    ("金骏眉", "jin-jun-mei"),
    ("寿眉", "shou-mei"),
    ("东方美人", "dongfang-meiren"),
    ("冻顶", "dong-ding-wulong"),
    ("茉莉龙珠", "moli-longzhu"),
    ("君山银针", "junshan-yin-zhen"),
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
    ("tie-guanyin", ("muzha",), "muzha-tieguanyin"),
    ("baihao-yinzhen", ("junshan",), "junshan-yin-zhen"),
    ("baihao-yinzhen", ("jun", "shan"), "junshan-yin-zhen"),
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
    ("georgia",),
    ("gruzja",),
    ("gruzinski",),
    ("kenya",),
    ("kenia",),
    ("sikkim",),
    ("turkei",),
    ("tuerkei",),
    ("sudkorea",),
    ("matcha",),
    ("sencha",),
    ("gyokuro",),
    ("hojicha",),
    ("houjicha",),
    ("genmaicha",),
    ("bancha",),
    ("kukicha",),
    ("kabusecha",),
    ("wakocha",),
    ("wakoucha",),
    ("tencha",),
    ("kamairicha",),
)

# A type word that sits outside the matched name. ``Hong Cha`` after Mao Feng
# means the row is not the green tea.
_CATEGORY_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("green", ("green", "tea")),
    ("green", ("gruner", "tee")),
    ("green", ("gruener", "tee")),
    ("green", ("zielona", "herbata")),
    ("green", ("herbata", "zielona")),
    ("green", ("lu", "cha")),
    ("white", ("white", "tea")),
    ("white", ("weisser", "tee")),
    ("white", ("weisse", "tee")),
    ("white", ("biala", "herbata")),
    ("white", ("herbata", "biala")),
    ("white", ("white",)),
    ("white", ("weiss",)),
    ("white", ("biala",)),
    ("white", ("bialy",)),
    ("yellow", ("yellow", "tea")),
    ("yellow", ("gelber", "tee")),
    ("yellow", ("zolta", "herbata")),
    ("yellow", ("huang", "cha")),
    ("oolong", ("oolong",)),
    ("oolong", ("wulong",)),
    ("oolong", ("ulun",)),
    ("black", ("hong", "cha")),
    ("black", ("hongcha",)),
    ("black", ("hong", "mei")),
    ("black", ("red", "tea")),
    ("black", ("black", "tea")),
    ("black", ("schwarzer", "tee")),
    ("black", ("schwarztee",)),
    ("black", ("roter", "tee")),
    ("black", ("czerwona", "herbata")),
    ("black", ("czerwony",)),
    ("black", ("czerwona",)),
    ("black", ("czerwonej",)),
    ("black", ("czarna",)),
    ("black", ("czarny",)),
    ("black", ("czarne",)),
    ("black", ("czarnej",)),
    ("black", ("schwarz",)),
    ("black", ("schwarzer",)),
    ("yellow", ("zolta",)),
    ("yellow", ("zolty",)),
    ("yellow", ("zolte",)),
    ("yellow", ("zoltej",)),
    ("puerh", ("puer",)),
    ("puerh", ("puerh",)),
    ("puerh", ("pu", "erh")),
    ("puerh", ("pu", "er")),
    ("dark", ("hei", "cha")),
    ("dark", ("heicha",)),
    ("dark", ("dark", "tea")),
)

_FLAVOUR_PHRASES: tuple[tuple[str, ...], ...] = (
    ("earl", "grey"),
    ("english", "breakfast"),
    ("aromatyzowana",),
    ("aromatyzowany",),
    ("aromatyzowane",),
    ("aromatisiert",),
    ("aromatisierte",),
    ("aromatisierter",),
    ("flavoured",),
    ("flavored",),
    ("flavour",),
    ("flavor",),
    ("erdbeer",),
    ("erdbeere",),
    ("strawberry",),
    ("truskawka",),
    ("truskawkowa",),
    ("mango",),
    ("pfirsich",),
    ("peach",),
    ("brzoskwinia",),
    ("vanille",),
    ("vanilla",),
    ("wanilia",),
    ("waniliowa",),
    ("bergamot",),
    ("bergamotte",),
    ("bergamotka",),
    ("zitrone",),
    ("lemon",),
    ("cytryna",),
    ("cytrynowa",),
    ("minze",),
    ("mint",),
    ("mieta",),
    ("himbeer",),
    ("raspberry",),
    ("malina",),
    ("kirsche",),
    ("cherry",),
    ("kokos",),
    ("coconut",),
    ("karamell",),
    ("caramel",),
    ("schoko",),
    ("chocolate",),
    ("czekolada",),
)

_HERBAL_PHRASES: tuple[tuple[str, ...], ...] = (
    ("rooibos",),
    ("yerba",),
    ("mate",),
    ("chamomile",),
    ("rumianek",),
    ("kamille",),
    ("krautertee",),
    ("herbal",),
    ("tisane",),
    ("hibiskus",),
    ("hibiscus",),
    ("melisa",),
    ("ziolowa",),
    ("ziolowy",),
    ("ziolowe",),
)

_GIFT_PHRASES: tuple[tuple[str, ...], ...] = (
    ("gift", "set"),
    ("tee", "set"),
    ("tea", "set"),
    ("teeset",),
    ("tastingset",),
    ("tasting", "set"),
    ("geschenkset",),
    ("probierset",),
    ("probierpaket",),
    ("vorteilsset",),
    ("degustationsset",),
    ("zestaw",),
    ("sampler",),
    ("set",),
    ("sets",),
    ("adventskalender",),
)

_EVENT_PHRASES: tuple[tuple[str, ...], ...] = (
    ("warsztaty",),
    ("warsztat",),
    ("workshop",),
    ("seminar",),
    ("seminarium",),
)

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
        "鐵": "铁",
        "觀": "观",
        "凍": "冻",
        "頂": "顶",
        "壽": "寿",
    }
)

_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_GENERIC_AUTO_TOKENS = frozenset(
    {
        "green",
        "tea",
        "teas",
        "tee",
        "china",
        "chinese",
        "lu",
        "cha",
        "lv",
        "oolong",
        "wulong",
        "puer",
        "puerh",
        "hong",
        "red",
        "black",
        "white",
        "yellow",
        "bai",
        "hei",
    }
)

_IN_SCOPE_TYPES = frozenset({"green", "white", "yellow", "oolong", "red", "puer"})
_TYPE_CATEGORY = {
    "green": "green",
    "white": "white",
    "yellow": "yellow",
    "oolong": "oolong",
    "red": "black",
    "puer": "puerh",
}
_TAIWAN_MARKERS = frozenset(
    {"taiwan", "tajwan", "formosa", "taiwanese", "taipei", "tajpej"}
)
_TAIWAN_SLUG_MARKERS = (
    "taiwan",
    "formosa",
    "alishan",
    "dong-ding",
    "da-yu-ling",
    "li-shan",
    "muzha",
    "dongfang-meiren",
    "shan-lin",
    "shanlinxi",
    "taicha",
    "wenshan-baozhong",
    "jin-xuan",
    "nai-xiang",
    "cuiyu",
    "guifei",
    "he-huan",
    "hongshui",
    "qi-lai",
    "riyuetan",
    "sijichun",
)
_DARK_SLUG_MARKERS = ("liu-bao", "liubao", "fu-zhuan", "fuzhuan", "tian-jian", "hei-cha", "heicha", "qianliang")
_SCENT_SLUG_MARKERS = ("moli", "jasmine", "jasmin", "guihua", "osmanthus", "renshen")
_SCENT_TOKENS = frozenset(
    {
        "moli",
        "jasmine",
        "jasmin",
        "jasminowa",
        "jasminowy",
        "jasminowe",
        "osmanthus",
        "guihua",
        "ginseng",
        "renshen",
        "zenszen",
    }
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


@dataclass(frozen=True)
class TeaMatch:
    """One canonical tea and the shelf category a later price tool should use."""

    tea_id: str
    category: str


def match_chinese_tea(title: str, context: str = "") -> TeaMatch | None:
    """Return a Chinese tea, or None when the title is not one of them."""
    if not title or not str(title).strip():
        return None
    tokens = fold_latin(title).split()
    context_tokens = fold_latin(context).split()
    combined = tokens + context_tokens
    if _contains_any(combined, _ORIGIN_PHRASES):
        return None
    if _contains_any(combined, _WARE_PHRASES):
        return None
    if _contains_any(combined, _FLAVOUR_PHRASES):
        return None
    if _contains_any(combined, _HERBAL_PHRASES):
        return None
    if _contains_any(combined, _GIFT_PHRASES):
        return None
    if _contains_any(combined, _EVENT_PHRASES):
        return None
    latin, cjk, catalog = _indexes()
    latin_hit = _best_latin(tokens, latin)
    cjk_hit = _best_cjk(cjk_norm(title), cjk)
    chosen = _choose(latin_hit, cjk_hit)
    if chosen is None:
        return None
    tea_id = _refine(chosen.tea_id, tokens)
    info = catalog.get(tea_id)
    if info is None:
        return None
    if _has_taiwan_marker(combined) and not info.taiwan:
        return None
    category = _category(info, tea_id, tokens)
    span = chosen.span
    if not _category_allowed(category, tea_id, tokens, span, context_tokens):
        return None
    return TeaMatch(tea_id, category)


def match_chinese_green(title: str, context: str = "") -> str | None:
    """Tea id for any kept Chinese tea. The name is historical."""
    match = match_chinese_tea(title, context)
    if match is None:
        return None
    return match.tea_id


def manual_tea_ids() -> set[str]:
    """Slugs named by the hand-written alias table."""
    ids = {tea_id for _, tea_id, _ in _MANUAL}
    ids.update(tea_id for _, tea_id in _MANUAL_CJK)
    ids.update(refined for _, _, refined in _REFINEMENTS)
    return ids


def encyclopedia_tea_ids() -> set[str]:
    """Chinese and Taiwanese tea slugs from the local tea.support list."""
    return set(_indexes()[2])


def encyclopedia_green_ids() -> set[str]:
    """Chinese green slugs, including Taiwanese greens."""
    catalog = _indexes()[2]
    return {slug for slug, info in catalog.items() if info.tea_type == "green"}


def _refine(tea_id: str, tokens: list[str]) -> str:
    token_set = set(tokens)
    for base, key, refined in _REFINEMENTS:
        if tea_id == base and set(key) <= token_set:
            return refined
    return tea_id


def _choose(latin: _Hit | None, cjk: _Hit | None) -> _Hit | None:
    if latin is None:
        return cjk
    if cjk is None:
        return latin
    if latin.tea_id == cjk.tea_id:
        return latin
    if latin.score[0] == cjk.score[0]:
        return None
    return latin if latin.score > cjk.score else cjk


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


@dataclass(frozen=True)
class _TeaInfo:
    tea_type: str
    taiwan: bool
    dark: bool


def _has_taiwan_marker(tokens: list[str]) -> bool:
    return any(token in _TAIWAN_MARKERS for token in tokens)


def _has_scent(tea_id: str, tokens: list[str]) -> bool:
    if any(marker in tea_id for marker in _SCENT_SLUG_MARKERS):
        return True
    if any(token in _SCENT_TOKENS for token in tokens):
        return True
    pairs = itertools.pairwise(tokens)
    return any(left == "gui" and right == "hua" for left, right in pairs)


def _category(info: _TeaInfo, tea_id: str, tokens: list[str]) -> str:
    if _has_scent(tea_id, tokens):
        return "scented"
    if info.dark:
        return "dark"
    return _TYPE_CATEGORY[info.tea_type]


def _signal_set(tokens: list[str]) -> set[str]:
    found: set[str] = set()
    if not tokens:
        return found
    token_set = set(tokens)
    for category, phrase in _CATEGORY_SIGNALS:
        if len(phrase) == 1:
            if phrase[0] in token_set:
                found.add(category)
            continue
        width = len(phrase)
        for index in range(len(tokens) - width + 1):
            if tuple(tokens[index : index + width]) == phrase:
                found.add(category)
                break
    return found


def _signals_outside(tokens: list[str], span: tuple[int, int] | None) -> set[str]:
    if span is None:
        return _signal_set(tokens)
    start, end = span
    outside = [token for index, token in enumerate(tokens) if index < start or index >= end]
    return _signal_set(outside)


def _category_allowed(
    category: str,
    tea_id: str,
    tokens: list[str],
    span: tuple[int, int] | None,
    context_tokens: list[str],
) -> bool:
    """Drop a row when a type word outside the tea name disagrees with it."""
    signals = _signals_outside(tokens, span)
    context_signals = _signal_set(context_tokens)
    if len(context_signals) == 1:
        signals |= context_signals
    if not signals or category in signals or category == "scented":
        return True
    if tea_id == "anji-baicha" and signals <= {"green", "white"}:
        return True
    return category == "dark" and "puerh" in signals


def _is_taiwan_tea(slug: str, name_en: str) -> bool:
    if any(marker in slug for marker in _TAIWAN_SLUG_MARKERS):
        return True
    # "Taiwanese" in an English name is not enough: 武夷烏龍 is labeled that way.
    name_tokens = set(fold_latin(name_en).split())
    return bool(name_tokens & {"taiwan", "formosa", "tajwan", "taipei"})


def _is_dark_tea(slug: str) -> bool:
    return any(marker in slug for marker in _DARK_SLUG_MARKERS)


@lru_cache(maxsize=1)
def _indexes() -> tuple[tuple[_Alias, ...], tuple[tuple[str, str, int], ...], dict[str, _TeaInfo]]:
    catalog: dict[str, _TeaInfo] = {}
    for tea in load_teas():
        slug = str(tea.get("slug") or "")
        if tea.get("origin_country") != "CN":
            continue
        if tea.get("tea_type") not in _IN_SCOPE_TYPES:
            continue
        catalog[slug] = _TeaInfo(
            tea_type=str(tea.get("tea_type") or ""),
            taiwan=_is_taiwan_tea(slug, str(tea.get("name_en") or "")),
            dark=_is_dark_tea(slug),
        )
    missing = manual_tea_ids() - set(catalog)
    if missing:
        joined = ", ".join(sorted(missing))
        raise RuntimeError(f"manual tea ids are not Chinese teas: {joined}")

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
        if slug not in catalog:
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
        normalized = "".join(ch for ch in cjk_norm(text) if "\u4e00" <= ch <= "\u9fff")
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
    return tuple(latin.values()), cjk_rows, catalog
