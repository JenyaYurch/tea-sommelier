# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import uuid
from typing import (
    Literal,
)

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

_TEXT_LIMIT = 4000
_EXCERPT_LIMIT = 500
_SHORT_LIMIT = 64


class Feedback(BaseModel):
    """One piece of tester feedback for a conversation.

    ``score`` is 1 for 👍, 0 for 👎, and null for free text from ``/feedback``.
    ``source`` is ``thumbs``, ``thumbs_reason``, or ``command``.
    ``user_id`` / ``session_id`` are the ADK ids (``tg-<id>``, ``tg-sess-<id>``).
    ``message_id`` is the Telegram message that was rated; ``reply_excerpt`` is
    a short clip of that message so the log is readable after the process restarts.
    """

    score: int | float | None = None
    text: str = ""
    log_type: Literal["feedback"] = "feedback"
    service_name: Literal["tea-sommelier"] = "tea-sommelier"
    user_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = ""
    rating: str = ""
    message_id: str = ""
    reply_excerpt: str = ""

    @field_validator(
        "text", "source", "rating", "message_id", "reply_excerpt", mode="before"
    )
    @classmethod
    def _coerce_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    @field_validator("text")
    @classmethod
    def _clip_text(cls, value: str) -> str:
        return value.strip()[:_TEXT_LIMIT]

    @field_validator("reply_excerpt")
    @classmethod
    def _clip_excerpt(cls, value: str) -> str:
        collapsed = " ".join(value.split())
        return collapsed[:_EXCERPT_LIMIT]

    @field_validator("source", "rating", "message_id")
    @classmethod
    def _clip_short(cls, value: str) -> str:
        return value.strip()[:_SHORT_LIMIT]
