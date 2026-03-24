# Glad Labs - AI Co-Founder System

**Version:** 3.0.82
**Last Updated:** March 2026

AI orchestration system with autonomous agents, multi-provider LLM routing, and full-stack web applications.

## Project Structure

```
.
├── src/cofounder_agent/        # Backend orchestrator (FastAPI, port 8000)
├── web/public-site/            # Content distribution (Next.js 15, port 3000)
├── web/oversight-hub/          # Admin dashboard (React 18 + Vite, port 3001)
├── docs/                       # Documentation
├── .github/                    # CI/CD workflows
└── scripts/                    # Utility scripts
```

## Quick Start

```bash
# Install all dependencies
npm install
cd src/cofounder_agent && poetry install && cd ../..

# Start all three services
npm run dev
```

Requires a `.env.local` at the project root with at minimum:

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs_dev
# Plus at least one LLM provider key:
# ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, or OLLAMA_BASE_URL
```

See `.env.example` for the full variable reference (~57 variables).

## Services

### Backend — FastAPI (`src/cofounder_agent/`)

Python orchestrator with 80 service modules, 29 route files exposing ~158 REST/WebSocket endpoints. Manages AI agents, task workflows, content pipelines, and cost tracking.

- Multi-provider LLM routing with cost tiers (free/budget/standard/premium/flagship)
- Four agent types: Content, Financial, Market Insight, Compliance
- 6-stage content pipeline: Research → Draft → QA → Refinement → Image → Publishing
- PostgreSQL via asyncpg, real-time WebSocket progress

### Public Site — Next.js 15 (`web/public-site/`)

Headless content consumer using the App Router. All content is fetched from the FastAPI backend — no local markdown files.

- Static generation with ISR
- Tailwind CSS, Sentry error tracking
- SEO: sitemap, structured data, Open Graph

### Oversight Hub — React 18 + Vite (`web/oversight-hub/`)

Admin dashboard for task management, workflow orchestration, cost analytics, and agent monitoring.

- Material-UI components, Zustand state management
- Real-time updates via WebSocket
- GitHub OAuth authentication

## Key Features

- **Multi-provider LLM routing** — Ollama, Anthropic, OpenAI, Google with automatic fallback
- **Capability-based tasks** — Composable, reusable task workflows
- **Real-time monitoring** — WebSocket-powered dashboard with live updates
- **Custom workflows** — Build and execute automation pipelines
- **OAuth integration** — GitHub authentication
- **Analytics** — Cost metrics, task performance, model usage

## Development

```bash
npm run dev                   # All services
npm run test                  # JS tests (Jest + Vitest)
npm run test:python:unit      # Python unit tests
npm run lint                  # ESLint all workspaces
npm run format                # Prettier formatting
npm run build                 # Build all frontends
```

See [CLAUDE.md](CLAUDE.md) for the full command reference.

## Deployment

- **main** branch auto-deploys: Vercel (frontends) + Railway (backend)
- **dev** branch deploys to Railway staging

## Documentation

Start at `docs/00-INDEX.md` for the full documentation index.

- `docs/01-Getting-Started/` — Setup and environment configuration
- `docs/02-Architecture/` — System design, API design, data model
- `docs/03-Features/` — Feature documentation
- `docs/04-Development/` — Workflow, testing, CI/CD
- `docs/05-Operations/` — Deployment, monitoring, maintenance
- `docs/06-Troubleshooting/` — Common issues and fixes
- `docs/07-Appendices/` — Reference material, version history

## License

See [LICENSE](LICENSE) for details.
