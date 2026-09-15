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
    format_next_steps_block,
    invented_buy_urls,
    is_catalog_url,
    parse_next_steps,
    prompt_needs_next_steps,
    should_attach_next_steps,
)

LONGJING_URL = "https://www.teashop.by/product/longjing-1/"
BILUOCHUN_URL = "https://www.teashop.by/product/duntin-bi-lo-chun/"
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
    for label in ACTION_LABELS:
        assert f"[{label}]" in updated
    assert updated.count(HEADING) == 1


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
