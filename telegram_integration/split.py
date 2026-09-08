"""Split long agent replies to fit Telegram's 4096-character limit."""

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def split_telegram_text(
    text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH
) -> list[str]:
    """Split text on paragraph / line / word boundaries when possible."""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < 1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks
