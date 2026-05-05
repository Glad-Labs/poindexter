# Poindexter Worker (Backend)

FastAPI orchestrator for the Poindexter AI content pipeline. Coordinates specialized agents through a model router with task management, QA gates, and real-time monitoring.

**Version:** 0.1.0
**Runtime:** Python 3.10+ with FastAPI
**Port:** 8002
**Database:** PostgreSQL via asyncpg (raw SQL, no ORM)
**Dependencies:** Poetry (`pyproject.toml`)

## Quick Start

Recommended: run via Docker Compose from the repo root.

```bash
# From repo root
docker compose -f docker-compose.local.yml up -d worker
```

For local Python development (requires Postgres running on port 15432):

```bash
cd src/cofounder_agent
poetry install
poetry run uvicorn main:app --reload --port 8002
```

Requires `~/.poindexter/bootstrap.toml` with `database_url` (created by `poindexter setup`). Ollama is the default LLM provider — no cloud API keys required. All other config lives in the `app_settings` DB table.

## Directory Structure

```
src/cofounder_agent/
├── main.py                    # FastAPI app entry point
├── config/                    # Configuration loading
├── routes/                    # 31 REST endpoint modules
├── services/                  # ~400 service modules
│   ├── database_service.py    # Coordinates 5 DB domain modules
│   ├── model_router.py        # LLM provider selection + fallback (legacy — LiteLLM is the next-gen replacement; see llm_providers/litellm_provider.py)
│   ├── llm_providers/         # LLMProvider plugins (Ollama-native, OpenAI-compat, LiteLLM)
│   ├── prompt_manager.py      # UnifiedPromptManager — Langfuse → DB → YAML resolution
│   ├── unified_orchestrator.py # Master agent choreography
│   ├── workflow_executor.py   # Legacy phase-based execution; being replaced by template_runner.py (LangGraph)
│   ├── template_runner.py     # LangGraph-backed dynamic-pipeline runner
│   ├── capability_registry.py # Intent-based task routing
│   ├── migrations/            # Python migration modules (raw SQL inside)
│   └── phases/                # Workflow phase implementations
├── agents/                    # AI agent implementations
│   ├── content_agent/         # 6-stage content pipeline
│   ├── financial_agent/       # Cost tracking + analysis
│   ├── market_insight_agent/  # Market research
│   └── compliance_agent/      # Compliance checks
├── schemas/                   # Pydantic model files
├── middleware/                 # 5 middleware modules (auth, validation, etc.)
├── utils/                     # 20 utility modules
└── tests/                     # pytest suite
    └── unit/                  # Unit tests (~5,097 passing)
```

## Key Architecture

**Model Router** (`services/model_router.py`): Routes LLM calls by cost tier (`free`/`budget`/`standard`/`premium`/`flagship`). Automatic fallback: Ollama → Anthropic → OpenAI → Google → echo/mock. Never hardcode model names. **Migration in flight (poindexter#199):** the LiteLLM-backed `LLMProvider` plugin (`services/llm_providers/litellm_provider.py`) is the next-gen replacement; activate via `plugin.llm_provider.primary.standard='litellm'`.

**Prompt Manager** (`services/prompt_manager.py`): `UnifiedPromptManager` — Langfuse-first, then DB overrides, then YAML defaults. Operator edits land in the Langfuse UI; takes effect on next `get_prompt` call (60s SDK cache). Activate via the three `langfuse_*` settings in `app_settings`.

**Database** (`services/database_service.py`): asyncpg connection pool with 5 domain modules (Users, Tasks, Content, Admin, WritingStyle). All queries are raw SQL. Migrations in `services/migrations/`.

**Content Pipeline**: 6-stage self-critiquing pipeline: Research → Creative Draft → QA Critique → Creative Refinement → Image Selection → Publishing Prep.

**Workflow Engine** (`services/workflow_executor.py`): Legacy phase-based execution with real-time WebSocket progress events. Phases defined in `services/phases/`. **Migration in flight (poindexter#356):** `template_runner.py` is the LangGraph-backed replacement, already driving the dev_diary template + future architect-composed pipelines.

**Gate History** (`pipeline_gate_history` table): Typed history of HITL gate approvals + regen retries (poindexter#366 phase 1 — replaces the dropped `pipeline_events` event-bus table). Approval service writes; `atoms.approval_gate` + `rejection_handlers` read.

## API

30 route modules at `/api/*`. OpenAPI docs at `/api/openapi.json` when running.

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

Bootstrap config loaded from `~/.poindexter/bootstrap.toml` (created by `poindexter setup`). Runtime config from `app_settings` DB table (200+ keys).

- `database_url` — in bootstrap.toml (the only disk-based config)
- `api_token` — in bootstrap.toml (auto-generated)
- Everything else — in app_settings via the settings API
