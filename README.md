# tea-sommelier


Agent generated with `agents-cli` version `1.2.1`

## Project Structure

```
tea-sommelier/
├── tea_agent/         # Core agent code
│   ├── agent.py               # Main agent logic
│   ├── fast_api_app.py        # FastAPI Backend server
│   └── app_utils/             # App utilities and helpers
├── tests/                     # Unit, integration, and load tests
├── GEMINI.md                  # AI-assisted development guide
└── pyproject.toml             # Project dependencies
```

> 💡 **Tip:** Use [Antigravity CLI](https://antigravity.google/) for AI-assisted development - project context is pre-configured in `GEMINI.md`.

## Requirements

Before you begin, ensure you have:
- **uv**: Python package manager (used for all dependency management in this project) - [Install](https://docs.astral.sh/uv/getting-started/installation/) ([add packages](https://docs.astral.sh/uv/concepts/dependencies/) with `uv add <package>`)
- **agents-cli**: Agents CLI - Install with `uv tool install google-agents-cli`
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)


## Quick Start

Install `agents-cli` and its skills if not already installed:

```bash
uvx google-agents-cli setup
```

Install required packages:

```bash
agents-cli install
```

Test the agent with a local web server:

```bash
agents-cli playground
```

You can also use features from the [ADK](https://adk.dev/) CLI with `uv run adk`.

## Commands

| Command              | Description                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `agents-cli install` | Install dependencies using uv                                                         |
| `agents-cli playground` | Launch local development environment                                                  |
| `agents-cli lint`    | Run code quality checks                                                               |
| `agents-cli eval`    | Evaluate agent behavior (generate, grade, analyze, and more — see `agents-cli eval --help`) |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests                                                        || [A2A Inspector](https://github.com/a2aproject/a2a-inspector) | Launch A2A Protocol Inspector                                                        |

## 🛠️ Project Management

| Command | What It Does |
|---------|--------------|
| `agents-cli scaffold enhance` | Add CI/CD pipelines and Terraform infrastructure |
| `agents-cli infra cicd` | One-command setup of entire CI/CD pipeline + infrastructure |
| `agents-cli scaffold upgrade` | Auto-upgrade to latest version while preserving customizations |

---

## Development

Edit your agent logic in `tea_agent/agent.py` and test with `agents-cli playground` - it auto-reloads on save.

## Telegram

Local polling (process must stay running):

```bash
uv run python -m telegram_integration
```

Production is two Cloud Run services (`tea-agent` + `telegram-integration`) in `europe-central2`. Webhook path is `<SERVICE_URL>/<TELEGRAM_BOT_TOKEN>`. Telegram is deployed twice: placeholder `SERVICE_URL=https://google.com`, then the real Cloud Run URL.

```bash
uv run python scripts/setup_secret_manager.py
uv run python scripts/setup_cloud_sql.py
uv run python scripts/deploy_cloud_run.py
uv run python scripts/deploy_cloud_run.py --execute
```

`--execute` provisions Cloud SQL `tea-sessions` if neither `CLOUD_SQL_INSTANCE` nor `GOOGLE_CLOUD_AGENT_ENGINE_ID` is set, deploys `tea-agent` as `SESSION_DB_USER=postgres` (POSTGRES_17 public schema owner, so `prepare_tables` can CREATE), then proves TEA-14 (`verify_session_persistence.py --write`, a new revision, `--check`) before deploying `telegram-integration`. If the org policy rejects `allUsers`, tea-agent is redeployed with `--no-allow-unauthenticated` and `roles/run.invoker` is granted to the Compute default SA plus the deploying account; Telegram still calls ADK with a Cloud Run identity token after 401/403. Telegram taste profiles survive Cloud Run restarts with Cloud SQL (`SESSION_DB_PASSWORD` in Secret Manager) or Agent Engine sessions. Cloud Run refuses in-memory and sqlite (container disk is ephemeral). Local polling defaults to `SESSION_SERVICE_URI=sqlite+aiosqlite:///./sessions.db`. Do not run local polling and the webhook at the same time (Telegram allows one getUpdates client).

## Deployment

Week 3 uses the two-service Cloud Run script above, not `agents-cli deploy`. To add CI/CD and Terraform later, run `agents-cli scaffold enhance`.

## Observability

Built-in telemetry exports to Cloud Trace, BigQuery, and Cloud Logging.

## A2A Inspector

This agent supports the [A2A Protocol](https://a2a-protocol.org/). Use the [A2A Inspector](https://github.com/a2aproject/a2a-inspector) to test interoperability.
See the [A2A Inspector docs](https://github.com/a2aproject/a2a-inspector) for details.
