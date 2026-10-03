# ruff: noqa: RUF001
"""Structured feedback payload and Cloud Logging sink (TEA-17)."""

from __future__ import annotations

import json
import logging

import pytest

from tea_agent.app_utils.feedback_log import LOGGER_NAME, log_feedback
from tea_agent.app_utils.typing import Feedback


def test_feedback_model_clips_text_and_keeps_zero_score() -> None:
    item = Feedback(
        score=0,
        text="  " + ("а" * 5000),
        user_id="tg-5",
        session_id="tg-sess-5",
        source="thumbs",
        rating="down",
        message_id=77,
        reply_excerpt="строка\nдва  " + ("б" * 800),
    )
    payload = item.model_dump(mode="json")
    assert payload["score"] == 0
    assert payload["user_id"] == "tg-5"
    assert payload["session_id"] == "tg-sess-5"
    assert payload["log_type"] == "feedback"
    assert payload["service_name"] == "tea-sommelier"
    assert payload["source"] == "thumbs"
    assert payload["rating"] == "down"
    assert payload["message_id"] == "77"
    assert len(payload["text"]) == 4000
    assert "\n" not in payload["reply_excerpt"]
    assert len(payload["reply_excerpt"]) == 500


def test_free_text_feedback_omits_score() -> None:
    payload = Feedback(
        text="сломалась кнопка", user_id="tg-1", session_id="tg-sess-1"
    ).model_dump(mode="json")
    assert payload["score"] is None
    assert payload["text"] == "сломалась кнопка"


def test_log_feedback_writes_structured_record_and_keeps_zero(monkeypatch) -> None:
    captured: dict = {}

    class _Logger:
        def log_struct(self, payload, severity="INFO") -> None:
            captured["payload"] = payload
            captured["severity"] = severity

    class _Client:
        def logger(self, name: str) -> _Logger:
            captured["name"] = name
            return _Logger()

    import google.cloud.logging as cloud_logging

    monkeypatch.setattr(cloud_logging, "Client", lambda: _Client())
    log_feedback(
        {
            "score": 0,
            "text": "",
            "rating": "down",
            "user_id": "tg-5",
            "extra_null": None,
        }
    )
    assert captured["name"] == LOGGER_NAME
    assert captured["severity"] == "INFO"
    assert captured["payload"]["log_type"] == "feedback"
    assert captured["payload"]["score"] == 0
    assert captured["payload"]["user_id"] == "tg-5"
    assert "extra_null" not in captured["payload"]


def test_log_feedback_falls_back_to_one_json_line(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    import google.cloud.logging as cloud_logging

    def _boom():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(cloud_logging, "Client", _boom)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_feedback({"rating": "down", "text": "горчит", "score": None})
    assert len(caplog.records) == 1
    message = caplog.records[0].message
    assert message.startswith("feedback ")
    body = json.loads(message.removeprefix("feedback "))
    assert body["log_type"] == "feedback"
    assert body["text"] == "горчит"
    assert body["rating"] == "down"
    assert "score" not in body
