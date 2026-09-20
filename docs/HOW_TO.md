# TeaBot how-to: run, deploy, status, and other functions

Operational guide for the Chinese-tea sommelier (Telegram → ADK agent → [tea.support](https://tea.support) facts + [teashop.by](https://teashop.by) prices). Repo commands use `uv`. Do not commit `.env`, tokens, or API keys.

Canonical copy in git: this file. Notion copy lives under **TeaBot — запуск AI-сомелье**.

---

## 1. What you are operating

Two ways to talk to the same agent:

| Surface | When to use | Process |
| --- | --- | --- |
| ADK playground | Prompt / tool iteration | Local web UI, auto-reload |
| Telegram polling | Local bot chat | `uv run python -m telegram_integration` (must stay running) |
| Cloud Run webhook | 24/7 bot | Services `tea-agent` + `telegram-integration` |

Telegram allows **one** getUpdates client. Do not run local polling while the Cloud Run webhook is active.

**Default production (cheap):** Cloud Run, AI Studio Gemini key, **no Cloud SQL**, **no Memory Bank**. Taste profiles live in memory and reset on scale-to-zero or a new revision.

**Optional paid persistence:** Cloud SQL (`tea-sessions`) and/or Agent Engine Memory Bank. Both are opt-in and require `--execute` on their setup scripts.

### Live GCP

| Item | Value |
| --- | --- |
| Project | `gen-lang-client-0393777014` |
| Region | `europe-central2` |
| Agent service | `tea-agent` |
| Telegram service | `telegram-integration` |
| ADK app name | `tea_agent` |
| Gemini (local + Cloud Run) | `GOOGLE_GENAI_USE_VERTEXAI=false` (AI Studio, not Vertex) |
| Model | `gemini-3.6-flash` unless `TEA_AGENT_MODEL` is set |

Billing is already attached on this project. Do **not** start a new Free Trial.

---

## 2. One-time setup

### Tools

- [uv](https://docs.astral.sh/uv/)
- Python via uv (3.12+ in Docker; local often 3.13)
- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud`) for deploy/status
- [agents-cli](https://github.com/google/adk): `uv tool install google-agents-cli`

```bash
uvx google-agents-cli setup
agents-cli install
```

### Local `.env`

Copy `.env.example` to `.env` (gitignored). Minimum for local:

```env
GOOGLE_GENAI_USE_VERTEXAI=false
GEMINI_API_KEY=your-api-key-here
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
```

Optional:

| Variable | Purpose |
| --- | --- |
| `TEA_AGENT_MODEL` | Override model if daily flash quota is exhausted (e.g. `gemini-3.1-flash-lite`) |
| `GOOGLE_CLOUD_PROJECT` | Used by some eval tooling; a placeholder like `teabot-local-eval` is enough locally |
| `SESSION_SERVICE_URI` | Local sessions; polling defaults to sqlite `./sessions.db` |
| `CLOUD_SQL_INSTANCE` | **Do not set** unless you want to pay for Postgres |
| `GOOGLE_CLOUD_AGENT_ENGINE_ID` | **Do not set** unless you want Memory Bank |

### GCP login (deploy / status only)

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project gen-lang-client-0393777014
gcloud config set run/region europe-central2
```

### Secret Manager (once, or when keys rotate)

Reads `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` / `GOOGLE_API_KEY` from local `.env` and upserts Secret Manager names `TELEGRAM_BOT_TOKEN` and `GOOGLE_API_KEY`. Values are not printed.

```bash
uv run python scripts/setup_secret_manager.py
```

---

## 3. Run locally

### Playground (agent only, no Telegram)

```bash
agents-cli playground
```

Edits under `tea_agent/` reload. Useful for tools, prompts, and tea-type replies.

### Local ADK HTTP (same FastAPI as Cloud Run `tea-agent`)

```bash
uv run uvicorn tea_agent.fast_api_app:app --host 127.0.0.1 --port 8080
```

Probe a session (Telegram-shaped ids):

```bash
curl http://127.0.0.1:8080/apps/tea_agent/users/tg-1/sessions/tg-sess-1
```

`404` on GET means the server is up but that session does not exist yet. POST creates it.

### Telegram polling (local bot)

```bash
uv run python -m telegram_integration
```

- Uses in-process ADK Runner + sqlite sessions by default.
- Process must stay running.
- Send `/start` in Telegram. Replies are in Russian.
- If Cloud Run webhook is already set, Telegram returns a conflict. Delete the webhook first (see [Telegram webhook vs polling](#6-telegram-webhook-vs-polling)).

### Tests

```bash
uv run pytest tests/unit tests/integration
agents-cli lint
```

Some session tests skip unless a Cloud SQL-like unix socket is present. That is expected.

---

## 4. Deploy (Cloud Run)

Week 3 deploy is **`scripts/deploy_cloud_run.py`**, not `agents-cli deploy`. `--execute` is required; dry-run prints the plan and creates nothing.

### Dry-run (always first)

Always pass the billed project so a leftover `GOOGLE_CLOUD_PROJECT=teabot-local-eval` in the shell does not win:

```bash
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014
```

A cheap-path plan looks like this:

- Memory Bank: off
- Sessions: in-memory (`TEA_ALLOW_EPHEMERAL_SESSIONS`)
- Will **not** create Cloud SQL `tea-sessions`
- `gcloud run deploy tea-agent ... --clear-cloudsql-instances`
- Two-stage Telegram: placeholder `SERVICE_URL=https://google.com`, then the real Cloud Run URL

### Execute (explicit approval)

```bash
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute
```

What `--execute` does:

1. Enables required APIs, grants Cloud Run builder IAM.
2. Deploys `tea-agent` from source (Dockerfile: uvicorn on port 8080, copies `data/`).
3. Skips TEA-14 write/restart/check when there is no Cloud SQL / Agent Engine.
4. Deploys `telegram-integration` with a placeholder `SERVICE_URL`.
5. Updates `SERVICE_URL` to the real Telegram service URL (webhook path `<SERVICE_URL>/<TELEGRAM_BOT_TOKEN>`).

During step 4 the webhook target is briefly `https://google.com`. Do not `/start` until the script prints **Deploy finished**.

Skip the persistence probe even when SQL is attached:

```bash
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute --skip-verify
```

### After deploy

1. Wait until both services show Ready.
2. Send `/start` in Telegram (one message; Gemini free tier is tight).
3. First reply after idle can take 20–30s (cold start). The bot says so.

### What a new revision does to users

| Backend | Taste profile after this deploy / scale-to-zero |
| --- | --- |
| In-memory (default) | Lost |
| Cloud SQL | Kept (if TEA-14 verify passed) |
| Agent Engine sessions | Kept |

---

## 5. Check status

Replace project/region if needed. PowerShell and bash both accept these `gcloud` lines.

### Services up?

```bash
gcloud run services list --project=gen-lang-client-0393777014 --region=europe-central2
```

### URLs and env (no secrets printed if you use this format)

```bash
gcloud run services describe tea-agent --project=gen-lang-client-0393777014 --region=europe-central2 --format="yaml(status.url,status.conditions,spec.template.spec.containers[0].env)"

gcloud run services describe telegram-integration --project=gen-lang-client-0393777014 --region=europe-central2 --format="yaml(status.url,status.conditions,spec.template.spec.containers[0].env)"
```

Confirm:

- `tea-agent` env has `GOOGLE_GENAI_USE_VERTEXAI=false` and, on the cheap path, `TEA_ALLOW_EPHEMERAL_SESSIONS=true`.
- No `CLOUD_SQL_INSTANCE` and no `GOOGLE_CLOUD_AGENT_ENGINE_ID` unless you opted in.
- Telegram `ADK_SERVER_URL` equals the tea-agent URL.
- Telegram `SERVICE_URL` equals the telegram-integration URL (not `https://google.com`).

Current URLs (they stay stable across revisions unless you recreate the service):

```text
https://tea-agent-6zy2uwhjla-lm.a.run.app
https://telegram-integration-6zy2uwhjla-lm.a.run.app
```

HTTP check (unauthenticated by design so Telegram can call the agent):

```bash
curl -s -o NUL -w "%{http_code}" https://tea-agent-6zy2uwhjla-lm.a.run.app/apps/tea_agent/users/tg-warmup/sessions/tg-sess-warmup
```

`404` is healthy (no such session). `000` / timeout often means cold start — retry after 30s. `500` on first request after a bad revision: read logs.

### Revisions

```bash
gcloud run revisions list --service=tea-agent --project=gen-lang-client-0393777014 --region=europe-central2
gcloud run revisions list --service=telegram-integration --project=gen-lang-client-0393777014 --region=europe-central2
```

### Logs

```bash
gcloud run services logs read tea-agent --project=gen-lang-client-0393777014 --region=europe-central2 --limit=80

gcloud run services logs read telegram-integration --project=gen-lang-client-0393777014 --region=europe-central2 --limit=80
```

Console: Cloud Run → service → Logs. Cloud Trace / Cloud Logging are enabled on the agent image when `OTEL_TO_CLOUD` is not `false`.

### Cloud SQL / Memory Bank (should be empty on the cheap path)

```bash
gcloud sql instances list --project=gen-lang-client-0393777014
```

Empty table = no always-on Postgres bill. If `tea-sessions` exists and you are not using it, delete it in the console or:

```bash
gcloud sql instances delete tea-sessions --project=gen-lang-client-0393777014
```

That is destructive. Detaching SQL from Cloud Run (`--clear-cloudsql-instances`) does **not** stop instance billing.

Memory Bank: if `GOOGLE_CLOUD_AGENT_ENGINE_ID` is absent from tea-agent env, Memory Bank is off.

### Secrets exist (names only)

```bash
gcloud secrets list --project=gen-lang-client-0393777014 --format="value(name)"
```

Expect `GOOGLE_API_KEY` and `TELEGRAM_BOT_TOKEN`. Do not `gcloud secrets versions access` in chat logs.

---

## 6. Telegram webhook vs polling

Webhook URL shape: `https://<telegram-integration-url>/<TELEGRAM_BOT_TOKEN>`. The token is only in the path, not in git.

### Conflict: “terminated by other getUpdates”

Local polling and Cloud Run both tried to receive updates.

- To use **Cloud Run**: stop local `python -m telegram_integration`. Redeploy or wait; the webhook is set by the Telegram service on startup.
- To use **local polling**: stop using the webhook:

```bash
curl https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/deleteWebhook
```

Then start polling. Do not paste the token into tickets or commits.

### Bot error texts (production)

| User-visible (RU) | Typical cause |
| --- | --- |
| Сомелье сейчас запускается… | Cloud Run cold start |
| Сейчас упёрлись в лимит бесплатного Gemini… | AI Studio quota (about 5 req/min, ~20/day on flash) |
| Запрос слишком долгий… | `/run` timeout (120s) |
| Сомелье временно недоступен… | Generic HTTP/agent failure |

---

## 7. Evaluation (quality loop)

Datasets live in `tests/eval/datasets/` (see that folder’s README). TEA-23 mixed-order slice: `mixed-order-case.json`. Broader tea types: `tea-types-dataset.json`.

### Generate traces

`agents-cli eval generate` wants a non-empty `GOOGLE_CLOUD_PROJECT` even for local AI Studio (placeholder is fine):

**PowerShell**

```powershell
$env:GOOGLE_CLOUD_PROJECT='teabot-local-eval'
agents-cli eval generate --dataset tests/eval/datasets/mixed-order-case.json
```

**bash**

```bash
export GOOGLE_CLOUD_PROJECT=teabot-local-eval
agents-cli eval generate --dataset tests/eval/datasets/mixed-order-case.json
```

Free-tier flash is ~5 req/min and ~20 req/day. Prefer single-case slices with pauses, then merge:

```bash
uv run python scripts/merge_traces.py artifacts/traces/traces_merged.json artifacts/traces/traces_*.json
```

### Grade locally (no Vertex)

If `agents-cli eval grade` fails with missing `google.adk`, grade with the same judge as CI:

```bash
uv run python scripts/grade_traces_local.py artifacts/traces/<traces-file>.json
```

Judge uses `gemini-3.5-flash` with a `gemini-3.1-flash-lite` fallback so it does not share the agent’s daily quota.

### Other eval commands

```bash
agents-cli eval metric list
agents-cli eval compare <older-grade.json> <newer-grade.json>
agents-cli eval analyze <grade-results.json>
agents-cli eval dataset synthesize --count 10
```

Debug helpers (edit the hardcoded paths inside if needed): `scripts/extract_tool_calls.py`, `scripts/summarize_traces.py`.

**Do not change the agent model** unless you explicitly decide to. Quota 404s are usually `GOOGLE_CLOUD_LOCATION` (use `global`), not a model rename.

---

## 8. Catalog and slug dictionary

Facts come from tools, not from the LLM’s memory.

| Data | File | Refresh |
| --- | --- | --- |
| tea.support slugs / aliases | `data/tea_slugs.json` | `uv run python scripts/build_tea_slugs.py` |
| teashop.by SKUs | `data/teashop_catalog.json` | `uv run python scripts/parse_teashop.py` |

Shop refresh checklist (every 1–2 weeks):

```bash
uv run python scripts/parse_teashop.py --status
uv run python scripts/parse_teashop.py
```

Spot-check 2–3 product URLs and prices, then commit the JSON. Dry-run:

```bash
uv run python scripts/parse_teashop.py --max-pages 6 --dry-run
```

Redeploy after catalog commits if Cloud Run should serve the new JSON (image copies `data/` at build time).

---

## 9. Optional: persistent sessions (Cloud SQL)

Always-on Postgres. Skip for maximum free.

```bash
uv run python scripts/setup_cloud_sql.py --project=gen-lang-client-0393777014
uv run python scripts/setup_cloud_sql.py --project=gen-lang-client-0393777014 --execute
```

Then put `CLOUD_SQL_INSTANCE=gen-lang-client-0393777014:europe-central2:tea-sessions` in the environment used by deploy (not git). Password is Secret Manager `SESSION_DB_PASSWORD`. Redeploy with `--execute`. TEA-14 probe:

```bash
uv run python scripts/verify_session_persistence.py --base-url https://tea-agent-6zy2uwhjla-lm.a.run.app --write
# new tea-agent revision (deploy script does this when SQL is attached)
uv run python scripts/verify_session_persistence.py --base-url https://tea-agent-6zy2uwhjla-lm.a.run.app --check
```

Without `TEA_ALLOW_EPHEMERAL_SESSIONS`, Cloud Run **refuses** sqlite/in-memory so a restart cannot silently drop profiles.

---

## 10. Optional: Memory Bank

Agent Engine Memory Bank is not hosted in `europe-central2`; setup defaults to location `eu`.

```bash
uv run python scripts/setup_memory_bank.py
uv run python scripts/setup_memory_bank.py --execute
```

Then set `GOOGLE_CLOUD_AGENT_ENGINE_ID` (and usually `GOOGLE_CLOUD_AGENT_ENGINE_LOCATION=eu`) and redeploy. Local/dev recall stays in-memory if the id is unset.

---

## 11. Other functions (cheat sheet)

| Task | Command |
| --- | --- |
| Install deps | `agents-cli install` |
| Playground | `agents-cli playground` |
| Unit + integration tests | `uv run pytest tests/unit tests/integration` |
| Lint | `agents-cli lint` |
| Local Telegram | `uv run python -m telegram_integration` |
| Local tea-agent HTTP | `uv run uvicorn tea_agent.fast_api_app:app --host 127.0.0.1 --port 8080` |
| Upsert secrets | `uv run python scripts/setup_secret_manager.py` |
| Deploy dry-run | `uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014` |
| Deploy | same + `--execute` |
| Service list | `gcloud run services list --project=gen-lang-client-0393777014 --region=europe-central2` |
| Logs | `gcloud run services logs read tea-agent --project=gen-lang-client-0393777014 --region=europe-central2 --limit=80` |
| Rebuild slug index | `uv run python scripts/build_tea_slugs.py` |
| Shop catalog | `uv run python scripts/parse_teashop.py --status` then parse |
| Grade traces | `uv run python scripts/grade_traces_local.py artifacts/traces/<file>.json` |
| Add CI/CD later | `agents-cli scaffold enhance` |

A2A: the FastAPI app exposes A2A routes. Inspector: [A2A Inspector](https://github.com/a2aproject/a2a-inspector).

---

## 12. Troubleshooting

| Symptom | What to check |
| --- | --- |
| Dry-run project is `teabot-local-eval` | Pass `--project=gen-lang-client-0393777014` |
| Dry-run says CREATE Cloud SQL | Old script; current cheap path must say in-memory / will not create |
| tea-agent crash loop, “persistent ADK session backend” | Missing `TEA_ALLOW_EPHEMERAL_SESSIONS` and no Cloud SQL / engine id |
| Telegram conflict / getUpdates | Webhook + polling together; see §6 |
| Empty or quota replies | AI Studio daily/minute limits; wait or set `TEA_AGENT_MODEL` |
| First Telegram message hangs | Cold start; wait 30s |
| Bot works locally, Cloud Run outdated | Need `--execute` after git changes (including `data/*.json`) |
| Invented tasting notes | Tools-only rule; check slug resolve + `get_tea_card` |
| `agents-cli eval grade` ImportError `google.adk` | Use `scripts/grade_traces_local.py` |

---

## 13. Safety

- Never commit `.env`, `credentials.json`, or secret values.
- `--execute` on deploy / Cloud SQL / Memory Bank is explicit approval. Dry-run first.
- Do not change `MODEL` in `tea_agent/agent.py` unless you intend to.
- `tea-agent` is `--allow-unauthenticated` so Telegram can call it. Treat the URL as public.
