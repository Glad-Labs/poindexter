# Local development setup

**Last Updated:** 2026-04-17
**Status:** Alpha

This is the end-to-end walkthrough for getting Poindexter running on
your own machine. If you only want "one command to a working
pipeline," run `poindexter setup --auto` — read
[Quick Start in the README](../../README#quick-start) first.

This document covers the longer form: what the setup wizard does
under the hood, how to verify each layer, and how to troubleshoot
when something doesn't come up.

## 1. Prerequisites

| Tool           | Version  | Purpose                                  | Required?   |
| -------------- | -------- | ---------------------------------------- | ----------- |
| Docker Desktop | 4.26+    | Runs the entire backend stack            | Yes         |
| Ollama         | 0.1.40+  | Local LLM inference                      | Yes         |
| Node.js        | 22+      | Frontend (Next.js) and lint-staged hooks | Yes         |
| Git + Git Bash | any      | start-stack.sh uses `bash`               | Yes         |
| Python         | 3.12+    | CLI + running worker outside Docker      | Yes         |
| GPU            | 8GB VRAM | Ollama inference is far faster with CUDA | Recommended |

**Windows note.** Run all commands from Git Bash or WSL. Native
`cmd.exe` and PowerShell do not work with the start scripts.
Docker Desktop must be configured to use WSL2 backend.

**GPU note.** You can run Poindexter on CPU, but content generation
that takes 30 seconds on an RTX 4090 can take 10+ minutes on CPU.
Not practical for daily use.

## 2. Clone and setup

```bash
git clone https://github.com/Glad-Labs/poindexter.git
cd poindexter
pip install -e src/cofounder_agent
poindexter setup          # interactive wizard
# or: poindexter setup --auto   (spins up a local Docker Postgres)
# or: poindexter setup --db-url "postgresql://..."  (non-interactive)
```

The setup wizard does the following:

1. **Prompts for a database URL** (or auto-provisions a local Docker
   Postgres with `--auto`).
2. **Tests the database connection** and reports success/failure.
3. **Runs migrations** against the target database. Safe to re-run —
   migrations are idempotent.
4. **Generates secrets** — creates random `local_postgres_password`,
   `grafana_password`, and `pgadmin_password`.
5. **Writes `~/.poindexter/bootstrap.toml`** with the database URL +
   generated secrets. This is the only config file on disk.
6. **Provisions the initial CLI OAuth client** — registers a row in
   `oauth_clients`, encrypts the credentials into
   `app_settings.cli_oauth_client_id` / `cli_oauth_client_secret`, and
   prints the plaintext secret once for capture. (Worker auth uses
   OAuth 2.1 only as of Glad-Labs/poindexter#249.)

No `.env` file is created. All secrets live in `bootstrap.toml`
(safe permissions, never committed to git).

## 3. Pull AI models

```bash
ollama pull gemma3:27b       # 16GB — QA reviews, fallback critic
ollama pull qwen3:8b         # 5GB — fast tasks (SEO, image decisions)
ollama pull nomic-embed-text # 274MB — embeddings for semantic search
```

Total first-run download: ~21GB. For better writing quality, also pull
a larger writer model:

```bash
ollama pull qwen3:30b        # 18GB — good balance of speed and quality
```

## 4. Bring up the full stack

```bash
bash scripts/start-stack.sh
```

This reads `~/.poindexter/bootstrap.toml`, exports the values as env
vars, and runs `docker compose -f docker-compose.local.yml up -d`.
No `.env` file needed.

This starts the core containers:

| Container                   | Purpose                            | Port  |
| --------------------------- | ---------------------------------- | ----- |
| `poindexter-worker`         | FastAPI backend, content pipeline  | 8002  |
| `poindexter-brain-daemon`   | Health probes + self-healing loop  | —     |
| `poindexter-postgres-local` | PostgreSQL 16 + pgvector           | 15432 |
| `poindexter-grafana`        | Monitoring dashboards (7 included) | 3000  |
| `poindexter-prometheus`     | Metric scraper                     | 9091  |
| `poindexter-sdxl-server`    | SDXL image generation (GPU)        | 9836  |
| `poindexter-pgadmin`        | Database GUI                       | 5480  |

Stop optional containers if you don't need them:

```bash
docker compose -f docker-compose.local.yml stop poindexter-pgadmin
```

## 5. Verify

```bash
# Worker health (expect "healthy" for every subsystem)
curl http://localhost:8002/api/health

# Mint a JWT for the CLI client (printed plaintext during `poindexter
# setup`; if you missed it, re-run `poindexter auth migrate-cli` to
# rotate to a new client).
JWT=$(poindexter auth mint-token \
  --client-id <pdx_xxx> --client-secret <secret>)

# Create a task end-to-end
curl -X POST http://localhost:8002/api/tasks \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"topic": "Why Docker changed everything", "category": "technology"}'
```

The task should move through `pending → in_progress → awaiting_approval`
within a few minutes. Follow along via:

```bash
docker logs -f poindexter-worker
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
poetry install
poetry run pytest tests/unit/ -q
```

Expected: 7,900+ passing across 329 test files. Some tests that depend
on the `brain` module or `sentry-sdk` are skipped when running inside
Docker (these pass on the host where all modules are available).

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
