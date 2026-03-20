# AI Co-Founder Agent (Backend)

FastAPI orchestrator for the Glad Labs AI system. Coordinates specialized agents through a multi-provider model router with task management and real-time monitoring.

**Version:** 3.0.81
**Runtime:** Python 3.10+ with FastAPI
**Port:** 8000
**Database:** PostgreSQL via asyncpg (raw SQL, no ORM)
**Dependencies:** Poetry (`pyproject.toml`)

## Quick Start

```bash
cd src/cofounder_agent
poetry install
poetry run uvicorn main:app --reload --port 8000
```

Requires `.env.local` at project root with `DATABASE_URL` and at least one LLM API key. See root `.env.example` for all options.

## Directory Structure

```
src/cofounder_agent/
├── main.py                    # FastAPI app entry point
├── config/                    # Configuration loading
├── routes/                    # 28 REST endpoint modules
├── services/                  # 80+ service modules
│   ├── database_service.py    # Coordinates 5 DB domain modules
│   ├── model_router.py        # LLM provider selection + fallback
│   ├── unified_orchestrator.py # Master agent choreography
│   ├── workflow_executor.py   # Phase-based workflow execution
│   ├── capability_registry.py # Intent-based task routing
│   ├── migrations/            # Python migration modules (raw SQL inside)
│   └── phases/                # Workflow phase implementations
├── agents/                    # AI agent implementations
│   ├── content_agent/         # 6-stage content pipeline
│   ├── financial_agent/       # Cost tracking + analysis
│   ├── market_insight_agent/  # Market research
│   └── compliance_agent/      # Compliance checks
├── schemas/                   # 24 Pydantic model files
├── middleware/                 # 5 middleware modules (auth, validation, etc.)
├── utils/                     # 20 utility modules
└── tests/                     # pytest suite
    └── unit/                  # Unit tests (~5,500 passing)
```

## Key Architecture

**Model Router** (`services/model_router.py`): Routes LLM calls by cost tier (`free`/`budget`/`standard`/`premium`/`flagship`). Automatic fallback: Ollama → Anthropic → OpenAI → Google → echo/mock. Never hardcode model names.

**Database** (`services/database_service.py`): asyncpg connection pool with 5 domain modules (Users, Tasks, Content, Admin, WritingStyle). All queries are raw SQL. Migrations in `services/migrations/`.

**Content Pipeline**: 6-stage self-critiquing pipeline: Research → Creative Draft → QA Critique → Creative Refinement → Image Selection → Publishing Prep.

**Workflow Engine** (`services/workflow_executor.py`): Phase-based execution with real-time WebSocket progress events. Phases defined in `services/phases/`.

## API

28 route modules at `/api/*`. OpenAPI docs at `/api/openapi.json` when running.

Key endpoint groups: `/api/tasks`, `/api/posts`, `/api/workflows`, `/api/agents`, `/api/metrics`, `/api/analytics`, `/api/social`, `/api/settings`.

Most endpoints require JWT auth. Public endpoints: `/api/health`, `/api/auth/*`, `/api/docs`, `/api/openapi.json`.

## Testing

```bash
poetry run pytest tests/unit/ -v           # All unit tests
poetry run pytest tests/unit/ -m unit -x   # Unit only, stop on first failure
poetry run pytest tests/unit/routes/test_task_routes.py -v  # Single file
```

Markers: `unit`, `integration`, `api`, `e2e`, `performance`, `slow`, `voice`, `websocket`.

## Configuration

Environment variables loaded from root `.env.local` via `config/__init__.py`. Key vars:

- `DATABASE_URL` — PostgreSQL connection string (required)
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` / `OLLAMA_BASE_URL` — LLM providers (at least one required)
- `DEVELOPMENT_MODE=true` — Enables dev bypasses (dev-token auth, etc.)
- `SENTRY_DSN` — Error tracking
- `REDIS_URL` — Optional caching layer
