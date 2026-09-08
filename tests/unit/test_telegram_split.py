from telegram_integration.split import split_telegram_text


def test_short_text_stays_one_chunk() -> None:
    assert split_telegram_text("привет") == ["привет"]


def test_empty_text() -> None:
    assert split_telegram_text("   ") == []


def test_splits_on_paragraphs() -> None:
    first = "а" * 20
    second = "б" * 20
    chunks = split_telegram_text(f"{first}\n\n{second}", limit=30)
    assert chunks == [first, second]


def test_hard_split_when_no_whitespace() -> None:
    text = "я" * 50
    chunks = split_telegram_text(text, limit=20)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 20 for chunk in chunks)
