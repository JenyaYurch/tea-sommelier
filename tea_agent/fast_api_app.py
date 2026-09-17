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

import contextlib
import logging
import os
from collections.abc import AsyncIterator

from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner

from tea_agent.app_utils import services
from tea_agent.app_utils.a2a import attach_a2a_routes
from tea_agent.app_utils.typing import Feedback

load_dotenv()
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_log = logging.getLogger(__name__)


def _otel_to_cloud() -> bool:
    raw = os.getenv("OTEL_TO_CLOUD")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no"}


def _log_feedback(payload: dict) -> None:
    try:
        from google.cloud import logging as google_cloud_logging

        google_cloud_logging.Client().logger(__name__).log_struct(
            payload, severity="INFO"
        )
    except Exception:
        _log.info("feedback %s", payload)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from tea_agent.agent import app as adk_app
    from tea_agent.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=await services.ensure_session_store_ready(),
        artifact_service=services.get_artifact_service(),
        memory_service=services.get_memory_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    await attach_a2a_routes(
        app,
        agent=root_agent,
        runner=runner,
        task_store=InMemoryTaskStore(),
        rpc_path=f"/a2a/{adk_app.name}",
    )
    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    memory_service_uri=services.MEMORY_SERVICE_URI,
    otel_to_cloud=_otel_to_cloud(),
    lifespan=lifespan,
    auto_create_session=True,
)
app.title = "tea-sommelier"
app.description = "API for interacting with the Agent tea-sommelier"


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback.

    Args:
        feedback: The feedback data to log

    Returns:
        Success message
    """
    _log_feedback(feedback.model_dump())
    return {"status": "success"}


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
