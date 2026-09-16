"""Cross-session Memory Bank hooks for the tea sommelier.

Generation runs after the agent turn and must not fail the user-facing reply.
Local/dev uses InMemoryMemoryService; production uses VertexAiMemoryBankService
when GOOGLE_CLOUD_AGENT_ENGINE_ID or MEMORY_SERVICE_URI is set.
"""

from __future__ import annotations

import logging

from google.adk.agents.callback_context import CallbackContext

logger = logging.getLogger("tea_agent.memory")


async def generate_memories_callback(callback_context: CallbackContext):
    """Send session events to Memory Bank after a turn (ADK memory-bank sample).

    VertexAiMemoryBankService defaults to ingest_events (non-blocking extraction).
    Missing memory service or backend errors are swallowed so recall never sits
    on the hot path.
    """
    try:
        await callback_context.add_session_to_memory()
    except ValueError:
        return None
    except Exception:
        logger.exception("Memory generation failed; user response is unchanged")
    return None
