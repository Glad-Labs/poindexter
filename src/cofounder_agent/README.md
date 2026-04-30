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
├── services/                  # ~76 service modules
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
├── schemas/                   # Pydantic model files
├── middleware/                 # 5 middleware modules (auth, validation, etc.)
├── utils/                     # 20 utility modules
└── tests/                     # pytest suite
    └── unit/                  # Unit tests (~5,097 passing)
```

## Key Architecture

**Model Router** (`services/model_router.py`): Routes LLM calls by cost tier (`free`/`budget`/`standard`/`premium`/`flagship`). Automatic fallback: Ollama → Anthropic → OpenAI → Google → echo/mock. Never hardcode model names.

**Database** (`services/database_service.py`): asyncpg connection pool with 5 domain modules (Users, Tasks, Content, Admin, WritingStyle). All queries are raw SQL. Migrations in `services/migrations/`.

**Content Pipeline**: 6-stage self-critiquing pipeline: Research → Creative Draft → QA Critique → Creative Refinement → Image Selection → Publishing Prep.

**Workflow Engine** (`services/workflow_executor.py`): Phase-based execution with real-time WebSocket progress events. Phases defined in `services/phases/`.

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

### Content-agent runtime settings

The content agent (`agents/content_agent/config.py`) reads the keys below from `app_settings` first and falls back to the matching environment variable for back-compat. Set them with `poindexter settings set <key> <value>` — no worker restart required.

| `app_settings` key     | Env-var fallback       | Default      | What it controls                                                                       |
| ---------------------- | ---------------------- | ------------ | -------------------------------------------------------------------------------------- |
| `serper_api_key`       | `SERPER_API_KEY`       | *(empty)*    | Serper search API key for real-time web search. Empty disables web search safely. Stored with `is_secret=true`. |
| `local_llm_api_url`    | `LOCAL_LLM_API_URL`    | *(empty)*    | Ollama base URL (e.g. `http://localhost:11434`). Empty means "Ollama not configured" — callers handle that explicitly. |
| `local_llm_model_name` | `LOCAL_LLM_MODEL_NAME` | `auto`       | Ollama model fallback when no per-task model is configured. `auto` lets `OllamaClient` pick the first pulled model. |
| `max_log_size_mb`      | `MAX_LOG_SIZE_MB`      | `5`          | Rotating log file size cap in MB.                                                      |
| `max_log_backup_count` | `MAX_LOG_BACKUP_COUNT` | `3`          | Number of rotated log backups to retain.                                               |

```bash
# Example — point the content agent at a remote Ollama host without restarting:
poindexter settings set local_llm_api_url http://gpu-box.lan:11434
poindexter settings set local_llm_model_name qwen3:30b

# Rotate the Serper key:
poindexter settings set serper_api_key sk_xxx
```

Migration `0116_seed_content_agent_app_settings.py` seeds these rows from your current env-vars at apply time, so existing deployments keep their tuned values.
