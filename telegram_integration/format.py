"""Convert agent markdown to Telegram HTML so **bold** / headings render."""

from __future__ import annotations

import html
import re

_FENCE = re.compile(r"```(?:\w+)?\n?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADING = re.compile(r"(?m)^(#{1,6})\s+(.+)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_STRIKE = re.compile(r"~~(.+?)~~")
_ITALIC = re.compile(
    r"(?<![A-Za-z0-9])\*(?!\*)(.+?)(?<!\*)\*(?![A-Za-z0-9*])"
    r"|(?<![A-Za-z0-9])_(?!_)(.+?)(?<!_)_(?![A-Za-z0-9_])"
)
_HOLD = re.compile(r"@@TEAHTML(\d+)@@")


def markdown_to_telegram_html(text: str) -> str:
    """Turn common markdown into Telegram HTML. Unknown text is escaped."""
    if not text:
        return text

    held: list[str] = []

    def hold(fragment: str) -> str:
        held.append(fragment)
        return f"@@TEAHTML{len(held) - 1}@@"

    def fence(match: re.Match[str]) -> str:
        return hold(f"<pre>{html.escape(match.group(1).rstrip())}</pre>")

    def code(match: re.Match[str]) -> str:
        return hold(f"<code>{html.escape(match.group(1))}</code>")

    def link(match: re.Match[str]) -> str:
        label = html.escape(match.group(1))
        url = html.escape(match.group(2), quote=True)
        return hold(f'<a href="{url}">{label}</a>')

    def heading(match: re.Match[str]) -> str:
        inner = match.group(2).strip().strip("*_ ")
        return hold(f"<b>{html.escape(inner)}</b>")

    def bold(match: re.Match[str]) -> str:
        inner = match.group(1) or match.group(2) or ""
        return hold(f"<b>{html.escape(inner)}</b>")

    def strike(match: re.Match[str]) -> str:
        return hold(f"<s>{html.escape(match.group(1))}</s>")

    def italic(match: re.Match[str]) -> str:
        inner = match.group(1) or match.group(2) or ""
        return hold(f"<i>{html.escape(inner)}</i>")

    converted = _FENCE.sub(fence, text)
    converted = _INLINE_CODE.sub(code, converted)
    converted = _LINK.sub(link, converted)
    converted = _HEADING.sub(heading, converted)
    converted = _BOLD.sub(bold, converted)
    converted = _STRIKE.sub(strike, converted)
    converted = _ITALIC.sub(italic, converted)
    converted = html.escape(converted)
    return _HOLD.sub(lambda match: held[int(match.group(1))], converted)
