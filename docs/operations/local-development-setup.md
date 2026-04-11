# Local development setup

**Last Updated:** 2026-04-11
**Status:** Alpha

This is the end-to-end walkthrough for getting Poindexter running on
your own machine. If you only want "one command to a working
pipeline," the `bootstrap.sh` script does most of this for you —
read [Quick Start in the README](../../README.md#quick-start) first.

This document covers the longer form: what the bootstrap is doing
under the hood, how to verify each layer, and how to troubleshoot
when something doesn't come up.

## 1. Prerequisites

| Tool           | Version  | Purpose                                          | Required?   |
| -------------- | -------- | ------------------------------------------------ | ----------- |
| Docker Desktop | 4.26+    | Runs the entire backend stack                    | Yes         |
| Ollama         | 0.1.40+  | Local LLM inference                              | Yes         |
| Node.js        | 22+      | Frontend (Next.js) and lint-staged hooks         | Yes         |
| Git + Git Bash | any      | Bootstrap scripts use `bash`                     | Yes         |
| Python         | 3.12     | Only needed if running the worker outside Docker | No          |
| GPU            | 8GB VRAM | Ollama inference is far faster with CUDA         | Recommended |

**Windows note.** Run all commands from Git Bash or WSL. Native
`cmd.exe` and PowerShell do not work with the bootstrap script.
Docker Desktop must be configured to use WSL2 backend.

**GPU note.** You can run Poindexter on CPU, but content generation
that takes 30 seconds on an RTX 4090 can take 10+ minutes on CPU.
Not practical for daily use.

## 2. Clone and bootstrap

```bash
git clone https://github.com/Glad-Labs/poindexter.git
cd poindexter
bash scripts/bootstrap.sh
```

The bootstrap script is idempotent and does the following:

1. **Generates secrets** — if `.env.local` is missing, writes one
   with random `API_TOKEN`, `LOCAL_POSTGRES_PASSWORD`,
   `GRAFANA_PASSWORD`, and `PGADMIN_PASSWORD`.
2. **Starts the local Postgres container** and waits for its health
   check to pass.
3. **Applies migrations** via `alembic` (through the
   `apply_migrations.py` wrapper). Safe to re-run — migrations are
   idempotent.
4. **Seeds `app_settings`** with the default configuration used in
   production on gladlabs.io. You can override any of these later
   via the settings API.
5. **Pulls required Ollama models** (`qwen3:8b`, `gemma3:27b`,
   `nomic-embed-text`). This is the longest step on first run —
   roughly 25GB of download.
6. **Runs a health check** against the worker's `/api/health`
   endpoint and reports status for each subsystem.

If any step fails, the script exits with a non-zero status and
prints which step hit the error. Re-run after fixing.

## 3. Bring up the full stack

```bash
docker compose -f docker-compose.local.yml up -d
```

This starts 10 containers:

| Container                   | Purpose                             | Port  |
| --------------------------- | ----------------------------------- | ----- |
| `poindexter-worker`         | FastAPI backend, content pipeline   | 8002  |
| `poindexter-brain-daemon`   | Health probes + self-healing loop   | -     |
| `poindexter-postgres-local` | Local Postgres 15 + pgvector        | 5433  |
| `poindexter-grafana`        | Monitoring dashboards               | 3001  |
| `poindexter-prometheus`     | Metric scraper                      | 9091  |
| `poindexter-pgadmin`        | DB GUI                              | 5051  |
| `gladlabs-gitea`            | Self-hosted git (optional)          | 3002  |
| `gladlabs-woodpecker`       | CI runner (optional)                | 8003  |
| `gladlabs-adminer`          | DB GUI alternative                  | 8081  |
| `gladlabs-openclaw`         | Multi-channel ops bridge (optional) | 18789 |

The `optional` containers can be stopped if you don't need them:

```bash
docker compose -f docker-compose.local.yml stop gitea woodpecker adminer openclaw
```

## 4. Verify

```bash
# Worker health (expect "healthy" for every subsystem)
curl http://localhost:8002/api/health

# Create a task end-to-end
curl -X POST http://localhost:8002/api/tasks \
  -H "Authorization: Bearer $(grep ^API_TOKEN .env.local | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"task_name":"Blog post: test","topic":"Why Docker changed everything","category":"technology","target_audience":"developers"}'
```

The task should move through `pending → in_progress → awaiting_approval`
within a few minutes. Follow along via:

```bash
docker logs -f poindexter-worker
```

## 5. Frontend (optional for backend dev)

If you're only iterating on the backend, skip the frontend — the
worker's API and the Grafana dashboards are all you need. If you do
want the Next.js public site running locally:

```bash
cd web/public-site
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Note that
`web/public-site/` is in a separate git-managed dir on Matt's main
setup; in the public repo it's not tracked (the site is deployed on
Vercel and consumes the JSON output of the backend).

## 6. Run the tests

```bash
cd src/cofounder_agent
poetry install
poetry run pytest tests/unit/ -q
```

Expected: ~4,900 passing. A single `test_web_research.py` file is
currently `--ignored` in CI (known issue, tracked in CHANGELOG).

## 7. What to do when something breaks

See [troubleshooting.md](troubleshooting.md).

## Configuration

All runtime configuration lives in the `app_settings` Postgres
table, not env vars. After bootstrap, change settings with:

```bash
# View all settings
curl http://localhost:8002/api/settings \
  -H "Authorization: Bearer $API_TOKEN"

# Change a setting
curl -X PUT http://localhost:8002/api/settings/auto_publish_threshold \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value": "80"}'
```

See [environment-variables.md](environment-variables.md) for the
env-var-layer reference, and
[reference/app-settings.md](../reference/app-settings.md) for the
DB-layer reference.
