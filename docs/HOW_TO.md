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

**Default production (cheap):** Cloud Run, AI Studio Gemini key, **no Cloud SQL**, **no Memory Bank**. Taste profiles live in the memory of one `tea-agent` instance (`--max-instances=1`, `--min-instances=1`). Idle time does not wipe them. A new revision still does. Set `TEA_AGENT_MIN_INSTANCES=0` before deploy only if you accept scale-to-zero.

**Who can call `tea-agent`:** only `telegram-integration`. The Cloud Run proxy still allows unauthenticated TCP so `/health` stays open, and the app returns **401** on `/run`, session routes, and the dev UI unless the request sends `X-Tea-Agent-Token`. The token is Secret Manager secret `TEA_AGENT_AUTH_SECRET`, mounted on both services. The ADK dev UI is off in prod (`TEA_AGENT_DEV_UI=false`) and on when you run uvicorn locally with that flag unset. Telegram itself calls `telegram-integration`, never `tea-agent`. Do not publish the `tea-agent` URL.

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
| Model | `gemini-3.1-flash-lite` unless `TEA_AGENT_MODEL` is set |

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
| `TEA_AGENT_MODEL` | Override model (default `gemini-3.1-flash-lite`; use `gemini-3.6-flash` on a paid Gemini API tier) |
| `GOOGLE_CLOUD_PROJECT` | Used by some eval tooling; a placeholder like `teabot-local-eval` is enough locally |
| `SESSION_SERVICE_URI` | Local sessions; polling defaults to sqlite `./sessions.db` |
| `CLOUD_SQL_INSTANCE` | **Do not set** unless you want to pay for Postgres |
| `GOOGLE_CLOUD_AGENT_ENGINE_ID` | **Do not set** unless you want Memory Bank |
| `TEA_AGENT_AUTH_SECRET` | Leave unset locally. Set only to exercise the prod header check |
| `TEA_AGENT_MIN_INSTANCES` | Deploy only. Default `1`. `0` allows scale-to-zero |

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

This script has no `--project` flag. `GOOGLE_CLOUD_PROJECT` in the shell or `.env` overrides the default `gen-lang-client-0393777014`.

### Shared secret for tea-agent (once, before the next `--execute`)

`telegram-integration` and `tea-agent` share `TEA_AGENT_AUTH_SECRET`. The value never goes in git. Generate it locally and create the secret (the `printf` form avoids a trailing newline):

```bash
openssl rand -base64 32
printf '%s' 'PASTE_THE_VALUE' | gcloud secrets create TEA_AGENT_AUTH_SECRET \
  --project=gen-lang-client-0393777014 \
  --replication-policy=automatic \
  --data-file=-
```

Rotate later with `gcloud secrets versions add TEA_AGENT_AUTH_SECRET --project=gen-lang-client-0393777014 --data-file=-` the same way. Do not `echo` the value (that appends a newline) and do not paste it into tickets or shell history if you can avoid it.

Both Cloud Run services run as the Compute Engine default service account. `--execute` grants that account `roles/secretmanager.secretAccessor` on `TEA_AGENT_AUTH_SECRET` (and on the other deploy secrets). To grant it yourself:

```bash
PROJECT_NUMBER=$(gcloud projects describe gen-lang-client-0393777014 --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding TEA_AGENT_AUTH_SECRET \
  --project=gen-lang-client-0393777014 \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor
```

Leave `TEA_AGENT_AUTH_SECRET` unset in local `.env` unless you want the local HTTP server to require the header too. Local polling does not call Cloud Run.

Why this is not IAM-only: the deploy path does not set a dedicated service account, so `roles/run.invoker` on the default compute account would also cover Cloud Build and any other workload on that account. A header checked in the app is what `telegram-integration` can send with one line, and it fails closed when `K_SERVICE` is set and the secret is missing.

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
- `tea-agent`: `--max-instances=1`, `--min-instances=1` (override with `TEA_AGENT_MIN_INSTANCES=0`), `--concurrency=8`, `--cpu-throttling`
- Will **not** create Cloud SQL `tea-sessions`
- `gcloud run deploy tea-agent ... --clear-cloudsql-instances`
- Secret `TEA_AGENT_AUTH_SECRET` on both services (name only; create it first or `--execute` stops)
- Two-stage Telegram: placeholder `SERVICE_URL=https://google.com`, then the real Cloud Run URL

### Execute (explicit approval)

```bash
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute
```

What `--execute` does:

1. Checks that Secret Manager already has `TEA_AGENT_AUTH_SECRET`. It does not create the value.
2. Enables required APIs, grants Cloud Run builder IAM, and grants `secretAccessor` on the deploy secrets (including `TEA_AGENT_AUTH_SECRET`) to the compute default service account.
3. Deploys `tea-agent` from source (Dockerfile: uvicorn on port 8080, copies `data/`). One instance, dev UI off, shared-secret header required.
4. Skips TEA-14 write/restart/check when there is no Cloud SQL / Agent Engine.
5. Deploys `telegram-integration` with a placeholder `SERVICE_URL` and the same auth secret (so it can call `tea-agent`).
6. Updates `SERVICE_URL` to the real Telegram service URL (webhook path `<SERVICE_URL>/<TELEGRAM_BOT_TOKEN>`).

`telegram-integration` stays `--allow-unauthenticated` because Telegram’s servers cannot send a Google identity token. `tea-agent` also stays `--allow-unauthenticated` at the proxy; the process returns 401 without `X-Tea-Agent-Token`. `/health` does not require the header (Cloud Run’s default startup probe is TCP and does not send it). Step 6 replaces Telegram env vars only. It does not remove the secret mounted in step 5.

During step 5 the webhook target is briefly `https://google.com`. Do not `/start` until the script prints **Deploy finished**.

### Switch model without a rebuild

Live `tea-agent` already reads `TEA_AGENT_MODEL` at process start. To move production to lite without `--execute`:

```bash
gcloud run services update tea-agent \
  --project=gen-lang-client-0393777014 \
  --region=europe-central2 \
  --update-env-vars=TEA_AGENT_MODEL=gemini-3.1-flash-lite
```

In-memory sessions reset on the new revision. Then send a short Telegram message (not a mixed cart) to confirm. A later `--execute` pins the same model because deploy always sets `TEA_AGENT_MODEL`.

Skip the persistence probe even when SQL is attached:

```bash
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute --skip-verify
```

### After deploy

1. Wait until both services show Ready.
2. Send `/start` in Telegram (one message; Gemini free tier is tight).
3. First reply after idle can take 20–30s (cold start). The bot says so.

### What a new revision does to users

| Backend | Taste profile after a new revision | After idle, with default min-instances 1 |
| --- | --- | --- |
| In-memory (default) | Lost | Kept on that one instance. Lost if you set `TEA_AGENT_MIN_INSTANCES=0` and Cloud Run scales to zero, or if the platform replaces the instance. |
| Cloud SQL | Kept (if TEA-14 verify passed) | Kept |
| Agent Engine sessions | Kept | Kept |

`--max-instances=1` stays even if you later attach Cloud SQL, until you change `AGENT_MAX_INSTANCES` in `telegram_integration/deploy_spec.py`. While sessions are in memory, do not raise it: a second instance has its own history. It also caps how many Gemini calls one traffic spike can start. `--concurrency=8` is the per-instance cap (Cloud Run’s default is 80, which is too many parallel `/run` calls on 1Gi). `--cpu-throttling` keeps request-based billing. Idle min-instance time is the lower idle rate (list price about $0.0000025 per vCPU-second and the same per GiB-second, so 1 vCPU + 1Gi is on the order of $13/month while the beta is quiet; check the [Cloud Run pricing](https://cloud.google.com/run/pricing) page for `europe-central2` before you rely on that). That is not the full-time vCPU price of instance-based billing (`--no-cpu-throttling`). Drop the floor with `TEA_AGENT_MIN_INSTANCES=0` on the next deploy if you would rather scale to zero.

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

- `tea-agent` env has `GOOGLE_GENAI_USE_VERTEXAI=false`, `TEA_AGENT_MODEL=gemini-3.1-flash-lite`, `TEA_AGENT_DEV_UI=false`, and, on the cheap path, `TEA_ALLOW_EPHEMERAL_SESSIONS=true`.
- `TEA_AGENT_AUTH_SECRET` is a `secretKeyRef`, not a plaintext `value`, on both services.
- No `CLOUD_SQL_INSTANCE` and no `GOOGLE_CLOUD_AGENT_ENGINE_ID` unless you opted in.
- Telegram `ADK_SERVER_URL` equals the tea-agent URL from the describe output. Do not copy that URL into git, Notion, or chat.
- Telegram `SERVICE_URL` equals the telegram-integration URL (not `https://google.com`).

Scaling (annotations `autoscaling.knative.dev/maxScale` and `minScale`, plus `containerConcurrency`):

```bash
gcloud run services describe tea-agent \
  --project=gen-lang-client-0393777014 \
  --region=europe-central2 \
  --format="yaml(spec.template.metadata.annotations,spec.template.spec.containerConcurrency)"
```

Expect `maxScale: '1'`, `minScale: '1'`, and `containerConcurrency: 8`. `run.googleapis.com/cpu-throttling` should be absent or `true` (request-based billing). `false` means you are paying for a full-time vCPU.

Lockdown check. `URL` comes from describe; an outsider has no header:

```bash
URL=$(gcloud run services describe tea-agent \
  --project=gen-lang-client-0393777014 \
  --region=europe-central2 \
  --format='value(status.url)')

curl -s -o /dev/null -w "%{http_code}\n" -X POST "$URL/run"
curl -s -o /dev/null -w "%{http_code}\n" \
  "$URL/apps/tea_agent/users/tg-1/sessions/tg-sess-1"
curl -s -o /dev/null -w "%{http_code}\n" "$URL/health"
curl -s -o /dev/null -w "%{http_code}\n" "$URL/dev-ui/"
```

Expect `401`, `401`, `200`, `401`. `/health` is the only open route. The dev UI is not mounted; with the secret, `/dev-ui/` is 404, and without it the middleware returns 401 before routing. Do not put the secret on the curl command line. Then send `/start` in Telegram. The bot should answer. If it says the sommelier is unavailable and tea-agent logs show `rejected tea-agent request`, the secret is missing on `telegram-integration` or the two services have different versions.

PowerShell: `curl.exe -s -o NUL -w "%{http_code}"` with the same URLs. `000` / timeout often means cold start — retry after 30s. With min-instances 1 the first reply should not wait on a scale-from-zero. `500` on first request after a bad revision: read logs.

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

Console: Cloud Run → service → Logs. Cloud Trace, Cloud Monitoring, and Cloud Logging are enabled on the agent image when `OTEL_TO_CLOUD` is not `false`.

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

Expect `GOOGLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TEA_AGENT_AUTH_SECRET`. Do not `gcloud secrets versions access` in chat logs.

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
| Сейчас упёрлись в лимит бесплатного Gemini… | AI Studio quota (flash-lite is higher RPD than flash; still per project) |
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

Spot-check 2–3 product URLs, prices, and stock, then commit the JSON. Listing cards mark stock on the parent `li` (`instock` / `outofstock`); if that signal is missing the row stays `unknown` and is not offered as a buy link. If category HTML returns 403, the script falls back to the WooCommerce store API (`is_in_stock`) instead of guessing. Dry-run:

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

Then put `CLOUD_SQL_INSTANCE=gen-lang-client-0393777014:europe-central2:tea-sessions` in the environment used by deploy (not git). Password is Secret Manager `SESSION_DB_PASSWORD`. Redeploy with `--execute`. TEA-14 probe (use the URL from `gcloud run services describe`, and export the auth secret so the probe is not a 401; the script also reads it from `.env` and does not print it):

```bash
URL=$(gcloud run services describe tea-agent \
  --project=gen-lang-client-0393777014 \
  --region=europe-central2 \
  --format='value(status.url)')
export TEA_AGENT_AUTH_SECRET="$(gcloud secrets versions access latest \
  --secret=TEA_AGENT_AUTH_SECRET \
  --project=gen-lang-client-0393777014)"
uv run python scripts/verify_session_persistence.py --base-url "$URL" --write
# new tea-agent revision (deploy script does this when SQL is attached)
uv run python scripts/verify_session_persistence.py --base-url "$URL" --check
unset TEA_AGENT_AUTH_SECRET
```

Without `TEA_ALLOW_EPHEMERAL_SESSIONS`, Cloud Run **refuses** sqlite/in-memory so a restart cannot silently drop profiles.

---

## 10. Optional: Memory Bank

Agent Engine Memory Bank is not hosted in `europe-central2`; setup defaults to location `eu`.

```bash
uv run python scripts/setup_memory_bank.py --project=gen-lang-client-0393777014
uv run python scripts/setup_memory_bank.py --project=gen-lang-client-0393777014 --execute
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
| Switch live model (no rebuild) | `gcloud run services update tea-agent --project=gen-lang-client-0393777014 --region=europe-central2 --update-env-vars=TEA_AGENT_MODEL=gemini-3.1-flash-lite` |
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
| Empty or quota replies | AI Studio daily/minute limits; wait, or set `TEA_AGENT_MODEL=gemini-3.6-flash` on a paid tier |
| First Telegram message hangs | Cold start; wait 30s. With min-instances 1 this should be rare |
| `--execute` stops on `TEA_AGENT_AUTH_SECRET` | Create the secret first (§2). The script will not invent a value |
| Bot replies «Не удалось открыть сессию…» or «Сбой на стороне сомелье…» and tea-agent logs `rejected tea-agent request` | Auth header missing or the two services have different secret versions |
| `curl` to tea-agent `/run` returns 200 with no header | Old revision, or `K_SERVICE` and the secret are both unset. Prod must fail closed |
| Bot works locally, Cloud Run outdated | Need `--execute` after git changes (including `data/*.json`) |
| Invented tasting notes | Tools-only rule; check slug resolve + `get_tea_card` |
| `agents-cli eval grade` ImportError `google.adk` | Use `scripts/grade_traces_local.py` |

---

## 13. Safety

- Never commit `.env`, `credentials.json`, or secret values.
- `--execute` on deploy / Cloud SQL / Memory Bank is explicit approval. Dry-run first.
- Do not change `MODEL` in `tea_agent/agent.py` unless you intend to.
- Telegram calls `telegram-integration` (webhook). Only that service calls `tea-agent` (`ADK_SERVER_URL`), with header `X-Tea-Agent-Token`. The tea-agent URL is not a public API. Do not commit it. Unauthenticated `/run` and session reads must return 401. `/health` may return 200.
