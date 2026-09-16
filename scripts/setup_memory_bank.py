"""Create or reuse an Agent Engine instance with Memory Bank (TEA-9).

Does not create anything unless you pass --execute (explicit approval).
Cloud Run stays in europe-central2; Memory Bank defaults to the EU
multi-region (`eu`) because europe-central2 does not host Memory Bank.

Usage:
    uv run python scripts/setup_memory_bank.py
    uv run python scripts/setup_memory_bank.py --execute
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from telegram_integration.deploy_spec import DEFAULT_PROJECT

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCATION = "eu"
DEFAULT_DISPLAY_NAME = "tea-sommelier-memory"


def _read_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        values[key.strip()] = raw.strip().strip("'").strip('"')
    return values


def _project_id(cli_project: str | None) -> str:
    env = _read_dotenv()
    return (
        cli_project
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or env.get("GOOGLE_CLOUD_PROJECT")
        or DEFAULT_PROJECT
    ).strip()


def _location(cli_location: str | None) -> str:
    env = _read_dotenv()
    return (
        cli_location
        or os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
        or os.environ.get("MEMORY_BANK_LOCATION")
        or env.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
        or env.get("MEMORY_BANK_LOCATION")
        or DEFAULT_LOCATION
    ).strip()


def _print_env_lines(resource_name: str, location: str) -> None:
    engine_id = resource_name.rsplit("/", 1)[-1]
    print()
    print("Put these in .env (not git) and redeploy tea-agent:")
    print(f"GOOGLE_CLOUD_AGENT_ENGINE_ID={engine_id}")
    print(f"GOOGLE_CLOUD_AGENT_ENGINE_LOCATION={location}")
    print(f"MEMORY_SERVICE_URI=agentengine://{resource_name}")


def _print_plan(project: str, location: str, display_name: str) -> None:
    print("Memory Bank Agent Engine plan (no secrets printed)")
    print(f"  project:       {project}")
    print(f"  location:      {location}")
    print(f"  display_name:  {display_name}")
    print("  topics:        USER_PREFERENCES, EXPLICIT_INSTRUCTIONS, TEA_TASTE_PROFILE")
    print()
    print("Creates or reuses an Agent Engine with memory_bank_config.")
    print("Does not deploy the sommelier to Agent Runtime — Cloud Run stays the serve path.")
    print("Dry-run only. Pass --execute after explicit approval to create/update.")


def _execute(project: str, location: str, display_name: str) -> None:
    import vertexai
    from vertexai._genai.types import AgentEngineConfig, ReasoningEngineContextSpec

    from tea_agent.app_utils.memory_config import memory_bank_config

    client = vertexai.Client(project=project, location=location)
    existing = [
        agent
        for agent in client.agent_engines.list()
        if agent.api_resource.display_name == display_name
    ]
    context_spec = ReasoningEngineContextSpec(memory_bank_config=memory_bank_config)
    config = AgentEngineConfig(
        display_name=display_name,
        context_spec=context_spec,
    )
    if existing:
        print(f"Updating existing Agent Engine {display_name}")
        engine = client.agent_engines.update(
            name=existing[0].api_resource.name,
            config=config,
        )
    else:
        print(f"Creating Agent Engine {display_name}")
        engine = client.agent_engines.create(config=config)
    resource_name = engine.api_resource.name
    print(f"Agent Engine: {resource_name}")
    _print_env_lines(resource_name, location)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=None, help="GCP project id")
    parser.add_argument(
        "--location",
        default=None,
        help="Memory Bank location (default: eu multi-region)",
    )
    parser.add_argument(
        "--display-name",
        default=DEFAULT_DISPLAY_NAME,
        help="Agent Engine display name",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create/update. Default is dry-run.",
    )
    args = parser.parse_args()
    project = _project_id(args.project)
    location = _location(args.location)
    if not args.execute:
        _print_plan(project, location, args.display_name)
        return
    _execute(project, location, args.display_name)


if __name__ == "__main__":
    main()
