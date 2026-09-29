# Local development setup

**Last Updated:** 2026-09-28
**Status:** Alpha

This is the end-to-end walkthrough for getting Poindexter running on
your own machine. If you only want the shortest path to a first post,
follow the [Quickstart](../quickstart) — the same sequence the
`quickstart-e2e` CI job runs on a clean runner (which has no GPU, so it
stands one tiny model in for the three large ones and zeroes the quality
bar) and requires to end with a post in the approval queue.

This document covers the longer form: what the setup wizard does
under the hood, how to verify each layer, and how to troubleshoot
when something doesn't come up.

## Minimum hardware

| Tier        | GPU VRAM | What it unlocks                                                                                                            |
| ----------- | -------- | -------------------------------------------------------------------------------------------------------------------------- |
| Minimum     | 8 GB     | Runs smaller Ollama models only; writer model will be constrained to 7B range                                              |
| Recommended | 16 GB    | Supports Q4 quantized 7B–14B models at full quality                                                                        |
| Optimal     | 24 GB+   | Q4_K_M 32B writer models; comfortable headroom for image-gen + Ollama simultaneously                                       |
| CPU-only    | no GPU   | Fallback available via `pipeline_writer_model=ollama/gemma2:2b-instruct-q4_K_M` but output quality will be noticeably poor |

RAM: 32 GB recommended. The default stack (`docker-compose.consumer.yml`)
idles at roughly 4–6 GB; the operator stack (`docker-compose.local.yml`,
adding Langfuse, ClickHouse, Loki, Tempo and more) uses 8–12 GB.

Disk: about 50 GB free — 31 GB for the default models and ~13 GB for the
stack's images (measured on a clean runner); more for larger writer models
and generated media.

## 1. Prerequisites

| Tool           | Version  | Purpose                                  | Required?   |
| -------------- | -------- | ---------------------------------------- | ----------- |
| Docker         | 20.10+   | Runs the entire backend stack            | Yes         |
| Ollama         | 0.1.40+  | Local LLM inference                      | Yes         |
| Node.js        | 22+      | Frontend (Next.js) and lint-staged hooks | Yes         |
| Git + Git Bash | any      | start-stack.sh uses `bash`               | Yes         |
| Python         | 3.13     | CLI (3.14 is not supported yet)          | Yes         |
| GPU            | 8GB VRAM | Ollama inference is far faster with CUDA | Recommended |

**Windows note.** Run all commands from Git Bash or WSL. Native
`cmd.exe` and PowerShell do not work with the start scripts.
Docker Desktop must be configured to use WSL2 backend.

**Linux note.** Stock Docker Engine on Linux does not automatically
resolve `host.docker.internal` — this is a Docker Desktop feature.
The compose files add `extra_hosts: ["host.docker.internal:host-gateway"]`
to every service that calls a host-side endpoint (Ollama, image-gen, voice
bridge). `host-gateway` resolves to the Docker bridge gateway (Docker
Engine 20.10+), so a container's call arrives on the bridge interface,
**not** on loopback. A default Ollama install listens on `127.0.0.1` only
and refuses it: every model call fails. Make Ollama listen on every
interface once (firewall port 11434 if the machine is on a network you
don't trust):

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0"\n' | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

**GPU note.** You can run Poindexter on CPU, but content generation
that takes 30 seconds on an RTX 4090 can take 10+ minutes on CPU.
Not practical for daily use.

## 2. Clone and setup

```bash
git clone https://github.com/Glad-Labs/poindexter.git && cd poindexter
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e src/cofounder_agent
poindexter setup --auto   # the quick-start path: the Docker stack's own Postgres
# or: poindexter setup                                 (interactive — asks for a DB URL)
# or: poindexter setup --db-url "postgresql://..."     (non-interactive, your own Postgres)
```

`--auto` does the following:

1. **Generates secrets** — `local_postgres_password`, `grafana_password`,
   `poindexter_secret_key` (the key every encrypted `app_settings` row
   uses), the Postiz pair, and the operator-stack extras — plus
   `compose_project_name`, so every later launch lands in the same compose
   project. Re-running with `--force` keeps every value the file already
   holds: Postgres keeps the password its volume was initialised with, and
   encrypted settings need the key they were written with.
2. **Starts the stack's own Postgres** — the `postgres-local` service of
   the compose file `scripts/start-stack.sh` launches
   (`docker-compose.consumer.yml` on a public clone), in that compose
   project. The host reaches it on port 5433 (`POSTGRES_HOST_PORT`
   overrides it, and the override is written to `bootstrap.toml` so it
   sticks); every container reaches it as `postgres-local:5432`. One
   database for the CLI and the stack.
3. **Tests the connection and runs migrations**, then seeds the
   `app_settings` defaults. Safe to re-run — migrations are idempotent.
4. **Writes `~/.poindexter/bootstrap.toml`** with the database URL +
   generated secrets. This is the only config file on disk.
5. **Provisions the initial CLI OAuth client** — registers a row in
   `oauth_clients`, encrypts the credentials into
   `app_settings.cli_oauth_client_id` / `cli_oauth_client_secret`, and
   prints the plaintext secret once for capture. (Worker auth uses
   OAuth 2.1 only as of Glad-Labs/poindexter#249.)

No `.env` file is created. All secrets live in `bootstrap.toml`
(safe permissions, never committed to git).

> Older releases' `--auto` started a separate `poindexter-postgres-auto`
> container on port 5434. The stack never used it — every container
> connects to `postgres-local` — so tasks queued from the CLI were never
> seen. If you have one, `setup --auto` points it out; remove it with
> `docker rm -f poindexter-postgres-auto` once you have copied out
> anything you need.

## 3. Pull AI models

```bash
ollama pull gemma3:27b && ollama pull phi4:14b && ollama pull llama3:latest && ollama pull nomic-embed-text
```

About 31 GB, once. These are the models the default pipeline calls, as
measured on a clean runner by the `quickstart-e2e` job: `gemma3:27b` (the
writer and every role seeded with it), `phi4:14b` (the critic that hard-gates
each draft, and the Ragas/DeepEval judges), `llama3:latest` (podcast and video
script drafting) and `nomic-embed-text` (embeddings). The pipeline does not pull
models on demand: a model Ollama lacks fails the call that names it. The roles
that make up this list live in `poindexter/services/required_models.py`; the
tags come from the seeded settings, and a unit test holds the README and the
Quickstart page to them. `poindexter setup --auto` prints the same command from
_your_ database's settings, so a role you re-pointed shows your model.

Pull these only when you switch the feature on: `qwen3-vl:30b-a3b-instruct`
(image QA and captions, ~20 GB — called only when a post has images),
`qwen2.5:32b` (fallback critic), `granite4.2:3b` (the brain's alert triage) and
`qwen2.5:7b` (console chat and the voice agent). Every role is an `app_settings`
key (`pipeline_writer_model`, `pipeline_critic_model`, `video_scene_model`, ...),
so a different writer is `ollama pull <tag>` plus
`poindexter settings set pipeline_writer_model ollama/<tag>`.

## 4. Bring up the stack

```bash
bash scripts/start-stack.sh up -d
```

This reads `~/.poindexter/bootstrap.toml`, exports the values as env
vars, and runs `docker compose -f <file> up -d`, where `<file>` is
`docker-compose.consumer.yml` on a public clone (or the operator's
`docker-compose.local.yml` when the checkout has one). No `.env` file
needed; a bare `docker compose up` stops at the first `${VAR:?...}`
sentinel because nothing else exports those values. The first run
builds the images, so allow several minutes.

The default stack starts these containers:

| Container                                             | Purpose                                                                                              | Port |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ---- |
| `poindexter-postgres-local`                           | PostgreSQL 16 + pgvector — the one database                                                          | 5433 |
| `poindexter-worker`                                   | FastAPI worker API                                                                                   | 8002 |
| `poindexter-brain-daemon`                             | Health probes + self-healing loop                                                                    | —    |
| `poindexter-prefect-server`                           | Prefect API + UI (flow runs, schedules)                                                              | 4200 |
| `poindexter-prefect-services`                         | Prefect scheduler etc. — turns the deployment's cron into flow runs                                  | —    |
| `poindexter-prefect-worker`                           | Runs the content pipeline; registers the `content-generation` deployment                             | —    |
| `poindexter-prefect-redis`                            | Prefect's message broker                                                                             | —    |
| `poindexter-grafana`                                  | Monitoring dashboards                                                                                | 3000 |
| `poindexter-prometheus`                               | Metric scraper                                                                                       | 9091 |
| `poindexter-alertmanager`                             | Alert routing                                                                                        | 9093 |
| `poindexter-pipeline-bot`                             | Telegram control surface — idles (healthy) until `telegram_bot_token` and `telegram_chat_id` are set | —    |
| `poindexter-auto-embed`                               | Hourly pgvector embedding sync                                                                       | —    |
| `poindexter-backup-*`                                 | Hourly/daily dumps; offsite backup idles until configured                                            | —    |
| `poindexter-cadvisor`, `poindexter-postgres-exporter` | Container + Postgres metrics for Prometheus                                                          | —    |

Opt-in profiles: `bash scripts/start-stack.sh --profile image-gen up -d`
adds local image generation (GPU); `--profile postiz` adds the social hub.

**Dispatch.** Prefect is the only dispatcher. On its first start the
`poindexter-prefect-worker` container runs
`deploy_content_flow.py --if-missing`, which registers the
`content-generation` deployment (cron from
`app_settings.prefect_content_flow_cron`, default every two minutes)
unless it already exists. It never touches an existing deployment, so a
re-tuned cron or a paused deployment survives restarts; to roll out a
tuning change, run `python -m scripts.deploy_content_flow` from
`src/cofounder_agent`.

## 5. Verify

```bash
# Worker health
curl http://localhost:8002/api/health

# Queue a task end-to-end. The CLI finds the worker through
# app_settings.api_base_url (set POINDEXTER_API_URL to override) and waits
# for it to finish booting.
poindexter tasks create "Why Docker changed everything"
```

The task should move through `pending → in_progress → awaiting_approval`.
Prefect picks it up within two minutes; the run itself takes minutes on a
GPU. Follow along via:

```bash
docker logs -f poindexter-prefect-worker
poindexter tasks list --status awaiting_approval
```

## 6. Frontend (optional for backend dev)

If you're only iterating on the backend, skip the frontend — the
worker's API and the Grafana dashboards are all you need. If you do
want the Next.js public site running locally:

```bash
cd web/public-site
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## 7. Run the tests

```bash
cd src/cofounder_agent
poetry install --extras "pipeline qa rag"
poetry run pytest tests/unit/ -q
```

> The unit suite runs **lean** — the cross-encoder reranker (`sentence-transformers`
>
> - `torch`) is an opt-in `rerank` extra that CI deliberately skips, and the tests
>   that touch it `importorskip`. Add `poetry install --extras rerank` only if you
>   want to exercise the reranker locally.

Expected: the full unit suite passes (several thousand cases). Some tests
that depend on the `brain` module or `sentry-sdk` are skipped when running
inside Docker (these pass on the host where all modules are available).

## 8. What to do when something breaks

See [troubleshooting.md](troubleshooting).

## Configuration

All runtime configuration lives in the `app_settings` Postgres
table, not env vars. After setup, change settings with:

```bash
# Mint a JWT (or use `poindexter settings get/set` directly — the CLI
# handles auth for you). $JWT below is the value printed during setup
# or by `poindexter auth mint-token --client-id ... --client-secret ...`.

# View all settings
curl http://localhost:8002/api/settings \
  -H "Authorization: Bearer $JWT"

# Change a setting
curl -X PUT http://localhost:8002/api/settings/auto_publish_threshold \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"value": "80"}'
```

See [environment-variables.md](environment-variables) for the
bootstrap-layer reference (the few env vars Docker still needs), and
[reference/app-settings.md](../reference/app-settings) for the
full DB-layer settings catalog.
