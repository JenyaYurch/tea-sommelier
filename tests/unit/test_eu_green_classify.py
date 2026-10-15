"""Chinese tea title filter. No network."""

from __future__ import annotations

import pytest

from tea_agent.eu_shops.classify import (
    encyclopedia_green_ids,
    encyclopedia_tea_ids,
    manual_tea_ids,
    match_chinese_green,
    match_chinese_tea,
)


@pytest.mark.parametrize(
    ("title", "tea_id"),
    [
        ("Long Jing", "xihu-longjing"),
        ("LONG JING", "xihu-longjing"),
        ("Lung Ching", "xihu-longjing"),
        ("Dragon Well", "xihu-longjing"),
        ("2026 Ming Qian Shi Feng Long Jing", "xihu-longjing"),
        ("2026 Xiaoshan Natural Garden Long Jing", "xihu-longjing"),
        ("2026 Ming Qian Da Fo Long Jing", "dafu-long-jing"),
        ("Da Fo Long Jing", "dafu-long-jing"),
        ("Bi Luo Chun", "biluochun"),
        ("Pi Lo Chun", "biluochun"),
        ("Bi Luo Chun (Bio)", "biluochun"),
        ("Bi Luo Chun Dongting", "dongting-biluochun"),
        ("San Xia Organic Bi Luo Chun Green Tea", "sanxia-biluochun"),
        ("Huangshan Mao Feng", "huangshan-mao-feng"),
        ("Huang Shan Mao Feng", "huangshan-mao-feng"),
        ("Organic Mao Feng", "huangshan-mao-feng"),
        ("Huangshan Maofeng 黄山毛峰", "huangshan-mao-feng"),
        ("Yunnan Mao Feng", "yun-nan-mao-feng"),
        ("Yunnan Organic Mao Feng", "yun-nan-mao-feng"),
        ("Emei Mao Feng", "emei-mao-feng"),
        ("Houkui", "taiping-hou-kui"),
        ("Tai Ping Hou Kui", "taiping-hou-kui"),
        ("2026 Houkeng Tai Ping Hou Kui", "taiping-hou-kui"),
        ("Anji Bai Cha", "anji-baicha"),
        ("Anji Bai Cha Mao Feng", "anji-baicha"),
        ("Lu An Gua Pian", "liu-an-guapian"),
        ("Liu An Gua Pian 2024", "liu-an-guapian"),
        ("Lu Shan Yun Wu", "lushan-yun-wu"),
        ("Zhu Ye Qing", "zhu-ye-qing"),
        ("Gunpowder", "pingshui-zhucha"),
        ("龙井", "xihu-longjing"),
        ("大佛龙井", "dafu-long-jing"),
    ],
)
def test_named_chinese_greens(title: str, tea_id: str) -> None:
    assert match_chinese_green(title) == tea_id


@pytest.mark.parametrize(
    "title",
    [
        "Nepal Mao Feng",
        "Formosa Pi Lo Chun",
        "Qi Shan Mao Feng",
        "Huangshan Organic Mao Feng Hong Cha",
        "Czerwony Long Jing",
        "Czarna Long Jing",
        "Jiu Qu Hong Mei (Czerwony Long Jing)",
        "Matcha Ceremonialna",
        "Milk Oolong",
        "Gaiwan Long Jing",
        "Long Jing teapot",
        "Jasmine Green",
        "Earl Grey",
        "Sencha Asan",
        "Rooibos",
        "Kenya Black Tea",
        "Kenia",
        "Darjeeling",
        "Wuyishan Hochland Grüntee",
        "Xiang Shui Bai Cha",
        "Green tea",
        "Oolong",
        "Pu-erh",
        "China",
        "Yunnan",
        "Schwarztee",
        "Weisser Tee",
        "Gift set Long Jing",
        "Zestaw herbat Chiny",
        "Long Jing / Bi Luo Chun",
        "Dragon Pearls",
        "Gushu Sheng Dragon Pearls 2021",
    ],
)
def test_excludes_other_teas_and_teaware(title: str) -> None:
    assert match_chinese_green(title) is None


def test_oolong_tag_rejects_a_green_name() -> None:
    assert match_chinese_green("Long Jing", "Oolong") is None


def test_green_category_does_not_reject() -> None:
    assert match_chinese_green("Long Jing", "Green Tea Herbaty") == "xihu-longjing"


def test_anji_keeps_a_white_tea_label() -> None:
    assert match_chinese_green("Anji Bai Cha", "white") == "anji-baicha"


@pytest.mark.parametrize(
    ("title", "context", "tea_id", "category"),
    [
        ("Bai Mu Dan", "", "bai-mudan", "white"),
        ("Silver Needle", "", "baihao-yinzhen", "white"),
        ("Junshan Yinzhen", "", "junshan-yin-zhen", "yellow"),
        ("Huo Shan Huang Ya", "", "huoshan-huang-ya", "yellow"),
        ("Tie Guan Yin", "", "tie-guanyin", "oolong"),
        ("Muzha Tie Guan Yin", "", "muzha-tieguanyin", "oolong"),
        ("Ali Shan", "", "alishan-wulong", "oolong"),
        ("Dong Ding", "", "dong-ding-wulong", "oolong"),
        ("Jin Xuan", "", "jin-xuan", "oolong"),
        ("Da Hong Pao", "", "da-hong-pao", "oolong"),
        ("Jin Jun Mei", "", "jin-jun-mei", "black"),
        ("Dian Hong Mao Feng", "", "dianhong-gongfu", "black"),
        ("Qimen Mao Feng", "", "qimen-hongcha", "black"),
        ("Lao Cha Tou", "", "lao-cha-tou", "puerh"),
        ("Jasmine Pearls", "", "moli-longzhu", "scented"),
        ("Jasmin Dragon Pearls", "", "moli-longzhu", "scented"),
        ("Jasmine Long Jing", "", "xihu-longjing", "scented"),
        ("Anji Bai Cha", "white", "anji-baicha", "green"),
        ("Formosa Ali Shan", "", "alishan-wulong", "oolong"),
    ],
)
def test_category_for_each_chinese_kind(title: str, context: str, tea_id: str, category: str) -> None:
    match = match_chinese_tea(title, context)
    assert match is not None
    assert match.tea_id == tea_id
    assert match.category == category


def test_taiwan_subtitle_rejects_a_mainland_green() -> None:
    assert match_chinese_tea("Bi Luo Chun", "San Xia, Taipei, Taiwan") is None


def test_manual_ids_are_in_the_encyclopedia() -> None:
    teas = encyclopedia_tea_ids()
    assert manual_tea_ids() <= teas
    assert "tie-guanyin" in teas
    assert "alishan-wulong" in teas
    assert encyclopedia_green_ids() <= teas
    assert "xihu-longjing" in encyclopedia_green_ids()
