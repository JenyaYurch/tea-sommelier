"""Cross-session Memory Bank hooks for the tea sommelier.

Per-turn preload and generation run only when Memory Bank is enabled.
The switch is ``GOOGLE_CLOUD_AGENT_ENGINE_ID`` (``agent_engine_id_from_env``).
``MEMORY_SERVICE_URI`` still selects a memory service in ``get_memory_service``,
but it does not attach these hooks: production opts in with the engine id.
"""

from __future__ import annotations

import logging
import sys

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from tea_agent.app_utils.session_uri import agent_engine_id_from_env

logger = logging.getLogger("tea_agent.memory")

_MEMORY_OFF_LOG = (
    "Memory Bank: off (GOOGLE_CLOUD_AGENT_ENGINE_ID unset); "
    "PreloadMemoryTool and generate_memories_callback are not attached"
)
_MEMORY_ON_LOG = (
    "Memory Bank: on (GOOGLE_CLOUD_AGENT_ENGINE_ID is set); "
    "PreloadMemoryTool and generate_memories_callback are attached"
)
_status_logged = False


def memory_bank_enabled() -> bool:
    """True when Agent Engine Memory Bank should load and write memories.

    Same switch as ``agent_engine_id_from_env``: a non-empty
    ``GOOGLE_CLOUD_AGENT_ENGINE_ID``. Blank values are off.
    """
    return bool(agent_engine_id_from_env())


def _chain_has_handler() -> bool:
    current: logging.Logger | None = logger
    while current is not None:
        if current.handlers:
            return True
        if not current.propagate:
            return False
        current = current.parent
    return False


def _ensure_startup_handler() -> None:
    """Emit the one startup line even when the root logger is still WARNING.

    Cloud Run captures stderr. Uvicorn does not lower the root level, so an
    INFO record with no handler of its own is dropped. If a parent logger
    already has a handler, reuse it so the line is not printed twice.
    """
    logger.setLevel(logging.INFO)
    if _chain_has_handler():
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(handler)


def log_memory_bank_status() -> None:
    """Log once per process whether Memory Bank hooks are attached."""
    global _status_logged
    if _status_logged:
        return
    _status_logged = True
    _ensure_startup_handler()
    if memory_bank_enabled():
        logger.info(_MEMORY_ON_LOG)
    else:
        logger.info(_MEMORY_OFF_LOG)


def memory_tools() -> list[PreloadMemoryTool]:
    """``PreloadMemoryTool`` when Memory Bank is on, otherwise nothing.

    ADK runs ``process_llm_request`` for every tool on the agent, before the
    model call. Leaving this attached against the in-memory store pastes the
    current session back into the prompt as ``<PAST_CONVERSATIONS>``.
    """
    if not memory_bank_enabled():
        return []
    return [PreloadMemoryTool()]


def memory_after_agent_callback():
    """``generate_memories_callback`` when Memory Bank is on, otherwise none."""
    if not memory_bank_enabled():
        return None
    return generate_memories_callback


async def generate_memories_callback(callback_context: CallbackContext):
    """Send session events to Memory Bank after a turn (ADK memory-bank sample).

    VertexAiMemoryBankService defaults to ingest_events (non-blocking extraction).
    Missing memory service or backend errors are swallowed so recall never sits
    on the hot path. When Memory Bank is off this returns without calling the
    memory service, so a stray attachment cannot write the in-memory store.
    """
    if not memory_bank_enabled():
        return None
    try:
        await callback_context.add_session_to_memory()
    except ValueError:
        return None
    except Exception:
        logger.exception("Memory generation failed; user response is unchanged")
    return None
