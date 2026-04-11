# Poindexter

**Your PC is a content factory.** Poindexter is an open-source AI content pipeline that researches, writes, reviews, and publishes — autonomously. Built by [Glad Labs LLC](https://www.gladlabs.io).

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-4%2C252_passing-brightgreen)]()

## What It Does

One engine that runs your entire content operation:

1. **Discovers** trending topics from HackerNews, Dev.to, and your niche
2. **Researches** each topic with deep web search and source verification
3. **Writes** long-form posts with real code examples using local LLMs (Ollama)
4. **Reviews** every draft with multi-model QA scoring on 7 quality dimensions
5. **Validates** against hallucinations — catches fake people, stats, quotes, and impossible claims
6. **Publishes** to any frontend via static JSON export (push-only headless CMS)
7. **Generates** podcast episodes and AI images from each article
8. **Monitors** itself with 7 Grafana dashboards, auto-heals via brain daemon

Run it on your machine. Own your data. No cloud lock-in.

## Prerequisites

- **Docker Desktop** — [docker.com](https://docker.com) (required)
- **Ollama** — [ollama.com](https://ollama.com) (required for local AI inference)
- **Node.js 22+** — [nodejs.org](https://nodejs.org) (for frontend)
- **GPU (recommended)** — RTX 3060+ (8GB VRAM min). Works on CPU but slow.

## Quick Start

> **Windows users:** run these commands from Git Bash or WSL. The bootstrap script needs `bash` (not native cmd or PowerShell).

```bash
# 1. Clone
git clone https://github.com/Glad-Labs/poindexter.git
cd poindexter

# 2. Bootstrap (creates config, starts DB, pulls models, seeds settings)
bash scripts/bootstrap.sh

# 3. Start the full stack
docker compose -f docker-compose.local.yml up -d

# 4. Your first post
curl -X POST http://localhost:8002/api/tasks \
  -H "Authorization: Bearer $(grep ^API_TOKEN .env.local | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"topic": "Why Docker changed everything", "category": "technology"}'
```

The pipeline runs automatically. Check progress at `http://localhost:3000` (Grafana).

### Minimum Models (auto-pulled by bootstrap)

| Model              | Size  | Role                                        |
| ------------------ | ----- | ------------------------------------------- |
| `qwen3:8b`         | 5GB   | Fast tasks: SEO, image decisions, summaries |
| `gemma3:27b`       | 16GB  | QA reviews, fallback critic                 |
| `nomic-embed-text` | 274MB | Embeddings for semantic search              |

For better writing quality, also pull a larger writer model:

```bash
ollama pull qwen3:30b      # 18GB — good balance of speed and quality
ollama pull glm-4.7:9b     # 6GB — lighter alternative
```

## Architecture

```
Your PC (the engine)
├── Content Pipeline (FastAPI)     — researches, writes, scores, publishes
├── Brain Daemon                   — monitors health, self-heals, auto-restarts
├── MCP Server                     — control from Claude Desktop / Telegram
├── Static Export                  — pushes JSON/RSS/Feed to any S3-compatible storage
├── Grafana + Prometheus           — 7 dashboards, 90+ panels, alerting
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
| **Production Monitoring** | 7 Grafana dashboards included out of the box                                     |
| **4,000+ Tests**          | Comprehensive unit test coverage across all services                             |

## Stack

- **Backend:** Python 3.12 / FastAPI / asyncpg
- **AI:** Ollama (local) + optional cloud fallback (Anthropic, OpenAI, Google)
- **Database:** PostgreSQL 16 + pgvector for embeddings
- **Monitoring:** Grafana + Prometheus + Telegram alerts
- **Storage:** Any S3-compatible (Cloudflare R2, AWS S3, MinIO)
- **CI/CD:** Woodpecker CI (self-hosted) or GitHub Actions
- **Infrastructure:** Docker Compose (10 containers)

## Configuration

Everything lives in the `app_settings` database table — not environment variables. The `.env` file bootstraps the system; after that, all config is managed via API or SQL.

```bash
# View all settings
curl http://localhost:8002/api/settings -H "Authorization: Bearer $TOKEN"

# Change a setting
curl -X PUT http://localhost:8002/api/settings/auto_publish_threshold \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"value": "80"}'
```

## Premium Add-Ons

The engine is free and open-source. For production-quality output:

- **Quick Start Guide ($29)** — Setup to first post in 30 minutes
- **Premium Prompts ($9/mo)** — Production-grade prompt templates, updated monthly

Available at [gladlabs.io](https://www.gladlabs.io)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[GNU Affero General Public License v3.0](LICENSE) — Copyright 2025-2026 Matthew M. Gladding

For commercial licensing inquiries: sales@gladlabs.io
