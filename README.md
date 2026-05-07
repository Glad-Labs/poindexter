# Poindexter

**A plug-and-play AI/ML content creation OSS stack.** Your PC is the factory: Poindexter researches, writes, reviews, and publishes — autonomously. Local-first, Ollama-powered, zero API costs. Built by [Glad Labs LLC](https://www.gladlabs.io).

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-7%2C900%2B_passing-brightgreen)]()
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)]()
[![Built by Glad Labs LLC](https://img.shields.io/badge/built_by-Glad_Labs_LLC-blueviolet.svg)](https://www.gladlabs.io)

## Who this is for

If you've ever thought _"I could publish good content at scale if I had a system that didn't just spam mediocre AI text,"_ — Poindexter is that system.

It's built for:

- **Solo operators** who want to run a content business from one machine, with their own GPU, without paying per-token API fees
- **Indie publishers** who need automation but refuse to ship hallucinated text
- **AI/ML engineers** who want a working content stack to fork, extend, and learn from — every layer is OSS, every layer is swappable

It is _not_ for: marketing teams who want a one-click web app (use Jasper / Copy.ai), or anyone unwilling to run Docker on their machine.

The pitch is "plug and play": `poindexter setup --auto` takes you from a fresh clone to a healthy local stack in one command — Postgres provisioned, OAuth client minted, migrations run, models pulled, services up. After that, every component is swappable through `app_settings` or plugins.

## What it does

One engine, eight jobs:

1. **Discovers** trending topics from HackerNews, Dev.to, and your niche feeds
2. **Researches** each topic with deep web search and source verification
3. **Writes** long-form posts using local LLMs (Ollama) — or cloud models via the optional LiteLLM provider plugin
4. **Reviews** every draft with multi-model adversarial QA on 7 quality dimensions
5. **Validates** against hallucinations — catches fake people, stats, quotes, impossible claims
6. **Publishes** to any frontend via static JSON export (push-only headless CMS)
7. **Generates** podcast episodes, AI images, and short text-to-video clips (Wan 2.1 T2V — alpha, opt-in)
8. **Monitors** itself with Grafana dashboards, auto-heals via brain daemon, alerts on Telegram/Discord

Run it on your machine. Own your data. No cloud lock-in.

**Not a spam cannon.** ~50% of generated drafts are rejected by QA. Multi-model adversarial review, deterministic anti-hallucination validation, and research-backed content. Speed comes from generating more candidates and filtering aggressively — not from lowering the bar.

## Quick start

> **Windows users:** run from Git Bash or WSL. The setup script needs `bash`.

```bash
# 1. Clone
git clone https://github.com/Glad-Labs/poindexter.git
cd poindexter

# 2. Setup — generates secrets, tests DB, writes ~/.poindexter/bootstrap.toml
pip install -e src/cofounder_agent
poindexter setup --auto    # spins up local Postgres automatically

# 3. Pull AI models
ollama pull gemma3:27b && ollama pull qwen3:8b && ollama pull nomic-embed-text

# 4. Start the full stack
bash scripts/start-stack.sh

# 5. Generate your first post
poindexter content create "Why Docker changed everything" --category technology
```

The pipeline runs automatically. Watch progress at `http://localhost:3000` (Grafana).

### Prerequisites

- **Docker Desktop** — [docker.com](https://docker.com)
- **Ollama** — [ollama.com](https://ollama.com)
- **Node.js 22+** — [nodejs.org](https://nodejs.org) (for the optional Next.js public site)
- **GPU** — RTX 3060+ (8 GB VRAM minimum). Works on CPU, just slowly.

### Required models

`poindexter setup --auto` doesn't pull these — Ollama does, but you trigger it. With these three, the full pipeline runs end-to-end on any 8 GB+ GPU:

| Model              | Size   | Role                                                  |
| ------------------ | ------ | ----------------------------------------------------- |
| `qwen3:8b`         | 5 GB   | Fast tasks — SEO, image decisions, summaries, routing |
| `gemma3:27b`       | 16 GB  | QA critic + writer fallback                           |
| `nomic-embed-text` | 274 MB | Embeddings for semantic search + memory retrieval     |

### Writer model — configurable

The writer is the one model worth upgrading. Set `pipeline_writer_model` in `app_settings` (or via `poindexter settings set`) to any Ollama model you have. Trade-offs:

```bash
ollama pull qwen3:30b          # 18 GB — best speed/quality balance publicly available
ollama pull qwen3.5:35b        # 23 GB — stronger prose, slower
ollama pull llama3.3:70b       # 42 GB — highest quality, needs 48 GB+ VRAM or CPU offload
ollama pull glm-4.7:9b         # 6 GB — lighter fallback for <16 GB VRAM
```

Glad Labs production runs a custom RTX 5090 fine-tune (`glm-4.7-5090`, 19 GB) not on the public registry; any of the above work fine.

Every model routing decision (writer / critic / research / summarizer / embedder) lives in `app_settings` and can be swapped at runtime — no restart, no redeploy. See [`docs/architecture/content-pipeline.md`](docs/architecture/content-pipeline.md) for the full routing table.

## Architecture

Poindexter is decomposed by analogy to brain anatomy. Each region is independent and communicates only through PostgreSQL — no inter-service imports.

```
Brainstem    (brain/)              — standalone daemon, monitors, self-heals
Cerebrum     (src/cofounder_agent/) — FastAPI backend, content pipeline, REST + MCP
Cerebellum                          — anticipation engine + QA registry (learned patterns)
Limbic       (brain_knowledge)     — knowledge graph, memory retrieval, revenue feedback
Thalamus                           — process composer, routes inputs to the right pipeline
Hypothalamus (settings_service)    — homeostasis: budget, cost guard, runtime config
Spinal Cord  (PostgreSQL+pgvector) — shared substrate, all components talk through it

Any frontend reads static JSON from CDN — Next.js, Hugo, Astro, or a single HTML file.
```

The brainstem can crash and restart without taking down the cerebrum. The cerebrum can be replaced with a different pipeline implementation as long as it writes the same tables. The architecture is designed to be poked at one region at a time.

Full diagram and design rationale in [`docs/architecture/`](docs/architecture/).

## Key features

| Feature                      | Description                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------- |
| **Local AI by default**      | Ollama for inference. Your GPU, your data, zero API costs.                                  |
| **Cloud opt-in**             | LiteLLM provider plugin routes to Anthropic, OpenAI, Groq, OpenRouter — gated by cost guard |
| **Anti-hallucination**       | 3 independent layers: prompts, multi-model QA, deterministic validator                      |
| **DB-as-config**             | 670+ settings in PostgreSQL. Change with SQL or REST. No deploys.                           |
| **Langfuse-managed prompts** | Edit prompts in a UI; runtime falls back to YAML defaults if Langfuse is offline            |
| **LangGraph pipelines**      | `template_runner.py` runs declarative DAGs with checkpointing                               |
| **Multi-modal output**       | Markdown posts, AI images (SDXL / Flux), podcast audio, text-to-video (Wan 2.1 — alpha)     |
| **Push-only output**         | Static JSON + RSS + JSON Feed 1.1 to any S3-compatible storage                              |
| **Multi-site**               | One daemon manages N sites. Each site = config row + storage bucket.                        |
| **Self-healing**             | Brain daemon monitors all services, restarts failures, alerts via Telegram/Discord          |
| **Production observability** | Grafana, Prometheus, Loki, Pyroscope (CPU profiling), Sentry/GlitchTip                      |
| **OAuth 2.1 throughout**     | Every consumer (CLI, MCP, brain, scripts) mints scoped JWTs. No static API keys.            |
| **7,900+ tests**             | Unit coverage across all services, smoke tests on migrations, link-rot CI                   |

## Stack

- **Backend:** Python 3.12 / FastAPI / asyncpg
- **LLM (default):** [Ollama](https://ollama.com) — local inference, your GPU
- **LLM (optional):** [LiteLLM](https://github.com/BerriAI/litellm) provider plugin — Anthropic, OpenAI, Groq, OpenRouter, Bedrock, Vertex (gated by `cost_guard`)
- **Orchestration:** [LangGraph](https://github.com/langchain-ai/langgraph) (declarative pipelines via `template_runner`)
- **Prompt management:** [Langfuse](https://langfuse.com) (UI-editable, runtime fallback to YAML)
- **Embeddings:** `nomic-embed-text` via Ollama → pgvector
- **Database:** PostgreSQL 16 + pgvector
- **Auth:** OAuth 2.1 Client Credentials Grant (per-consumer JWTs)
- **Observability:** Grafana + Prometheus + Loki + [Pyroscope](https://pyroscope.io) + Sentry-compatible (GlitchTip)
- **Voice (optional):** LiveKit + Whisper (STT) + Kokoro (TTS)
- **Storage:** any S3-compatible (Cloudflare R2, AWS S3, Backblaze B2, MinIO)
- **CI/CD:** GitHub Actions
- **Infrastructure:** Docker Compose (~12 containers)

## Configuration

Everything tunable lives in the `app_settings` database table — not environment variables. The only file you write to disk is `~/.poindexter/bootstrap.toml`, created by `poindexter setup`. It contains the database URL plus a small number of pre-DB-reachable secrets (Postgres password, OAuth signing key, optional Telegram/Discord operator alerts).

After that, every config knob is managed via API, SQL, or the CLI:

```bash
# View all settings
poindexter settings list

# Change a setting at runtime
poindexter settings set auto_publish_threshold 80

# Rotate via REST (with OAuth-issued JWT)
curl -X PUT http://localhost:8002/api/settings/auto_publish_threshold \
  -H "Authorization: Bearer $(poindexter auth token)" \
  -d '{"value": "80"}'
```

No restart required for most settings. See [`docs/operations/configuration.md`](docs/operations/configuration.md).

## Plugins

Poindexter is built on a small extension framework. Six plugin types let you customize the system without touching core code:

| Type            | Role                                                         |
| --------------- | ------------------------------------------------------------ |
| **Tap**         | Pulls data into the system (RSS, Slack, social feeds, etc.)  |
| **Probe**       | Reports state to the brain (health checks, business metrics) |
| **Job**         | Scheduled work (cron-like, lives in the worker)              |
| **Stage**       | A step in the content pipeline (research, draft, QA, etc.)   |
| **Pack**        | Bundle of prompts + style rules (your "brand voice")         |
| **LLMProvider** | Inference backend (Ollama is default; LiteLLM, vLLM, etc.)   |

Each plugin lives in its own pip package and registers via setuptools `entry_points`.

### Using a plugin

```bash
pip install poindexter-tap-slack
poindexter settings set plugin.tap.slack '{"enabled": true, "config": {"workspace": "myteam"}}'
```

Next worker restart picks it up. No core code changes.

### Authoring a plugin

A package needs three things — a class implementing the relevant Protocol, an `entry_points` registration, and per-install config docs. Example Tap:

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

```toml
# pyproject.toml
[project.entry-points."poindexter.taps"]
slack = "my_package.slack_tap:SlackTap"
```

The shipping samples (`HelloTap`, `DatabaseProbe`, `NoopJob`) live in `src/cofounder_agent/plugins/samples/`. The first real plugin in production is the `LiteLLMProvider` — Glad Labs eats its own dog food. Full design in [`docs/architecture/plugin-architecture.md`](docs/architecture/plugin-architecture.md).

## Project status

Poindexter is in **alpha**. Honest snapshot:

**What works today**

- Full content pipeline end-to-end on the author's daily-driver setup (RTX 5090, 64 GB RAM, Windows 11). Single-operator content business publishing daily.
- 52 live posts on [gladlabs.io](https://www.gladlabs.io) (218 total drafts, 1,500+ pipeline runs).
- 7,900+ unit tests passing in CI on every push, plus migrations smoke test and link-rot CI.
- `poindexter setup` takes a fresh clone to a healthy local stack — generates secrets, tests DB, runs migrations, writes bootstrap.toml. No `.env` file required.
- Live in-place upgrades — schema changes, container renames, env var migrations applied to a running instance with zero data loss and no in-flight task downtime.
- Multi-model QA scoring with deterministic validators, an LLM critic chain, and a programmatic anti-hallucination layer.
- Push-only static export to any S3-compatible storage. Frontend is decoupled — Next.js, Hugo, Astro, or a static HTML file.
- OAuth 2.1 throughout (per-consumer scoped JWTs, no static API keys).

**Known rough edges**

- No managed/hosted Poindexter offering yet. Self-host only.
- No multi-tenant deployment recipe. One operator, one machine.
- Native Windows cmd / PowerShell not supported. Use Git Bash or WSL.
- Database schema is not yet stable across releases. Read the CHANGELOG before upgrading.
- Plugin framework is real (LiteLLMProvider runs in production), but the community ecosystem is nascent — you may be writing the second-ever third-party plugin.
- **Text-to-video is alpha.** The Wan 2.1 T2V provider plugin and `wan-server` Docker sidecar exist and pass smoke tests, but it's an opt-in path (not in the default content pipeline) and the inference server needs ~28 GB VRAM headroom on a 32 GB+ card. Track Glad-Labs/poindexter#124 for production-readiness.

If any of those would block your use case, that's worth knowing before you start. PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING).

## Pricing

The engine is free and open-source under Apache 2.0. **Pro** is a subscription for operators who want production-grade output without tuning from scratch.

| Tier     | Price                                                 | What you get                                                                                                                                                                 |
| -------- | ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Free** | $0                                                    | Full pipeline engine, baseline prompts, 1 Grafana dashboard (Pipeline Operations), GitHub issues support                                                                     |
| **Pro**  | $9/mo or $89/year (save ~17%)<br>**7-day free trial** | Production-tuned prompts (anti-hallucination, SEO, QA, research), additional Grafana dashboards, prompt updates as Matt tunes them, private VIP Discord, the Poindexter book |

Pro exists for the obvious case: you've installed the OSS, you've seen output that's _almost_ there, and you want the version that's actually shipping content on gladlabs.io daily. Pro gives you the months of prompt tuning in a single install.

- **[Start your 7-day Pro trial — $9/mo](https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9)**
- [Subscribe annually — $89/year](https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9)
- [Compare tiers on gladlabs.io/product](https://www.gladlabs.io/product)

## Documentation

Full technical docs live under [`docs/`](docs/welcome). Recommended path:

- **[Architecture overview](docs/ARCHITECTURE)** — how the regions fit together
- **[Multi-agent pipeline](docs/architecture/multi-agent-pipeline)** — the content pipeline + cross-model QA
- **[Database schema](docs/architecture/database-schema)** — every table + migration system
- **[CLI reference](docs/operations/cli-reference)** — every `poindexter` subcommand
- **[Plugin authoring](docs/operations/extending-poindexter)** — write Stages, Reviewers, Adapters, Taps, Jobs, Probes
- **[Local development setup](docs/operations/local-development-setup)** — end-to-end walkthrough
- **[Troubleshooting](docs/operations/troubleshooting)** — production issues we've hit

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING). Issues and PRs welcome.

## Security & SBOM

- Report vulnerabilities to **security@gladlabs.io** ([SECURITY.md](SECURITY))
- Every push to `main` runs gitleaks (secrets), Trivy (CVEs), and syft+grype (SBOM + CVE scan)
- A CycloneDX-JSON **SBOM** is published as a workflow artifact on every release; enterprise buyers can request one directly

## License

[Apache License 2.0](LICENSE) — Copyright 2025-2026 Matthew M. Gladding

Relicensed from AGPL-3.0 to Apache 2.0 on 2026-04-29 — see [CHANGELOG](CHANGELOG.md).
