# TeaBot how-to: run, deploy, status, and other functions

Operational guide for the Chinese-tea sommelier (Telegram → ADK agent → [tea.support](https://tea.support) facts + [teashop.by](https://teashop.by) prices + [b2btea](https://b2btea.com) shops by city). Repo commands use `uv`. Do not commit `.env`, tokens, or API keys.

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

**Default production (cheap):** Cloud Run, AI Studio Gemini key, **no Cloud SQL**, **no Memory Bank**. `PreloadMemoryTool` and `generate_memories_callback` attach only when `GOOGLE_CLOUD_AGENT_ENGINE_ID` is set. Taste profiles live in the memory of one `tea-agent` instance (`--max-instances=1`, `--min-instances=1`). Idle time does not wipe them. A new revision still does. Set `TEA_AGENT_MIN_INSTANCES=0` before deploy only if you accept scale-to-zero. City (`/city`) and currency (`/currency`) are the same session state, not Memory Bank.

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
| `GOOGLE_CLOUD_AGENT_ENGINE_ID` | **Do not set** unless you want Memory Bank. This existing variable is the only switch for preload and memory generation |
| `TEA_AGENT_AUTH_SECRET` | Leave unset locally. Set only to exercise the prod header check |
| `TEA_AGENT_MIN_INSTANCES` | Deploy only. Default `1`. `0` allows scale-to-zero |
| `TELEGRAM_ALLOWED_USER_IDS` | Closed-beta allowlist. See [Closed beta](#61-closed-beta-allowlist-and-rate-limit) |
| `TELEGRAM_ADMIN_USER_IDS` | Always allowed during the beta (still rate-limited) |
| `TELEGRAM_INVITE_CODE` | One-time `/start <code>` access. In-memory until restart |
| `TELEGRAM_ACCESS_MODE` | `auto` (default), `closed`, or `open` |
| `TELEGRAM_RATE_LIMIT_PER_MINUTE` | Default `4`. `0` disables the minute cap |
| `TELEGRAM_RATE_LIMIT_PER_DAY` | Default `30` (UTC day). `0` disables the daily cap |

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

`--execute` uses `--set-env-vars` and replaces the whole `telegram-integration` env block. Export `TELEGRAM_ALLOWED_USER_IDS` and `TELEGRAM_ADMIN_USER_IDS` again in that shell (semicolons; see [§6.1](#61-closed-beta-allowlist-and-rate-limit)), or the new revision comes up closed with an empty allowlist. The same applies to `TELEGRAM_ALLOWLIST_SECRET` and `TELEGRAM_INVITE_CODE_SECRET` when those mounts are in use.

The account that runs `--execute` needs `roles/secretmanager.secretAccessor` on `TEA_AGENT_AUTH_SECRET` so the smoke test can read it. The Compute Engine default service account already has that binding for the running services; this grant is for the human (or CI identity) invoking `gcloud`. Project Owner already includes it.

```bash
gcloud secrets add-iam-policy-binding TEA_AGENT_AUTH_SECRET \
  --project=gen-lang-client-0393777014 \
  --member="user:you@example.com" \
  --role=roles/secretmanager.secretAccessor
```

```bash
export TELEGRAM_ALLOWED_USER_IDS='111111;222222;333333;444444;555555'
export TELEGRAM_ADMIN_USER_IDS='111111'
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute
```

What `--execute` does:

1. Checks that Secret Manager already has `TEA_AGENT_AUTH_SECRET`. It does not create the value.
2. Enables required APIs, grants Cloud Run builder IAM, and grants `secretAccessor` on the deploy secrets (including `TEA_AGENT_AUTH_SECRET`) to the compute default service account.
3. Deploys `tea-agent` from source (Dockerfile: uvicorn on port 8080, copies `data/`). One instance, dev UI off, shared-secret header required.
4. Skips TEA-14 write/restart/check when there is no Cloud SQL / Agent Engine.
5. Deploys `telegram-integration` with a placeholder `SERVICE_URL` and the same auth secret (so it can call `tea-agent`).
6. Updates `SERVICE_URL` to the real Telegram service URL (webhook path `<SERVICE_URL>/<TELEGRAM_BOT_TOKEN>`).
7. Smoke-tests `tea-agent` only (see below). A failure exits non-zero. The new revision is already serving; do not `/start` until the script prints **Deploy finished**.

`telegram-integration` stays `--allow-unauthenticated` because Telegram’s servers cannot send a Google identity token. `tea-agent` also stays `--allow-unauthenticated` at the proxy; the process returns 401 without `X-Tea-Agent-Token`. `/health` does not require the header (Cloud Run’s default startup probe is TCP and does not send it). Step 6 replaces Telegram env vars only. It does not remove the secret mounted in step 5.

During step 5 the webhook target is briefly `https://google.com`. Do not `/start` until the script prints **Deploy finished**.

### Post-deploy smoke

In-memory sessions mean the old TEA-14 write/restart/check does not run, so step 7 is what checks that chat still works. It calls `tea-agent` only:

1. `GET /health` returns 200.
2. `POST /run` without `X-Tea-Agent-Token` returns 401.
3. Creates session `tea-smoke-1` for user `tea-smoke` (not a Telegram id, not `tg-<digits>`).
4. `POST /run` with the token and the message `Привет` returns a non-empty answer. The token is read with `gcloud secrets versions access latest --secret=TEA_AGENT_AUTH_SECRET` and is not printed.
5. `DELETE`s that session when the API allows it. A delete that the API refuses does not fail a chat that already answered.

The smoke test does not call `telegram-integration`, does not send a Telegram message, and does not spend `TELEGRAM_RATE_LIMIT_PER_MINUTE` or `TELEGRAM_RATE_LIMIT_PER_DAY`.

Rerun the check without deploying, or skip it:

```bash
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --smoke-only
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute --skip-smoke
```

`--skip-verify` still runs the smoke test. `--skip-smoke` is the switch that turns the chat check off. A passing smoke spends one Gemini request on the shared `GOOGLE_API_KEY` (the message is `Привет`).

### Switch model without a rebuild

Live `tea-agent` already reads `TEA_AGENT_MODEL` at process start. To move production to lite without `--execute`:

```bash
gcloud run services update tea-agent \
  --project=gen-lang-client-0393777014 \
  --region=europe-central2 \
  --update-env-vars=TEA_AGENT_MODEL=gemini-3.1-flash-lite
```

In-memory sessions reset on the new revision. Then send a short Telegram message (not a mixed cart) to confirm. A later `--execute` pins the same model because deploy always sets `TEA_AGENT_MODEL`.

Skip the persistence probe even when SQL is attached. The smoke test still runs unless you also pass `--skip-smoke`:

```bash
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute --skip-verify
```

### After deploy

1. The script already required `GET /health` 200 and one non-empty `tea-agent` answer, unless you passed `--skip-smoke`.
2. Send `/start` in Telegram (one message; Gemini free tier is tight). That path is `telegram-integration`, which the smoke test does not call.
3. First reply after idle can take 20–30s (cold start). The bot says so. With min-instances 1 this should be rare.

### What a new revision does to users

| Backend | Taste profile after a new revision | After idle, with default min-instances 1 |
| --- | --- | --- |
| In-memory (default) | Lost | Kept on that one instance. Lost if you set `TEA_AGENT_MIN_INSTANCES=0` and Cloud Run scales to zero, or if the platform replaces the instance. |
| Cloud SQL | Kept (if TEA-14 verify passed) | Kept |
| Agent Engine sessions | Kept | Kept |

`--max-instances=1` stays even if you later attach Cloud SQL, until you change `AGENT_MAX_INSTANCES` in `telegram_integration/deploy_spec.py`. While sessions are in memory, do not raise it: a second instance has its own history. It also caps how many Gemini calls one traffic spike can start. `--concurrency=8` is the per-instance cap (Cloud Run’s default is 80, which is too many parallel `/run` calls on 1Gi). `--cpu-throttling` keeps request-based billing. Idle min-instance time is the lower idle rate (list price about $0.0000025 per vCPU-second and the same per GiB-second, so 1 vCPU + 1Gi is on the order of $13/month while the beta is quiet; check the [Cloud Run pricing](https://cloud.google.com/run/pricing) page for `europe-central2` before you rely on that). That is not the full-time vCPU price of instance-based billing (`--no-cpu-throttling`). Drop the floor with `TEA_AGENT_MIN_INSTANCES=0` on the next deploy if you would rather scale to zero.

A new telegram-integration revision also clears in-memory invite-code grants and per-user rate counters. Ids in `TELEGRAM_ALLOWED_USER_IDS` and `TELEGRAM_ADMIN_USER_IDS` are read again from the environment and stay approved. See [Closed beta](#61-closed-beta-allowlist-and-rate-limit).

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

Console: Cloud Run → service → Logs. Cloud Trace, Cloud Monitoring, and Cloud Logging are enabled on the agent image when `OTEL_TO_CLOUD` is not `false`. After tea-agent starts, one line from `tea_agent.memory` says `Memory Bank: off` or `Memory Bank: on`.

Beta feedback (👍 / 👎 and `/feedback`) is a structured Cloud Logging record on `tea-agent`, not the in-memory chat session. Query it with [§6.2](#62-in-chat-feedback).

### Cloud SQL / Memory Bank (should be empty on the cheap path)

```bash
gcloud sql instances list --project=gen-lang-client-0393777014
```

Empty table = no always-on Postgres bill. If `tea-sessions` exists and you are not using it, delete it in the console or:

```bash
gcloud sql instances delete tea-sessions --project=gen-lang-client-0393777014
```

That is destructive. Detaching SQL from Cloud Run (`--clear-cloudsql-instances`) does **not** stop instance billing.

Memory Bank: if `GOOGLE_CLOUD_AGENT_ENGINE_ID` is absent from tea-agent env, Memory Bank is off. The process logs that once (`Memory Bank: off` or `Memory Bank: on`). With it off, a turn does not preload memories or call `add_session_to_memory`.

### Secrets exist (names only)

```bash
gcloud secrets list --project=gen-lang-client-0393777014 --format="value(name)"
```

Expect `GOOGLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TEA_AGENT_AUTH_SECRET`. Do not `gcloud secrets versions access` in chat logs. The deploy smoke test reads that secret inside the process and does not print it.

---

## 5.1 Alerts (log-based metrics)

Prod used to notice `TEA_QUOTA`, `TEA_AGENT_ERROR`, and HTTP 5xx only when someone read the chat. `scripts/setup_alerting.py` creates the log-based metrics and the alert policies. Dry-run leaves GCP unchanged. `--execute` applies them. It does not deploy Cloud Run.

Region is `europe-central2` (`CLOUD_RUN_REGION` in `telegram_integration/deploy_spec.py`). Pass `--region` if that constant changes.

### What is paged

| Signal | Log | Pilot threshold |
| --- | --- | --- |
| `TEA_AGENT_ERROR` | `telegram-integration` line `error_code=TEA_AGENT_ERROR` | any match in 5 minutes |
| `TEA_QUOTA` | `telegram-integration` line `error_code=TEA_QUOTA` | any match in 5 minutes |
| `tea-agent` HTTP 5xx | request log `run.googleapis.com/requests`, status 500–599 | more than 2 in 5 minutes |
| `telegram-integration` HTTP 5xx | same request log for that service | more than 2 in 5 minutes |

`_log_agent_failure` in `telegram_integration/main.py` writes `agent failed for telegram user <id> error_code=<code> status=<status>` with the stdlib format `%(asctime)s %(levelname)s %(name)s: %(message)s`. `TEA_QUOTA` is a warning. `TEA_AGENT_ERROR` is `logger.exception` (ERROR plus a traceback). Cloud Run stores that stdout line in `textPayload`. A JSON line or a `log_struct` record stores the same words in `jsonPayload.message`, and may set `jsonPayload.error_code`. Each TEA_* filter matches all three.

5xx uses the Cloud Run request log, not the application stderr line. One cold-start 503 during a deploy stays under the threshold. Three 5xx responses in five minutes pages. A single free-tier Gemini 429 pages as `TEA_QUOTA` on purpose for this 5-person pilot.

Metric names: `tea_agent_error_count`, `tea_quota_count`, `tea_agent_5xx_count`, `telegram_integration_5xx_count`. Policies are named `TeaBot TEA_AGENT_ERROR`, `TeaBot TEA_QUOTA`, `TeaBot tea-agent 5xx`, and `TeaBot telegram-integration 5xx`. A second `--execute` reuses the email channel and updates a metric or policy only when the filter or threshold drifted.

### One-time IAM and APIs

Project Owner already has these roles. Bind them when the runner is a narrower account. Replace `you@example.com`.

```bash
PROJECT=gen-lang-client-0393777014
MEMBER="user:you@example.com"

gcloud services enable logging.googleapis.com monitoring.googleapis.com --project="$PROJECT"

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="$MEMBER" \
  --role=roles/logging.configWriter

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="$MEMBER" \
  --role=roles/monitoring.alertPolicyEditor

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="$MEMBER" \
  --role=roles/monitoring.notificationChannelEditor
```

`roles/serviceusage.serviceUsageAdmin` is what `gcloud services enable` needs. Owner includes it. `roles/logging.configWriter` creates the log-based metrics. `roles/monitoring.alertPolicyEditor` and `roles/monitoring.notificationChannelEditor` create the policies and the email channel. The script enables the two APIs on `--execute` as well.

Deploy smoke is separate: whoever runs `scripts/deploy_cloud_run.py --execute` or `--smoke-only` needs `roles/secretmanager.secretAccessor` on `TEA_AGENT_AUTH_SECRET` (see [§4](#4-deploy-cloud-run)).

### Apply

```bash
uv run python scripts/setup_alerting.py \
  --project=gen-lang-client-0393777014 \
  --notify-email=you@example.com

uv run python scripts/setup_alerting.py \
  --project=gen-lang-client-0393777014 \
  --notify-email=you@example.com \
  --execute
```

`TEA_ALERT_EMAIL` is the same address when you omit `--notify-email`.

Google sends a verification link to that inbox. Alerts do not send until the channel is verified:

```bash
gcloud monitoring channels list \
  --project=gen-lang-client-0393777014 \
  --format='table(displayName,labels.email_address,verificationStatus,enabled)'
```

Expect `verificationStatus` `VERIFIED`.

### Force one TEA_AGENT_ERROR and confirm the alert

This writes a log entry. It does not call the bot, does not spend a rate limit, and does not send Telegram. The text is the same marker the process emits (`error_code=TEA_AGENT_ERROR`); the live line also has a timestamp, `ERROR telegram_integration:`, and usually a traceback.

```bash
PROJECT=gen-lang-client-0393777014
REGION=europe-central2

gcloud logging write tea-alert-probe \
  "agent failed for telegram user 0 error_code=TEA_AGENT_ERROR status=500" \
  --payload-type=text \
  --severity=ERROR \
  --project="$PROJECT" \
  --resource="type=cloud_run_revision,project_id=${PROJECT},location=${REGION},service_name=telegram-integration,revision_name=alert-probe,configuration_name=alert-probe"
```

Confirm the entry is visible to the same filter the metric uses:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="telegram-integration" AND resource.labels.location="europe-central2" AND (textPayload:"error_code=TEA_AGENT_ERROR" OR jsonPayload.message:"error_code=TEA_AGENT_ERROR" OR jsonPayload.error_code="TEA_AGENT_ERROR")' \
  --project="$PROJECT" \
  --freshness=30m \
  --limit=5
```

The metric can lag a couple of minutes. The alert alignment window is 5 minutes, so the mail often arrives within about 10 minutes, and only after the channel is verified.

Optional: prove the `jsonPayload` clause with a second write. That counts as another event (the threshold is already “any”).

```bash
gcloud logging write tea-alert-probe \
  '{"message":"agent failed for telegram user 0 error_code=TEA_AGENT_ERROR status=500","error_code":"TEA_AGENT_ERROR"}' \
  --payload-type=json \
  --severity=ERROR \
  --project="$PROJECT" \
  --resource="type=cloud_run_revision,project_id=${PROJECT},location=${REGION},service_name=telegram-integration,revision_name=alert-probe,configuration_name=alert-probe"
```

A real chat failure takes the same path: `telegram-integration` calls `tea-agent` `POST /run`, gets a non-quota error, and `logger.exception` writes `error_code=TEA_AGENT_ERROR`. You do not need a broken chat to test the policy.

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
| Бот сейчас в закрытой бете… | Telegram user is not on the allowlist, not an admin, and has not redeemed the invite code in this process |
| Код не подошёл… | `/start` argument did not match `TELEGRAM_INVITE_CODE` |
| Слишком много сообщений за минуту… | Per-user minute cap (`TELEGRAM_RATE_LIMIT_PER_MINUTE`, default 4) |
| На сегодня сообщений достаточно… | Per-user UTC daily cap (`TELEGRAM_RATE_LIMIT_PER_DAY`, default 30) |

---

## 6.1 Closed beta allowlist and rate limit

Everyone shares one free-tier Gemini key. During the pilot and the closed beta, `telegram-integration` refuses unknown Telegram users **before** it calls `tea-agent`, and caps how many agent turns each approved user can send.

This section is the owner runbook. Shipping the code does not by itself change the live bot: deploy is a separate explicit `--execute`.

### Behavior

| Situation | Who can talk to tea-agent |
| --- | --- |
| Local polling, no allowlist and no invite code (`TELEGRAM_ACCESS_MODE` unset / `auto`) | Anyone. This keeps `uv run python -m telegram_integration` usable. |
| Local polling with an allowlist or an invite code | Only those ids, admins, and people who redeem the code in this process |
| Cloud Run webhook, mode `auto`, nothing else set | Nobody. Fail closed. |
| Deployed telegram-integration | `TELEGRAM_ACCESS_MODE=closed` is set by `telegram_integration/deploy_spec.py`, so the gate is on even if the id list is still empty |
| `TELEGRAM_ACCESS_MODE=open` | Anyone, including on Cloud Run. Leave this unset for the beta |
| Admin id | Always allowed when the gate is on, including when the id is missing from the allowlist. The same per-user rate limit applies |

A refused user gets a short Russian closed-beta reply. Their text is not sent to `tea-agent`. The same allowlist check runs for inline-button presses, including 👍 / 👎. Rating a reply and `/feedback` do not call the model; see [In-chat feedback](#62-in-chat-feedback).

### Defaults

| Variable | Default |
| --- | --- |
| `TELEGRAM_ACCESS_MODE` | `auto` in the process. Cloud Run deploy writes `closed` |
| `TELEGRAM_RATE_LIMIT_PER_MINUTE` | `4` |
| `TELEGRAM_RATE_LIMIT_PER_DAY` | `30` (resets at 00:00 UTC) |

`0` on a rate-limit variable turns that bucket off. Any other non-number keeps the default. The minute window is a fixed unix minute (a user can send 4 just before the boundary and 4 just after). Text messages to the sommelier and next-step buttons (мягче, дешевле, купить, магазины рядом, and the rest) both count. `/start` by itself does not. A wrong invite code does, so guessing is capped. 👍, 👎, `/help`, `/city`, `/currency`, `/feedback`, and the follow-up message `/feedback` asks for do not count. A city sent right after `/city` with no argument does not count either.

Five pilot testers at the daily cap are 150 agent calls. Before the 30–50 person beta, lower `TELEGRAM_RATE_LIMIT_PER_DAY` if the shared Gemini key is close to its project quota.

### What is remembered after a restart

`telegram-integration` keeps invite approvals and rate counters in process memory. There is one Cloud Run instance for the beta; a second instance would not share that memory. Nothing is written to disk or to the ADK session.

| Data | After a new revision, crash, or scale-to-zero |
| --- | --- |
| `TELEGRAM_ALLOWED_USER_IDS` | Still approved. Read from env / Secret Manager on startup |
| `TELEGRAM_ADMIN_USER_IDS` | Still approved |
| Invite-code grants | Forgotten. The code still works; the tester sends `/start <code>` again |
| Minute and daily counters | Reset |

When someone redeems a code, the log line is `invite redeemed by telegram user <id>`. Copy that id into the allowlist if they should not have to send the code again after the next deploy. A blocked stranger is logged as `blocked non-allowlisted telegram user <id>` (the message text is not logged).

### Add the 5 pilot testers

No code change. You need each person's numeric Telegram id and your own id as admin.

1. Collect ids. Each tester opens `@userinfobot` and sends you the `Id` number. Or, once this revision is deployed and the bot is closed, they send `/start` and you read `blocked non-allowlisted telegram user <id>`:

```bash
gcloud run services logs read telegram-integration --project=gen-lang-client-0393777014 --region=europe-central2 --limit=80
```

2. Put your id in `TELEGRAM_ADMIN_USER_IDS` and the five tester ids in `TELEGRAM_ALLOWED_USER_IDS`. Separate ids with semicolons. gcloud splits env assignments on commas, so a comma inside the id list is parsed as another variable.

3. If this revision is not live yet, export the ids in the same shell as the deploy so both stages of `scripts/deploy_cloud_run.py` keep them. Dry-run first. `--execute` still needs explicit approval and is not part of the code change:

```bash
export TELEGRAM_ALLOWED_USER_IDS='111111;222222;333333;444444;555555'
export TELEGRAM_ADMIN_USER_IDS='111111'
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute
```

The script also sets `TELEGRAM_ACCESS_MODE=closed`, `TELEGRAM_RATE_LIMIT_PER_MINUTE=4`, and `TELEGRAM_RATE_LIMIT_PER_DAY=30`.

4. If the revision is already running, add testers with a service update (new revision; invite grants and rate counters reset; the allowlist is loaded from env):

```bash
gcloud run services update telegram-integration --project=gen-lang-client-0393777014 --region=europe-central2 --update-env-vars=TELEGRAM_ALLOWED_USER_IDS=111111;222222;333333;444444;555555,TELEGRAM_ADMIN_USER_IDS=111111
```

5. Check. Each of the five sends `/start` and gets the sommelier greeting. A sixth account gets the closed-beta reply and does not reach tea-agent. Confirm the env without printing secrets:

```bash
gcloud run services describe telegram-integration --project=gen-lang-client-0393777014 --region=europe-central2 --format="yaml(spec.template.spec.containers[0].env)"
```

A later `--execute` uses `--set-env-vars` and replaces the whole env block. Export `TELEGRAM_ALLOWED_USER_IDS` and `TELEGRAM_ADMIN_USER_IDS` again in that shell, or the new revision comes up closed with an empty allowlist.

### Invite code (when you do not have the numeric id yet)

The code is one token. For a `t.me/<bot>?start=<code>` link, use letters, digits, `_`, and `-`, up to 64 characters. The tester can also type `/start <code>`.

Prefer Secret Manager so the code is not a plaintext Cloud Run env value. Create it once (type the code, then Ctrl-D; do not commit it):

```bash
gcloud secrets create TELEGRAM_INVITE_CODE --project=gen-lang-client-0393777014 --data-file=-
```

If the secret already exists, add a version instead of `create`:

```bash
gcloud secrets versions add TELEGRAM_INVITE_CODE --project=gen-lang-client-0393777014 --data-file=-
```

Mount it on the running service:

```bash
gcloud run services update telegram-integration --project=gen-lang-client-0393777014 --region=europe-central2 --update-secrets=TELEGRAM_INVITE_CODE=TELEGRAM_INVITE_CODE:latest
```

The bot reads the env var `TELEGRAM_INVITE_CODE`. A matching `/start` adds that user only until this process exits.

To keep that mount across the next full deploy, export `TELEGRAM_INVITE_CODE_SECRET=1` in the deploy shell. The deploy command's `--set-secrets` list otherwise contains `TELEGRAM_BOT_TOKEN` and `TEA_AGENT_AUTH_SECRET`, and drops the invite secret. The same pattern exists for the allowlist: store semicolon- or comma-separated ids in secret `TELEGRAM_ALLOWED_USER_IDS` and export `TELEGRAM_ALLOWLIST_SECRET=1`. A plaintext `TELEGRAM_INVITE_CODE` in the deploy shell is forwarded only when it has no commas and no spaces.

### Change the caps

```bash
gcloud run services update telegram-integration --project=gen-lang-client-0393777014 --region=europe-central2 --update-env-vars=TELEGRAM_RATE_LIMIT_PER_MINUTE=4,TELEGRAM_RATE_LIMIT_PER_DAY=30
```

---

## 6.2 In-chat feedback

Testers can rate a recommendation and send a note without spending a Gemini call. The allowlist from [§6.1](#61-closed-beta-allowlist-and-rate-limit) still applies. 👍, 👎, `/feedback`, the follow-up those ask for, `/help`, `/city`, and `/currency` do not spend `TELEGRAM_RATE_LIMIT_PER_MINUTE` or `TELEGRAM_RATE_LIMIT_PER_DAY`. Next-step buttons, including «магазины рядом», still do.

| Tester action | What they see | What is stored |
| --- | --- | --- |
| 👍 or 👎 under a recommendation, on the same keyboard as мягче / дешевле / купить | «Спасибо, записал.» or, after 👎, a one-line ask for a reason | `source=thumbs`, `rating=up` (`score=1`) or `rating=down` (`score=0`), plus `user_id`, `session_id`, `message_id`, and a short `reply_excerpt` of that Telegram message |
| Next message after 👎 | «Спасибо, отзыв записан.» The message is not sent to the sommelier | Second line, `source=thumbs_reason`, same `message_id` and excerpt, `text` is the reason |
| «Пропустить» after 👎 | «Хорошо. Дальше пишите сомелье как обычно.» | Nothing new. The 👎 line is already stored. The next message goes to the sommelier |
| `/feedback текст` | «Спасибо, отзыв записан.» | `source=command`, `text` is the note, no score |
| `/feedback` with no text | Asks for the next message. That message is stored the same way and is not sent to the sommelier | `source=command` |
| `/help` | Short Russian памятка (how to ask, what the buttons do, how to send a note). Allowed users only; others get the closed-beta refusal | Nothing |

A 👎 is logged immediately, so it is kept even if the tester never explains. The "waiting for a reason" flag is process memory on `telegram-integration`. A new revision or restart drops the flag; the 👎 line in Cloud Logging stays. If they reply after that restart, the text goes to the sommelier.

`/help` is the beta FAQ. The Notion card asked for a short tester text and did not include a longer FAQ, so the in-chat памятка is the whole FAQ for this pilot.

The buttons are only on replies that already have the next-step keyboard. A plain question, an error, or `/start` has no 👍 / 👎. The row is two buttons (`tea:f:up`, `tea:f:down`). Callback data stays under Telegram's 64-byte cap, and the extra row stays inside the 100-button cap. Tapping one does not call `POST /run`.

### Where it is stored

Sessions are in memory, so feedback is not written to the ADK session. `telegram-integration` POSTs the JSON to tea-agent `POST /feedback` with the same `X-Tea-Agent-Token` header as `/run` (secret `TEA_AGENT_AUTH_SECRET`). tea-agent writes one structured Cloud Logging record (`jsonPayload.log_type` = `feedback`, log name `tea-sommelier.feedback`). That record survives a restart and a new revision.

Fields you will filter on:

| Field | Meaning |
| --- | --- |
| `log_type` | Always `feedback` |
| `user_id` | `tg-<telegram id>` |
| `session_id` | `tg-sess-<telegram id>` (same ids the bot uses for `/run`) |
| `source` | `thumbs`, `thumbs_reason`, or `command` |
| `rating` | `up`, `down`, or empty for a free-text note |
| `score` | `1`, `0`, or absent for a free-text note |
| `message_id` | Telegram message id of the recommendation that was rated |
| `reply_excerpt` | Plain text of that message, one line, clipped |
| `text` | The tester's note, if any |

If tea-agent rejects the POST (401, timeout, 5xx), `telegram-integration` writes the same JSON itself so the tap is not dropped. Local polling without `ADK_SERVER_URL` only has that local write: a structured log when Google credentials exist, otherwise one stdout line that starts with `feedback` and contains the JSON.

### Read it after the pilot

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="tea-agent" AND jsonPayload.log_type="feedback"' \
  --project=gen-lang-client-0393777014 \
  --freshness=30d \
  --limit=200 \
  --format='table(timestamp, jsonPayload.user_id, jsonPayload.rating, jsonPayload.source, jsonPayload.text, jsonPayload.reply_excerpt)'
```

Downvotes only:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="tea-agent" AND jsonPayload.log_type="feedback" AND jsonPayload.rating="down"' \
  --project=gen-lang-client-0393777014 \
  --freshness=30d \
  --limit=100 \
  --format=json
```

One tester (`user_id` is `tg-` plus the numeric Telegram id):

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="tea-agent" AND jsonPayload.log_type="feedback" AND jsonPayload.user_id="tg-111111"' \
  --project=gen-lang-client-0393777014 \
  --freshness=30d \
  --limit=50 \
  --format=json
```

Fallback, when the POST did not reach tea-agent (same payload, on the Telegram service):

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="telegram-integration" AND (jsonPayload.log_type="feedback" OR textPayload:"tea-sommelier.feedback")' \
  --project=gen-lang-client-0393777014 \
  --freshness=30d \
  --limit=50 \
  --format=json
```

Console: Logging → Logs Explorer, resource Cloud Run Revision, service `tea-agent`, query `jsonPayload.log_type="feedback"`.

This code does not deploy and does not read or change the live webhook. The lines show up after the next approved `--execute`.

---

## 6.3 Local shops and `/city`

Buy links stay teashop.by product pages (`find_in_shop`, chip «Купить»). Shops near the user come from the b2btea directory already on the tea API: `GET https://api.thetea.app/api/v2/companies` (`source: b2btea.com`). A hit is a shop, not a SKU and not a price. The bot does not scrape the b2btea homepage.

`find_local_shops(country, city, tea_slug)` returns at most 3 shops. It keeps `retail`, `tea_house`, and `ecommerce` rows that have a website and `china_focus` of `strong` or `core` (`china_focus` is filtered here; the server ignores that parameter). The tea class comes from the tea card. `red` is queried as directory key `black`. A cultivar such as biluochun is queried as `green`, and the reply says the shop carries that class. Same-city shops come first, then online shops in that country (`type=ecommerce` without a city). Keyless calls stay inside the 10-row cap and do not send `offset`.

Each shown shop has the card URL the API returns (`url`, shaped `https://b2btea.com/{lang}/c/{slug}/`) and the shop `website`. No other shop links. If nothing matches, the bot says so. If the directory times out, the tea recommendation still stands.

The user has no location until they say it. The bot asks for a city once and stores `city` and `country` in the ADK session (the same in-memory session as the taste profile on this pilot — not Cloud SQL). `/city Warsaw`, `/city Варшава`, or `/city Warsaw, Poland` sets it without a Gemini call. `/city` with no argument asks; the next short reply is saved the same way. A tea question after that ask still goes to the sommelier. Common cities supply a country (Warsaw → Poland, Минск → Belarus). There is no Telegram location pin and no geocoding.

The next-step chip «магазины рядом» is separate from «Купить». It calls the model. Only URLs returned by `find_local_shops` are shown.

No new environment variable. Deploy is still:

```bash
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute
```

`--execute` replaces the whole env block. Export `TELEGRAM_ALLOWED_USER_IDS` and `TELEGRAM_ADMIN_USER_IDS` again in that shell if the live bot uses them. Nothing new has to be exported for shops, `/city`, or `/currency` ([§6.4](#64-vitrine-prices-and-currency)).

A new tea-agent revision drops in-memory session state, including the saved city, the same way it drops the taste profile.

---

## 6.4 Vitrine prices and `/currency`

`find_in_shop` prices come from the teashop.by catalog as `price_from_byn`. The number a tester sees is EUR unless they pick another currency. USD and BYN are the other two. A saved city does not pick the currency, and neither does a budget such as «до 20 евро».

`/currency USD` (also `EUR` or `BYN`, any letter case) stores `currency` on the same in-memory ADK session as the city. `/currency` with no argument sends three inline buttons. The command does not call the model and does not spend a rate-limit token. The allowlist still runs first. `/help` and `/start` mention it.

The bot converts in code, not in the model. National Bank of Belarus is tried first (`https://api.nbrb.by/exrates/rates/{EUR|USD}?parammode=2`, 3 second timeout). If that fails, the keyless fallback is `https://open.er-api.com/v6/latest/EUR` (credited in the reply as ExchangeRate-API). A good quote is kept in the `tea-agent` process for 24 hours, or from 12 hours onward once the calendar day is past the rate date. One failed lookup is not retried for 15 minutes, so a dead host does not stall every tea in the same answer. If both hosts fail, the reply shows the BYN amount only and does not invent a rate.

A price line leads with the chosen currency and keeps the original BYN, the source, and that source's date:

```text
~7,39 EUR (25,00 BYN, курс NBRB 04.10.2026) — Дунтин Би Ло Чунь
```

BYN has no conversion: `25,00 BYN — Дунтин Би Ло Чунь`. The block is `### На витрине`. It is teashop.by only. A b2btea card under `### Где рядом` never gets that amount. If `price_from_byn` is missing, that tea has no number.

No new environment variable. Cloud Run does not set a VPC connector or `--vpc-egress`, so `tea-agent` already reaches both hosts on the default internet path. `telegram-integration` does not call them; it only writes the currency onto the session.

Deploy is still the same script. `--execute` replaces the whole env block, so export the allowlist again:

```bash
export TELEGRAM_ALLOWED_USER_IDS='111111;222222;333333;444444;555555'
export TELEGRAM_ADMIN_USER_IDS='111111'
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014
uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --execute
```

A new tea-agent revision drops the saved currency along with the city and the taste profile.

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

Then set `GOOGLE_CLOUD_AGENT_ENGINE_ID` (and usually `GOOGLE_CLOUD_AGENT_ENGINE_LOCATION=eu`) and redeploy. No new environment variables: that id is the switch. `PreloadMemoryTool` and `generate_memories_callback` attach on the root agent only when it is set (sub-agents do not carry them). When it is unset, those hooks are absent, a turn does not search or write memories, and the prompt does not tell the model it has facts from past sessions. Taste profile, `/city`, and `/currency` stay in session state.

Startup logs one line (`tea_agent.memory`): `Memory Bank: off (GOOGLE_CLOUD_AGENT_ENGINE_ID unset)` or `Memory Bank: on (GOOGLE_CLOUD_AGENT_ENGINE_ID is set)`.

To confirm per-turn prompt tokens dropped on the cheap path, send two short messages in one Telegram chat after the new revision. In Cloud Trace, open the second turn's span `generate_content <TEA_AGENT_MODEL>` (today `generate_content gemini-3.1-flash-lite`) and read `gen_ai.usage.input_tokens`. Before this gate, `PreloadMemoryTool` inserted a `<PAST_CONVERSATIONS>` user turn copied from the same session, so that attribute counted the chat twice. After, the request has no `PAST_CONVERSATIONS` (also visible on `gen_ai.input.messages` when the trace keeps content). There is no `execute_tool preload_memory` span in either revision: ADK runs this tool while building the model request, not as a function call. The parent span `invoke_agent tea_sommelier` carries the same token totals when experimental telemetry is on. A local trace from `agents-cli eval generate` should likewise contain neither `PAST_CONVERSATIONS` nor `preload_memory` when the engine id is unset.

---

## 11. Other functions (cheat sheet)

| Task | Command |
| --- | --- |
| Install deps | `agents-cli install` |
| Playground | `agents-cli playground` |
| Unit + integration tests | `uv run pytest tests/unit tests/integration` |
| Lint | `agents-cli lint` |
| Local Telegram | `uv run python -m telegram_integration` |
| Add beta testers | [§6.1](#61-closed-beta-allowlist-and-rate-limit) (`TELEGRAM_ALLOWED_USER_IDS`, semicolons) |
| Read beta feedback | [§6.2](#62-in-chat-feedback) (`jsonPayload.log_type="feedback"` on `tea-agent`) |
| Local shops / city | [§6.3](#63-local-shops-and-city) (`/city`, no new env) |
| Vitrine currency | [§6.4](#64-vitrine-prices-and-currency) (`/currency`, no new env) |
| Local tea-agent HTTP | `uv run uvicorn tea_agent.fast_api_app:app --host 127.0.0.1 --port 8080` |
| Upsert secrets | `uv run python scripts/setup_secret_manager.py` |
| Deploy dry-run | `uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014` |
| Deploy | same + `--execute` (re-export allowlist ids; smoke runs after) |
| Smoke only | `uv run python scripts/deploy_cloud_run.py --project=gen-lang-client-0393777014 --smoke-only` |
| Alerting dry-run | `uv run python scripts/setup_alerting.py --project=gen-lang-client-0393777014 --notify-email=you@example.com` |
| Alerting apply | same + `--execute` ([§5.1](#51-alerts-log-based-metrics)) |
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
| Smoke: `GET /health` is not 200 | Revision not ready, or the process is crash-looping. Rerun `--smoke-only` after a minute |
| Smoke: `POST /run` without the header is not 401 | Lockdown missing on this revision. See the curl check in §5 |
| Smoke: answer text was empty, or `/run` was not 200 | Read `tea-agent` logs. The new revision is already serving |
| Smoke: could not read `TEA_AGENT_AUTH_SECRET` | Grant `roles/secretmanager.secretAccessor` to the account running the script (§4). The value is not printed |
| No alert email after a forced `TEA_AGENT_ERROR` | Channel `verificationStatus` is still unverified, or the 5-minute alignment has not closed (§5.1) |
| Allowlist empty after deploy | `--set-env-vars` replaced the env block. Export `TELEGRAM_ALLOWED_USER_IDS` and `TELEGRAM_ADMIN_USER_IDS` again (§6.1) |
| Bot replies «Не удалось открыть сессию…» or «Сбой на стороне сомелье…» and tea-agent logs `rejected tea-agent request` | Auth header missing or the two services have different secret versions |
| `curl` to tea-agent `/run` returns 200 with no header | Old revision, or `K_SERVICE` and the secret are both unset. Prod must fail closed |
| Bot works locally, Cloud Run outdated | Need `--execute` after git changes (including `data/*.json`) |
| Invented tasting notes | Tools-only rule; check slug resolve + `get_tea_card` |
| `agents-cli eval grade` ImportError `google.adk` | Use `scripts/grade_traces_local.py` |

---

## 13. Safety

- Never commit `.env`, `credentials.json`, or secret values.
- `--execute` on deploy / Cloud SQL / Memory Bank / `scripts/setup_alerting.py` is explicit approval. Dry-run first.
- The deploy smoke test reads `TEA_AGENT_AUTH_SECRET` and does not print it. Do not pass that value on the command line.
- Do not change `MODEL` in `tea_agent/agent.py` unless you intend to.
- Telegram calls `telegram-integration` (webhook). Only that service calls `tea-agent` (`ADK_SERVER_URL`), with header `X-Tea-Agent-Token`. The tea-agent URL is not a public API. Do not commit it. Unauthenticated `/run` and session reads must return 401. `/health` may return 200.
