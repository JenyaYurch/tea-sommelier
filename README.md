# tea-sommelier


Agent generated with `agents-cli` version `1.2.1`

## Project Structure

```
tea-sommelier/
├── tea_agent/         # Core agent code
│   ├── agent.py               # Main agent logic
│   ├── fast_api_app.py        # FastAPI Backend server
│   └── app_utils/             # App utilities and helpers
├── tests/                     # Unit, integration, and eval tests
├── docs/HOW_TO.md             # Run, deploy, and status
└── pyproject.toml             # Project dependencies
```

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
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests                                                        |
| [A2A Inspector](https://github.com/a2aproject/a2a-inspector) | Launch A2A Protocol Inspector                                                        |

## 🛠️ Project Management

| Command | What It Does |
|---------|--------------|
| `agents-cli scaffold enhance` | Add CI/CD pipelines and Terraform infrastructure |
| `agents-cli infra cicd` | One-command setup of entire CI/CD pipeline + infrastructure |
| `agents-cli scaffold upgrade` | Auto-upgrade to latest version while preserving customizations |

---

## Development

Edit your agent logic in `tea_agent/agent.py` and test with `agents-cli playground` - it auto-reloads on save.

## How to run, deploy, and check status

Full operational guide (local run, Cloud Run deploy, logs, eval, catalog refresh, optional Cloud SQL / Memory Bank): [docs/HOW_TO.md](docs/HOW_TO.md).

## Telegram

Local polling (process must stay running):

```bash
uv run python -m telegram_integration
```

Production is two Cloud Run services (`tea-agent` + `telegram-integration`) in `europe-central2`. Webhook path is `<SERVICE_URL>/<TELEGRAM_BOT_TOKEN>`. Telegram is deployed twice: placeholder `SERVICE_URL=https://google.com`, then the real Cloud Run URL.

```bash
uv run python scripts/setup_secret_manager.py
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute
```

Pass `--project` on deploy. A leftover `GOOGLE_CLOUD_PROJECT` in the shell or `.env` (for example `teabot-local-eval`) wins over the billed project. `setup_secret_manager.py` has no `--project` flag and uses that same override, then the default `gen-lang-client-0393777014`.

`--execute` deploys without Memory Bank and without Cloud SQL unless `GOOGLE_CLOUD_AGENT_ENGINE_ID` or `CLOUD_SQL_INSTANCE` is already set. Default `tea-agent` gets `TEA_ALLOW_EPHEMERAL_SESSIONS=true`, `--clear-cloudsql-instances`, `--max-instances=1`, `--min-instances=1`, and `--concurrency=8`. Taste profiles live in that one process. Idle scale-to-zero does not wipe them; a new revision does. Set `TEA_AGENT_MIN_INSTANCES=0` before deploy to allow scale-to-zero. Create Secret Manager secret `TEA_AGENT_AUTH_SECRET` once before the first locked-down deploy (commands in [docs/HOW_TO.md](docs/HOW_TO.md)). `telegram-integration` sends it as `X-Tea-Agent-Token`. Unauthenticated calls to `tea-agent` `/run` and session routes get 401. `/health` stays open. The ADK dev UI is off on Cloud Run and on for local uvicorn when the secret and `TEA_AGENT_DEV_UI` are unset. Optional TEA-14 persistence: `uv run python scripts/setup_cloud_sql.py --project=gen-lang-client-0393777014 --execute`, set `CLOUD_SQL_INSTANCE`, then redeploy. Cloud Run still refuses sqlite/in-memory when that flag is unset. Local polling defaults to `SESSION_SERVICE_URI=sqlite+aiosqlite:///./sessions.db`. Do not run local polling and the webhook at the same time (Telegram allows one getUpdates client). Do not publish the tea-agent URL.

## Deployment

Week 3 uses the two-service Cloud Run script above, not `agents-cli deploy`. To add CI/CD and Terraform later, run `agents-cli scaffold enhance`.

## Observability

When `OTEL_TO_CLOUD` is not `false`, the agent exports OpenTelemetry to Cloud Trace, Cloud Monitoring, and Cloud Logging.

## A2A Inspector

This agent supports the [A2A Protocol](https://a2a-protocol.org/). Use the [A2A Inspector](https://github.com/a2aproject/a2a-inspector) to test interoperability.
See the [A2A Inspector docs](https://github.com/a2aproject/a2a-inspector) for details.
