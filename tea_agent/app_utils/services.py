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

"""Process-wide ADK session/artifact/memory services shared by every serving surface.

Registered under ``shared://`` so the ADK web routes, the A2A path, and the
reasoning_engine adapter share one instance: a session created on any surface
is visible to the others.

Sessions (TEA-14) use Cloud SQL, Agent Engine, or SQLite via
``SESSION_SERVICE_URI`` / ``CLOUD_SQL_INSTANCE`` / ``GOOGLE_CLOUD_AGENT_ENGINE_ID``.
Cloud Run refuses in-memory so a restart cannot silently drop taste profiles.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os

from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.cli.service_registry import get_service_registry
from google.adk.cli.utils.service_factory import (
    create_memory_service_from_options,
    create_session_service_from_options,
)

from tea_agent.app_utils.session_uri import (
    CLOUD_RUN_SERVICE_ENV,
    agent_engine_id_from_env,
    is_ephemeral_session_uri,
    missing_persistent_backend_error,
    postgres_engine_kwargs,
    resolve_session_service_uri,
)

SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"
MEMORY_SERVICE_URI = "shared://memory"
_PREPARE_TABLE_ATTEMPTS = 8
_log = logging.getLogger(__name__)

_AGENT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


@functools.cache
def get_session_service():
    """Process-wide session service shared across every serving surface."""
    if uri := resolve_session_service_uri():
        if os.environ.get(CLOUD_RUN_SERVICE_ENV) and is_ephemeral_session_uri(uri):
            raise missing_persistent_backend_error()
        kwargs = postgres_engine_kwargs(uri)
        return create_session_service_from_options(
            base_dir=_AGENT_DIR,
            session_service_uri=uri,
            session_db_kwargs=kwargs or None,
        )
    if agent_engine_id := agent_engine_id_from_env():
        from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService

        return VertexAiSessionService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=_agent_engine_location(),
            agent_engine_id=agent_engine_id,
        )
    if os.environ.get(CLOUD_RUN_SERVICE_ENV):
        raise missing_persistent_backend_error()
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    return InMemorySessionService()


async def ensure_session_store_ready():
    """Create DatabaseSessionService tables before the first Telegram /run.

    ADK otherwise pays this on the first request. On Cloud Run a down Cloud SQL
    socket should fail the revision at startup instead of dropping profiles.
    The unix socket can appear a moment after process start; retry connect errors.
    """
    service = get_session_service()
    prepare = getattr(service, "prepare_tables", None)
    if not callable(prepare):
        return service
    last_error: BaseException | None = None
    for attempt in range(1, _PREPARE_TABLE_ATTEMPTS + 1):
        try:
            await prepare()
            return service
        except Exception as err:
            last_error = err
            if attempt == _PREPARE_TABLE_ATTEMPTS or not _is_retryable_db_error(err):
                raise
            _log.warning(
                "Session store not ready (attempt %s/%s): %s",
                attempt,
                _PREPARE_TABLE_ATTEMPTS,
                err,
            )
            await asyncio.sleep(0.5 * attempt)
    raise last_error  # pragma: no cover


def _is_retryable_db_error(err: BaseException) -> bool:
    text = str(err).lower()
    needles = (
        "connection refused",
        "could not connect",
        "connection does not exist",
        "timeout",
        "temporarily unavailable",
        "the socket",
        "no such file",
    )
    return isinstance(err, (ConnectionError, OSError, TimeoutError)) or any(
        needle in text for needle in needles
    )


def _agent_engine_location() -> str | None:
    return (
        os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
        or os.environ.get("MEMORY_BANK_LOCATION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
    )


@functools.cache
def get_memory_service():
    """Process-wide memory service: Memory Bank when an Agent Engine is set."""
    if uri := os.environ.get("MEMORY_SERVICE_URI"):
        return create_memory_service_from_options(
            base_dir=_AGENT_DIR, memory_service_uri=uri
        )
    if agent_engine_id := os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        from google.adk.memory.vertex_ai_memory_bank_service import (
            VertexAiMemoryBankService,
        )

        return VertexAiMemoryBankService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=_agent_engine_location(),
            agent_engine_id=agent_engine_id,
        )
    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

    return InMemoryMemoryService()


@functools.cache
def get_artifact_service():
    """Process-wide artifact service: GCS when a bucket is set, else in-memory."""
    if bucket := os.environ.get("LOGS_BUCKET_NAME"):
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()


_registry = get_service_registry()
_registry.register_session_service("shared", lambda uri, **kw: get_session_service())
_registry.register_artifact_service("shared", lambda uri, **kw: get_artifact_service())
_registry.register_memory_service("shared", lambda uri, **kw: get_memory_service())
