"""Chinese-green title filter. No network."""

from __future__ import annotations

import pytest

from tea_agent.eu_shops.classify import (
    encyclopedia_green_ids,
    manual_tea_ids,
    match_chinese_green,
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
        "Dian Hong Mao Feng",
        "Czerwony Long Jing",
        "Jiu Qu Hong Mei (Czerwony Long Jing)",
        "Qimen Mao Feng",
        "Matcha Ceremonialna",
        "Milk Oolong",
        "Gaiwan Long Jing",
        "Long Jing teapot",
        "Jasmine Green",
        "Wuyishan Hochland Grüntee",
        "Xiang Shui Bai Cha",
        "Bai Mu Dan",
        "Green tea",
        "China",
        "Yunnan",
        "Long Jing / Bi Luo Chun",
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


def test_manual_ids_are_chinese_greens() -> None:
    assert manual_tea_ids() <= encyclopedia_green_ids()
