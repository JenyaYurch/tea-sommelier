"""Telegram HTML rendering of agent markdown (TEA-21)."""

import pytest
from telegram.constants import ParseMode

from telegram_integration.format import markdown_to_telegram_html
from telegram_integration.main import _deliver_reply


def test_bold_and_headings_become_html() -> None:
    html = markdown_to_telegram_html(
        "### Рекомендации\n\n**Лунцзин** — мягкий чай.\n"
    )
    assert "<b>Рекомендации</b>" in html
    assert "<b>Лунцзин</b>" in html
    assert "**" not in html
    assert "###" not in html


def test_links_and_escaping() -> None:
    html = markdown_to_telegram_html(
        "[Купить](https://www.teashop.by/product/longjing-1/) и 2 < 3"
    )
    assert '<a href="https://www.teashop.by/product/longjing-1/">Купить</a>' in html
    assert "2 &lt; 3" in html


def test_inline_code_and_italic() -> None:
    html = markdown_to_telegram_html("температура `80°C` и *мягкий* вкус")
    assert "<code>80°C</code>" in html
    assert "<i>мягкий</i>" in html


@pytest.mark.asyncio
async def test_deliver_reply_sends_html_parse_mode() -> None:
    calls: list[tuple[str, dict]] = []

    class FakeMessage:
        async def reply_text(self, text: str, **kwargs) -> None:
            calls.append((text, kwargs))

    await _deliver_reply(
        FakeMessage(),  # type: ignore[arg-type]
        "### Тема\n\n**жирный** список:\n1. Лунцзин\n2. Би Ло Чунь",
    )
    assert len(calls) == 1
    text, kwargs = calls[0]
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["disable_web_page_preview"] is True
    assert "<b>Тема</b>" in text
    assert "<b>жирный</b>" in text
    assert "**" not in text
    assert "###" not in text
