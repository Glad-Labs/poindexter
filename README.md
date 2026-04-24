# Poindexter

**Your PC is a content factory.** Poindexter is an open-source AI content pipeline that researches, writes, reviews, and publishes — autonomously. Built by [Glad Labs LLC](https://www.gladlabs.io).

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-5%2C000%2B_passing-brightgreen)]()
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)]()
[![Built by Glad Labs LLC](https://img.shields.io/badge/built_by-Glad_Labs_LLC-blueviolet.svg)](https://www.gladlabs.io)

## What It Does

One engine that runs your entire content operation:

1. **Discovers** trending topics from HackerNews, Dev.to, and your niche
2. **Researches** each topic with deep web search and source verification
3. **Writes** long-form posts with real code examples using local LLMs (Ollama)
4. **Reviews** every draft with multi-model QA scoring on 7 quality dimensions
5. **Validates** against hallucinations — catches fake people, stats, quotes, and impossible claims
6. **Publishes** to any frontend via static JSON export (push-only headless CMS)
7. **Generates** podcast episodes and AI images from each article
8. **Monitors** itself with Grafana monitoring dashboard, auto-heals via brain daemon

Run it on your machine. Own your data. No cloud lock-in.

**Not a spam cannon.** 50% of generated drafts are rejected by QA. Six independent reviewers, anti-hallucination validation, and research-backed content. Speed comes from generating more candidates and filtering aggressively — not from lowering the bar.

## Prerequisites

- **Docker Desktop** — [docker.com](https://docker.com) (required)
- **Ollama** — [ollama.com](https://ollama.com) (required for local AI inference)
- **Node.js 22+** — [nodejs.org](https://nodejs.org) (for frontend)
- **GPU (recommended)** — RTX 3060+ (8GB VRAM min). Works on CPU but slow.

## Quick Start

> **Windows users:** run these commands from Git Bash or WSL. The start script needs `bash` (not native cmd or PowerShell).

```bash
# 1. Clone
git clone https://github.com/Glad-Labs/poindexter.git
cd poindexter

# 2. Setup (generates secrets, tests DB, writes ~/.poindexter/bootstrap.toml)
pip install -e src/cofounder_agent
poindexter setup --auto    # spins up local Postgres automatically

# 3. Pull AI models
ollama pull gemma3:27b && ollama pull qwen3:8b && ollama pull nomic-embed-text

# 4. Start the full stack
bash scripts/start-stack.sh

# 5. Your first post
curl -X POST http://localhost:8002/api/tasks \
  -H "Authorization: Bearer $(grep api_token ~/.poindexter/bootstrap.toml | cut -d'"' -f2)" \
  -H "Content-Type: application/json" \
  -d '{"topic": "Why Docker changed everything", "category": "technology"}'
```

The pipeline runs automatically. Check progress at `http://localhost:3000` (Grafana).

### Minimum Models (auto-pulled by bootstrap)

| Model              | Size  | Role                                                         |
| ------------------ | ----- | ------------------------------------------------------------ |
| `qwen3:8b`         | 5GB   | Fast tasks — SEO, image decisions, summaries, routing        |
| `gemma3:27b`       | 16GB  | QA critic (runs alongside the writer for adversarial review) |
| `nomic-embed-text` | 274MB | Embeddings for semantic search + memory retrieval            |

**With just these three, the pipeline runs end-to-end on any Ollama-capable
GPU.** The writer falls back to `gemma3:27b` when no larger model is
available — output quality drops but nothing breaks.

### Writer model (configurable via `app_settings`)

The writer is the one model that benefits most from upgrading. Set
`pipeline_writer_model` in the DB (or via `poindexter settings set`) to
any Ollama model you have pulled. Glad Labs prod runs a custom RTX 5090
tune (`glm-4.7-5090`, 19GB) that isn't on the public registry, but any
of these publicly available models work well and trade off size vs
quality:

```bash
ollama pull qwen3:30b          # 18GB — best speed/quality balance publicly available
ollama pull qwen3.5:35b        # 23GB — stronger prose, slower
ollama pull llama3.3:70b       # 42GB — highest quality, needs 48GB+ VRAM or CPU offload
ollama pull glm-4.7:9b         # 6GB — lighter fallback, good for <16GB VRAM
```

Every model routing decision (writer / critic / research / summarizer /
embedder) lives in `app_settings` and can be swapped at runtime without a
restart. See `docs/architecture/content-pipeline.md` for the full routing
table.

## Project Status

Poindexter is in **alpha**. Honestly:

**What works today:**

- The full content pipeline end-to-end on the author's daily-driver setup (RTX 5090, 64GB RAM, Windows 11 + WSL2). Single-operator content business, a few published posts per day.
- 5,000+ unit tests passing in CI on every push.
- `poindexter setup` takes a fresh clone to a healthy local stack — generates secrets, tests DB, runs migrations, writes `~/.poindexter/bootstrap.toml`. No `.env` file needed.
- Live in-place upgrades — database renames, container renames, and env var migrations have been applied to a running instance with zero data loss and no downtime for in-flight tasks.
- Customer-facing Docker containers with healthchecks, restart policies, and log aggregation baked in.
- Multi-model QA scoring with deterministic validators, an LLM critic chain, and a programmatic anti-hallucination layer.
- Push-only static export to any S3-compatible storage. The frontend can be Next.js, Hugo, Astro, or a single static HTML file — Poindexter doesn't care.

**What doesn't work yet (or has known rough edges):**

- The legacy `bootstrap.sh` has been replaced by `poindexter setup` (interactive wizard with `--auto` mode). If you're using bootstrap.sh from an older clone, switch to the new flow.
- No managed/hosted Poindexter offering. Self-host only.
- No multi-tenant deployment recipe. One operator, one machine.
- 1 Grafana dashboard ships free (Pipeline Operations). 5 additional dashboards (Cost Analytics, Quality, Infrastructure, Approval Queue, Link Registry) ship with Pro.
- Native Windows cmd / PowerShell is not supported. Use Git Bash or WSL.
- Database schema is not yet considered stable across releases. Read the CHANGELOG before upgrading.

If any of those would block your use case, that's worth knowing before you start. If you want to fix one of them, [PRs welcome](CONTRIBUTING.md).

## Screenshots

<!-- TODO: add screenshots once the public site is fully linked.
     Wishlist:
       - Grafana "Pipeline Operations" dashboard with live throughput
       - The OpenClaw approval Discord embed
       - The Next.js public site rendering a generated post -->

_Screenshots coming. In the meantime, see [gladlabs.io](https://www.gladlabs.io) for live output from the pipeline._

## Architecture

```
Your PC (the engine)
├── Content Pipeline (FastAPI)     — researches, writes, scores, publishes
├── Brain Daemon                   — monitors health, self-heals, auto-restarts
├── MCP Server                     — control from Claude Desktop / Telegram
├── Static Export                  — pushes JSON/RSS/Feed to any S3-compatible storage
├── Grafana + Prometheus           — 1 free dashboard + 5 premium, alerting
└── PostgreSQL + pgvector          — config, content, embeddings, knowledge graph

Any Frontend (reads static JSON from CDN)
├── Next.js, Hugo, Astro, custom...
└── Zero API dependency — just fetch JSON files
```

## Key Features

| Feature                   | Description                                                                      |
| ------------------------- | -------------------------------------------------------------------------------- |
| **Local AI**              | Ollama for inference. Your GPU, your data, zero API costs.                       |
| **Anti-Hallucination**    | 3 layers: prompt engineering, cross-model QA, deterministic validator            |
| **Push-Only Output**      | Static JSON + RSS + JSON Feed 1.1 to any S3-compatible storage                   |
| **DB-as-Config**          | Every setting, prompt, and threshold in PostgreSQL. Change with SQL, no deploys. |
| **Multi-Site**            | One daemon manages N sites. Each site = config row + storage bucket.             |
| **Self-Healing**          | Brain daemon monitors all services, restarts failures, alerts via Telegram       |
| **Production Monitoring** | Grafana monitoring dashboard included out of the box                             |
| **5,000+ Tests**          | Comprehensive unit test coverage across all services                             |

## Stack

- **Backend:** Python 3.12 / FastAPI / asyncpg
- **AI:** Ollama (local inference, Ollama-only pipeline). Community plugins can add any OpenAI-compat backend — vllm, llama.cpp, SGLang, TGI, LocalAI, LiteLLM — by config; paid-API connectors (Anthropic, OpenAI, Groq, OpenRouter) are community-plugin territory.
- **Embeddings:** `nomic-embed-text` via Ollama → pgvector
- **Database:** PostgreSQL 16 + pgvector for embeddings
- **Monitoring:** Grafana + Prometheus + Alertmanager (Telegram/Discord webhooks)
- **Storage:** Any S3-compatible (Cloudflare R2, AWS S3, MinIO)
- **CI/CD:** Gitea Actions (self-hosted) or GitHub Actions
- **Infrastructure:** Docker Compose (~12 containers)

## Configuration

Everything lives in the `app_settings` database table — not environment variables. The only value you ever write to disk is the database URL itself, in `~/.poindexter/bootstrap.toml` (created by `poindexter setup`). After that, all config is managed via API or SQL.

```bash
# View all settings
curl http://localhost:8002/api/settings -H "Authorization: Bearer $TOKEN"

# Change a setting
curl -X PUT http://localhost:8002/api/settings/auto_publish_threshold \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"value": "80"}'
```

## Plugins

Poindexter is built on a small plugin framework — Taps (data in), Probes (state checks), Jobs (scheduled work), Stages (pipeline steps), Packs (prompt/style bundles), and LLMProviders (inference backends). Each Protocol lives in `src/cofounder_agent/plugins/` and registers via setuptools `entry_points`.

### Using community plugins

```bash
# Install any Poindexter plugin with pip:
pip install poindexter-tap-slack       # community Tap for Slack messages
pip install poindexter-llm-groq        # community LLMProvider for Groq

# Enable + configure via app_settings:
curl -X PUT http://localhost:8002/api/settings/plugin.tap.slack \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"value": "{\"enabled\": true, \"interval_seconds\": 3600, \"config\": {\"workspace\": \"myteam\"}}"}'
```

Next worker restart picks up the new plugin. No code edits to Poindexter core required.

### Authoring a plugin

A plugin package needs three things:

1. A class implementing the relevant Protocol (e.g. `Tap`):

   ```python
   # my_package/slack_tap.py
   from poindexter.plugins import Tap, Document

   class SlackTap:
       name = "slack"
       interval_seconds = 3600

       async def extract(self, pool, config):
           async for msg in fetch_slack_messages(config):
               yield Document(
                   source_id=f"slack/{msg.ts}",
                   source_table="slack",
                   text=msg.text,
                   metadata={"channel": msg.channel, "user": msg.user},
                   writer="poindexter-tap-slack",
               )
   ```

2. Entry_points registration in `pyproject.toml`:

   ```toml
   [project.entry-points."poindexter.taps"]
   slack = "my_package.slack_tap:SlackTap"
   ```

3. Per-install config shape documented in your README so operators know what to put under `plugin.tap.<name>.config`.

See `src/cofounder_agent/plugins/samples/` for the three shipping samples (`HelloTap`, `DatabaseProbe`, `NoopJob`) and `docs/architecture/plugin-architecture.md` for the full design.

## Documentation

Full technical documentation lives under [`docs/`](docs/README.md).
Start with the [architecture overview](docs/ARCHITECTURE.md), then
dive into specific areas:

- **[Architecture overview](docs/ARCHITECTURE.md)** — how the whole system fits together
- **[Multi-agent pipeline](docs/architecture/multi-agent-pipeline.md)** — the content pipeline + cross-model QA
- **[Database schema](docs/architecture/database-schema.md)** — every table + migration system
- **[API reference](docs/api/README.md)** — REST endpoints exposed by the worker
- **[CLI reference](docs/operations/cli-reference.md)** — every `poindexter` subcommand with flags + examples
- **[Services reference](docs/reference/services.md)** — catalog of every service in the worker
- **[Extending Poindexter](docs/operations/extending-poindexter.md)** — how to add stages, reviewers, adapters, taps, jobs, and probes
- **[Local development setup](docs/operations/local-development-setup.md)** — end-to-end setup walkthrough
- **[Environment variables](docs/operations/environment-variables.md)** — bootstrap-layer config
- **[Troubleshooting](docs/operations/troubleshooting.md)** — production issues we've hit

The docs are written for operators and contributors who want to
master the system. They take hours to read end-to-end. If you want
a guided shortcut, Pro (below) includes the full Poindexter book
so you skip the reading and get straight to publishing.

## Pricing

The engine is free and open-source. Pro is a subscription for operators who want production-grade output without tuning from scratch.

| Tier     | Price                                                 | What You Get                                                                                                                                                               |
| -------- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Free** | $0                                                    | Full pipeline engine, basic prompts, 1 Grafana dashboard (Pipeline Operations), GitHub issues support                                                                      |
| **Pro**  | $9/mo or $89/year (save ~17%)<br>**7-day free trial** | Premium prompts (anti-hallucination, SEO, QA, research), 5 additional Grafana dashboards, prompt updates as Matt tunes them, private VIP Discord, the full Poindexter book |

The free tier runs the pipeline end-to-end. Pro gives you months of tuning in a single install — the difference between default output and content that actually ranks.

- **[Start your 7-day Pro trial — $9/mo](https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9)**
- [Subscribe annually — $89/year](https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9)
- [Compare tiers on gladlabs.io/product](https://www.gladlabs.io/product)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Security & SBOM

- Report vulnerabilities to **security@gladlabs.io** (see [SECURITY.md](SECURITY.md))
- Every push to `main` runs gitleaks (secret scan), Trivy (CVE scan), and syft+grype (SBOM + CVE scan against the SBOM) — see [`.github/workflows/security.yml`](.github/workflows/security.yml)
- A CycloneDX-JSON **SBOM is published as a workflow artifact** on every release; enterprise buyers can also request one directly at the contact address above

## License

[GNU Affero General Public License v3.0](LICENSE) — Copyright 2025-2026 Matthew M. Gladding

For commercial licensing inquiries: sales@gladlabs.io
