# Glad Labs — Personal Media Operating System

**Version:** 0.1.0 | **License:** [AGPL-3.0](LICENSE) | **Copyright:** 2025-2026 Matthew M. Gladding

An autonomous AI system that runs content media businesses. Generates quality-scored articles, distributes across platforms, and operates via conversational interface (Discord/Telegram). Built for solo operators who want professional media presence without the operational overhead.

## Architecture

```
You (Discord / Telegram)
  │
  ├─ OpenClaw Gateway ─── 14 custom skills ──→ FastAPI Backend (Railway)
  │                                               ├─ Content pipeline (6-stage)
  │                                               ├─ Model router (Ollama/Anthropic/OpenAI/Google)
  │                                               ├─ Quality scoring (7 criteria + Flesch-Kincaid)
  │                                               ├─ Auto-publish (configurable threshold)
  │                                               └─ PostgreSQL (source of truth)
  │
  ├─ Next.js Public Sites (Vercel) ─── ISR, SEO, AdSense
  │
  └─ SQLAdmin (/admin) ─── Browse tasks, posts, costs, settings
```

## Project Structure

```
.
├── src/cofounder_agent/     # FastAPI backend (Python)
│   ├── routes/              # 7 active API route modules
│   ├── services/            # Content pipeline, model router, quality scoring
│   ├── agents/              # AI agent implementations
│   ├── middleware/           # Bearer token auth
│   ├── migrations/          # PostgreSQL schema migrations
│   └── admin.py             # SQLAdmin panel
├── web/public-site/         # Next.js 15 content site
├── skills/openclaw/         # OpenClaw skill definitions (14 skills)
├── docs/                    # Technical documentation
└── scripts/                 # Utility scripts
```

## Quick Start

```bash
# Install dependencies
npm install
cd src/cofounder_agent && poetry install && cd ../..

# Configure environment
cp .env.example .env.local
# Edit .env.local: set DATABASE_URL, API_TOKEN, and at least one LLM provider key

# Start services
npm run dev
```

The API is at `http://localhost:8000` with:

- `/api/docs` — Swagger UI (interactive API explorer)
- `/admin` — SQLAdmin panel (browse data)

## How It Works

1. **You** tell OpenClaw in Discord: _"write 10 posts about budget gaming PCs"_
2. **OpenClaw** calls the FastAPI batch creation endpoint
3. **Task Executor** picks up pending tasks and runs the 6-stage pipeline:
   - Research → Creative Draft → QA Critique → Refinement → Image Selection → Publishing Prep
4. **Quality Service** scores each post (7 criteria + readability)
5. Posts above the auto-publish threshold go live automatically
6. **Webhook** notifies you in Discord: _"Published 'Budget Gaming PC Guide' (score: 87)"_

## Key Capabilities

- **Multi-provider LLM routing** with cost tiers — Ollama (free) → Anthropic → OpenAI → Google
- **6-stage self-critiquing pipeline** — QA agents critique, creative agents refine
- **Quality scoring** — 7 criteria + Flesch-Kincaid readability, configurable threshold
- **Auto-publish** — posts above quality threshold publish without approval
- **Batch creation** — create 20+ tasks in one command
- **Multi-site** — one pipeline serving multiple content domains
- **Writing style profiles** — RAG-powered voice matching from writing samples
- **Cost tracking** — per-task, per-model spend with budget alerts
- **Webhook notifications** — pipeline events pushed to Discord/Telegram

## Operations (via Discord)

```
"write a post about RTX 5090 builds"     → creates task, pipeline runs
"show my tasks"                           → lists queue with status/scores
"approve task abc123"                     → approves for publishing
"what's my spend this week?"              → cost breakdown by model
"check railway status"                    → backend health + logs
```

See [docs/openclaw/README.md](docs/openclaw/README.md) for the full skill reference.

## Development

```bash
npm run dev                   # Start all services
npm run test:python:unit      # Backend tests (5,488 passing)
npm run test                  # Public site tests (482 passing)
npm run lint                  # ESLint
npm run format                # Prettier
```

## Deployment

| Branch    | Target     | Action                                         |
| --------- | ---------- | ---------------------------------------------- |
| `main`    | Production | Auto-deploy: Vercel (site) + Railway (backend) |
| `staging` | Staging    | Auto-deploy: Railway staging                   |
| `dev`     | CI only    | Tests run, no deployment                       |

## Documentation

- [Architecture Vision](docs/architecture/VISION.md) — Product roadmap and full system design
- [System Design](docs/architecture/system-design.md) — Technical architecture
- [API Contracts](docs/architecture/api-contracts.md) — REST endpoint reference
- [OpenClaw Setup](docs/openclaw/README.md) — Discord/Telegram integration
- [Environment Variables](docs/operations/env-vars.md) — Configuration reference
- [Deployment](docs/operations/deployment.md) — Railway + Vercel setup

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).

For commercial licensing inquiries: support@gladlabs.io
# woodpecker live 1775180709
# ci test 1775180798
