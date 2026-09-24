# ruff: noqa: RUF001
"""Unit tests for playground next-step chips (TEA-7)."""

from types import SimpleNamespace

from google.adk.models.llm_response import LlmResponse
from google.genai import types

from tea_agent.next_steps import (
    ACTION_LABELS,
    HEADING,
    SHOP_HITS_KEY,
    attach_next_steps_to_response,
    collect_shop_hits,
    ensure_next_steps,
    extract_recommended_names,
    format_next_steps_block,
    invented_buy_urls,
    is_catalog_url,
    parse_next_steps,
    products_for_reply,
    prompt_needs_next_steps,
    should_attach_next_steps,
)

LONGJING_URL = "https://www.teashop.by/product/longjing-1/"
LONGJING_SKU2 = "https://www.teashop.by/product/xihu-longjing/"
LONGJING_SKU3 = "https://www.teashop.by/product/longjingcha/"
BILUOCHUN_URL = "https://www.teashop.by/product/duntin-bi-lo-chun/"
ANJI_URL = "https://www.teashop.by/product/anczi-bajcha/"
BAI_MAO_URL = "https://www.teashop.by/product/baj-mao-xou-snezhnaya-obezyana/"
GUNTIN_URL = "https://www.teashop.by/product/pujer-guntin/"
LAO_CHA_TOU_URL = "https://www.teashop.by/product/lao-cha-tou-tri-obezjany/"
FAKE_URL = "https://www.teashop.by/product/totally-invented-tea/"


def _recs_text() -> str:
    return (
        "1. Лунцзин — мягкий утренний чай.\n"
        "2. Би Ло Чунь — без горечи.\n"
        "3. Аньцзи Бай Ча — светлый вкус."
    )


def test_catalog_url_accepts_real_product() -> None:
    assert is_catalog_url(LONGJING_URL)
    assert not is_catalog_url(FAKE_URL)
    assert not is_catalog_url("https://amazon.com/longjing")


def test_format_block_has_actions_and_catalog_buy_links() -> None:
    block = format_next_steps_block(
        [
            {"product_name": "Си Ху Лун Цзин", "product_url": LONGJING_URL},
            {"product_name": "Fake", "product_url": FAKE_URL},
        ]
    )
    assert HEADING in block
    for label in ACTION_LABELS:
        assert f"[{label}]" in block
    assert LONGJING_URL in block
    assert "Купить: Си Ху Лун Цзин" in block
    assert FAKE_URL not in block
    assert "[купить]" not in block


def test_format_block_without_products_has_text_buy_chip() -> None:
    block = format_next_steps_block([])
    assert "[купить]" in block
    assert "http" not in block


def test_parse_next_steps_roundtrip() -> None:
    block = format_next_steps_block(
        [{"product_name": "Си Ху Лун Цзин", "product_url": LONGJING_URL}]
    )
    steps = parse_next_steps(f"текст\n\n{block}")
    actions = {step.label for step in steps if step.kind == "action"}
    buys = [step for step in steps if step.kind == "buy"]
    assert set(ACTION_LABELS) <= actions
    assert buys[0].url == LONGJING_URL
    assert "Лун Цзин" in buys[0].label


def test_ensure_next_steps_appends_and_rewrites_invented_link() -> None:
    text = _recs_text() + f"\n\n{HEADING}\n[мягче]\n[Купить: фейк]({FAKE_URL})"
    products = [
        {"product_name": "Си Ху Лун Цзин", "product_url": LONGJING_URL},
        {"product_name": "Дунтин Би Ло Чунь", "product_url": BILUOCHUN_URL},
    ]
    updated = ensure_next_steps(text, products)
    assert FAKE_URL not in updated
    assert LONGJING_URL in updated
    assert BILUOCHUN_URL in updated
    assert ANJI_URL in updated
    for label in ACTION_LABELS:
        assert f"[{label}]" in updated
    assert updated.count(HEADING) == 1


def test_extract_recommended_names_from_numbered_list() -> None:
    names = extract_recommended_names(
        "1. **Лунцзин (Longjing)** — мягкий утренний чай.\n"
        "2. Би Ло Чунь — без горечи.\n"
        "3. Аньцзи Бай Ча — светлый вкус.\n"
    )
    assert names == ["Лунцзин (Longjing)", "Би Ло Чунь", "Аньцзи Бай Ча"]


def test_buy_products_match_named_teas_not_neighbor_skus() -> None:
    products = [
        {
            "product_name": "Лунцзин «Колодец Дракона»",
            "product_url": LONGJING_URL,
            "matched_slug": "xihu-longjing",
        },
        {
            "product_name": "Си Ху Лун Цзин",
            "product_url": LONGJING_SKU2,
            "matched_slug": "xihu-longjing",
        },
        {
            "product_name": "Лунцзин ча",
            "product_url": LONGJING_SKU3,
            "matched_slug": "xihu-longjing",
        },
        {
            "product_name": "Бай Мао Хоу",
            "product_url": BAI_MAO_URL,
            "matched_slug": "bai-mao-hou",
        },
        {
            "product_name": "Дунтин Би Ло Чунь",
            "product_url": BILUOCHUN_URL,
            "matched_slug": "biluochun",
        },
        {
            "product_name": "Аньцзи Бай Ча",
            "product_url": ANJI_URL,
            "matched_slug": "anji-baicha",
        },
    ]
    selected = products_for_reply(_recs_text(), products)
    urls = [item["product_url"] for item in selected]
    assert urls == [LONGJING_URL, BILUOCHUN_URL, ANJI_URL]
    assert LONGJING_SKU2 not in urls
    assert LONGJING_SKU3 not in urls
    assert BAI_MAO_URL not in urls

    updated = ensure_next_steps(_recs_text(), products)
    buys = [step for step in parse_next_steps(updated) if step.kind == "buy"]
    assert [step.url for step in buys] == [LONGJING_URL, BILUOCHUN_URL, ANJI_URL]


def test_collect_shop_hits_keeps_distinct_skus_with_same_slug() -> None:
    state: dict = {}
    tool = SimpleNamespace(name="find_in_shop")
    ctx = SimpleNamespace(state=state)
    collect_shop_hits(
        tool,  # type: ignore[arg-type]
        {},
        ctx,  # type: ignore[arg-type]
        {
            "status": "success",
            "products": [
                {
                    "product_name": "Лунцзин 1",
                    "product_url": LONGJING_URL,
                    "matched_slug": "xihu-longjing",
                },
                {
                    "product_name": "Лунцзин 2",
                    "product_url": LONGJING_SKU2,
                    "matched_slug": "xihu-longjing",
                },
            ],
        },
    )
    hits = state[SHOP_HITS_KEY]
    assert [item["product_url"] for item in hits] == [LONGJING_URL, LONGJING_SKU2]


def test_shu_products_with_shared_slug_keep_sku_data_atomic() -> None:
    products = [
        {
            "product_name": "Шу пуэр Лао Ча Тоу «Чайные обезьяны»",
            "product_url": LAO_CHA_TOU_URL,
            "matched_slug": "7572-shu-bing",
            "price_from_byn": 11.0,
            "weight_g": 25,
        },
        {
            "product_name": "Шу пуэр Гун Тин",
            "product_url": GUNTIN_URL,
            "matched_slug": "7572-shu-bing",
            "price_from_byn": 10.1,
            "weight_g": 100,
        },
    ]
    text = (
        "1. Шу пуэр «Гун Тин» — мягкий и древесный.\n"
        "2. Шу пуэр «Лао Ча Тоу» — плотный и сладкий."
    )

    selected = products_for_reply(text, products)

    assert [item["product_url"] for item in selected] == [
        GUNTIN_URL,
        LAO_CHA_TOU_URL,
    ]
    assert [(item["price_from_byn"], item["weight_g"]) for item in selected] == [
        (10.1, 100),
        (11.0, 25),
    ]
    assert "Гунтин" in selected[0]["product_name"]
    assert "Лао Ча Тоу" in selected[1]["product_name"]

    buys = [
        step
        for step in parse_next_steps(ensure_next_steps(text, products))
        if step.kind == "buy"
    ]
    assert [(step.label, step.url) for step in buys] == [
        (selected[0]["product_name"], GUNTIN_URL),
        (selected[1]["product_name"], LAO_CHA_TOU_URL),
    ]


def test_should_attach_on_three_recs_even_without_shop() -> None:
    assert should_attach_next_steps(_recs_text(), [])
    assert not should_attach_next_steps("Какой у вас опыт с китайским чаем?", [])


def test_invented_buy_urls_detects_fake_teashop_and_amazon() -> None:
    text = (
        f"[Купить]({FAKE_URL}) и ещё "
        "[магазин](https://www.amazon.com/dp/tea)"
    )
    found = invented_buy_urls(text)
    assert FAKE_URL.rstrip("/") in found or any("totally-invented" in u for u in found)
    assert any("amazon.com" in u for u in found)


def test_prompt_needs_next_steps() -> None:
    assert prompt_needs_next_steps("Я новичок, хочу мягкий чай без горечи утром")
    assert prompt_needs_next_steps("Покажи, что можно купить у партнёра")
    assert not prompt_needs_next_steps("Поможет ли чай вылечить давление?")


def test_collect_shop_hits_keeps_only_catalog_urls() -> None:
    state: dict = {}
    tool = SimpleNamespace(name="find_in_shop")
    ctx = SimpleNamespace(state=state)
    result = collect_shop_hits(
        tool,  # type: ignore[arg-type]
        {},
        ctx,  # type: ignore[arg-type]
        {
            "status": "success",
            "products": [
                {"product_name": "Си Ху Лун Цзин", "product_url": LONGJING_URL},
                {"product_name": "Fake", "product_url": FAKE_URL},
            ],
        },
    )
    assert result is None
    hits = state[SHOP_HITS_KEY]
    assert len(hits) == 1
    assert hits[0]["product_url"] == LONGJING_URL


def test_after_model_callback_appends_block() -> None:
    state = {
        SHOP_HITS_KEY: [
            {"product_name": "Си Ху Лун Цзин", "product_url": LONGJING_URL}
        ]
    }
    ctx = SimpleNamespace(state=state)
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=_recs_text())],
        )
    )
    updated = attach_next_steps_to_response(ctx, response)  # type: ignore[arg-type]
    assert updated is not None
    text = updated.content.parts[0].text
    assert HEADING in text
    assert LONGJING_URL in text
    assert FAKE_URL not in text


def test_after_model_callback_skips_function_call() -> None:
    ctx = SimpleNamespace(state={})
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="find_in_shop", args={"slug": "longjing"}
                )
            ],
        )
    )
    assert attach_next_steps_to_response(ctx, response) is None  # type: ignore[arg-type]


def test_after_model_callback_skips_partial() -> None:
    ctx = SimpleNamespace(
        state={SHOP_HITS_KEY: [{"product_url": LONGJING_URL}]}
    )
    response = LlmResponse(
        partial=True,
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=_recs_text())],
        ),
    )
    assert attach_next_steps_to_response(ctx, response) is None  # type: ignore[arg-type]


def test_next_steps_quality_metric_pass_and_fail() -> None:
    from tests.eval.next_steps_quality import evaluate

    recs = _recs_text()
    good = evaluate(
        {
            "prompt": "Я новичок, хочу мягкий чай без горечи утром. Дай 3 рекомендации.",
            "response": {
                "role": "model",
                "parts": [
                    {
                        "text": ensure_next_steps(
                            recs,
                            [
                                {
                                    "product_name": "Си Ху Лун Цзин",
                                    "product_url": LONGJING_URL,
                                }
                            ],
                        )
                    }
                ],
            },
        }
    )
    assert good["score"] == 5

    bad = evaluate(
        {
            "prompt": "Я новичок, хочу мягкий чай без горечи утром",
            "response": {"role": "model", "parts": [{"text": recs}]},
        }
    )
    assert bad["score"] == 1
