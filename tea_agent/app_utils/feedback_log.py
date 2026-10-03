"""Structured feedback log for the beta pilot.

Sessions live in memory, so this log is the copy that survives a restart.
On Cloud Run, ``log_struct`` writes ``jsonPayload`` (filter
``jsonPayload.log_type="feedback"``). Without credentials the same JSON is
one logger line.
"""

from __future__ import annotations

import json
import logging

_log = logging.getLogger("tea-sommelier.feedback")
LOGGER_NAME = "tea-sommelier.feedback"


def log_feedback(payload: dict) -> None:
    """Write one feedback record. Null fields are omitted; ``score`` 0 is kept."""
    body = {key: value for key, value in payload.items() if value is not None}
    body["log_type"] = "feedback"
    try:
        from google.cloud import logging as google_cloud_logging

        google_cloud_logging.Client().logger(LOGGER_NAME).log_struct(
            body, severity="INFO"
        )
    except Exception:
        _log.info("feedback %s", json.dumps(body, ensure_ascii=False, default=str))
