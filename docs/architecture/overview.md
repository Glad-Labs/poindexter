# Poindexter Architecture

**Last Updated:** 2026-06-13
**Version:** 0.1.x (alpha)
**Status:** Production-ready on the author's daily-driver setup. Public alpha.

> This document is the mastery-grade reference for how Poindexter is
> built. It is intentionally long. For running the stack locally right
> now, see [../operations/local-development-setup.md](../operations/local-development-setup).

---

## Quick links

- **[Purpose](#purpose)** — what Poindexter does and, more importantly, what it doesn't
- **[System architecture](#system-architecture)** — high-level overview
- **[Technology stack](#technology-stack)** — tools and platforms
- **[Component design](#component-design)** — each subsystem explained
- **[Data architecture](#data-architecture)** — database and storage

---

## Purpose

Poindexter is an open-source AI content pipeline that researches,
writes, reviews, and publishes blog content autonomously, with a
human-in-the-loop approval queue. One operator, one machine.

The target user is a small business owner or solo creator who wants
the speed of AI (several posts per day) but refuses to publish AI
slop that hurts their brand. The product is **quality automated
content with human oversight**, not "AI content factory" and not
"headless CMS with AI."

### Non-goals

- **Not a hosted SaaS.** Poindexter runs on your machine. There is no
  managed tier (yet).
- **Not an "AI co-founder" or general business agent.** It does one
  thing — blog content — and does it well.
- **Not multi-tenant.** One operator, one site. Multi-site is possible
  via config rows but is not a supported deployment model.
- **Not a CMS.** Poindexter pushes static JSON to any S3-compatible
  storage; the frontend can be anything.

### Architecture principles

1. **Local-first.** Ollama inference on your GPU. Paid cloud APIs are
   a fallback, not the default.
2. **PostgreSQL as the spinal cord.** All services communicate through
   shared DB tables, not imports or queues.
3. **Config in the database, not env vars.** The `app_settings` table
   replaces most environment variables. Change settings with SQL, no
   redeploy.
4. **Push-only output.** Poindexter emits static JSON, RSS, and JSON
   Feed to S3-compatible storage. It does not serve pages.
5. **Three layers of anti-hallucination.** Prompts tell the LLM not to
   fabricate, a cross-model critic reviews the output, and a
   deterministic Python validator catches fake people, stats, and
   quotes that slipped through.
6. **Human approval queue.** Every post is reviewed before it goes
   live, unless it scored above the auto-publish threshold AND all
   gates passed.
7. **Self-healing.** The brain daemon monitors every service, restarts
   failures, and alerts on regressions.

---

## 🏗️ System Architecture

### High-Level Overview

```text
┌──────────────────────────────────────────────────────────────────┐
│          OPERATIONS (OpenClaw + Grafana Monitoring)              │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ • Task Management  • Agent Monitor  • Cost Tracking        │ │
│  │ • Performance Dashboards  • Alerting  • Log Aggregation    │ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                              ↕️ REST API
┌──────────────────────────────────────────────────────────────────┐
│         POINDEXTER WORKER                                        │
│                     FastAPI + Python                             │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Prefect flow  →  ContentRouterService                     │ │
│  │     → TemplateRunner (LangGraph canonical_blog template,   │ │
│  │       39 nodes; dev_diary template, 5 nodes)               │ │
│  │  LiteLLM router (primary on prod for all 5 cost tiers;     │ │
│  │     Ollama default, cloud providers behind cost_guard +    │ │
│  │     allow_paid_base_url opt-in per feedback_no_paid_apis)  │ │
│  │  Semantic Memory (pgvector, writer-segregated)             │ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                              ↕️ Internal APIs
│  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────┐       │
│  │  PostgreSQL│ │            │ │ pgvector │ │ Storage  │       │
│  │ (Production)│ │            │ │  Vector  │ │ (Media)  │       │
│  └────────────┘ └────────────┘ └──────────┘ └──────────┘       │
└──────────────────────────────────────────────────────────────────┘
```

### Backend: FastAPI worker (port 8002)

The worker is a FastAPI service that handles all asynchronous task
execution and multi-agent orchestration.

**Architecture conventions:**

- **Unified task API.** All task creation flows through
  `POST /api/tasks` with a `task_type` discriminator.
- **Async DB driver.** The worker uses **asyncpg** directly — no
  SQLAlchemy ORM. Pool lifecycle is managed by the FastAPI lifespan.
- **Prefect dispatch.** `services/flows/content_generation.py` is the
  sole task dispatch path as of 2026-05-16 (Stage 4 of the Prefect
  cutover deleted the legacy `task_executor.py`). The flow claims
  pending `pipeline_tasks` rows via `SELECT ... FOR UPDATE SKIP
LOCKED` and hands them to `content_router_service`. Retry /
  heartbeat / stale-run sweep are Prefect-native; operator UI at
  http://localhost:4200.

### Data Architecture

- **Primary DB**: PostgreSQL 16 with pgvector extension
- **Driver**: `asyncpg` (Full Async)
- **Schema Management**: Managed via `DatabaseService` delegates (`TasksDatabase`, `UsersDatabase`, etc.).

### Request Flow

```text
1. User Action (POST /api/tasks or scheduled task)
   ↓
2. FastAPI route handler validates payload, writes task row (status=pending)
   ↓
3. Prefect content_generation_flow claims the row (SELECT ... FOR UPDATE SKIP LOCKED)
   ↓
4. Flow dispatches by task_type → ContentRouterService
   ↓
5. TemplateRunner runs the canonical_blog LangGraph template (verify → generate → self-review → resolve-links → QA → ...)
   ↓
6. Each stage picks its own model via app_settings (writer, critic, research)
   ↓
7. Provider Protocol invokes the configured LLM (Ollama by default)
   ↓
8. the 12-atom qa.* rail block (qa.programmatic → qa.critic → qa.deepeval → qa.ragas → … → qa.aggregate) runs each reviewer; qa.aggregate combines the scores into the gate decision and halts the graph on reject
   ↓
9. finalize_task writes pipeline_tasks (status=awaiting_approval)
   ↓
10. Human operator approves via /api/tasks/{id}/approve → published
```

---

## 🔧 Technology Stack

### Frontend Architecture

| Component       | Technology                       | Port | Status        |
| --------------- | -------------------------------- | ---- | ------------- |
| **Public Site** | Next.js 16 + React 19 + Tailwind | 3000 | ✅ Production |

**Frontend Features:**

- Server-side rendering (SSR) and static generation (SSG)
- Responsive design with Tailwind CSS
- Component-based architecture
- RESTful API integration
- SEO optimization with sitemap and structured data

### Backend Architecture

| Component             | Technology                      | Port | Status        |
| --------------------- | ------------------------------- | ---- | ------------- |
| **Poindexter Worker** | FastAPI + Python 3.13 + Uvicorn | 8002 | ✅ Production |
| **CMS Data**          | PostgreSQL (Direct Access)      | 5433 | ✅ Production |

**Backend Features:**

- RESTful API (~70 endpoints across tasks, posts, media, memory, pipeline, analytics, webhooks)
- WebSocket support (planned)
- LangGraph-orchestrated pipeline — `canonical_blog` graph_def (39 nodes, seeded into the `pipeline_templates` table from `services/canonical_blog_spec.py`), dispatched by Prefect via `services/flows/content_generation.py`.
- LLM router via LiteLLM (`services/llm_providers/litellm_provider.py`) — primary on prod for all 5 cost tiers (`plugin.llm_provider.primary.{free,budget,standard,premium,flagship}='litellm'`) as of 2026-05-16. Provider routing, cost tracking, and retries all delegated to mature OSS. Paid-vendor model prefixes (`openai/`, `anthropic/`, `gemini/`, …) refuse to dispatch unless `plugin.llm_provider.litellm.allow_paid_base_url=true` (cycle-5 #251, 2026-05-27).
- Semantic memory via pgvector (writer-segregated)
- Async task processing with atomic task-claim via `SELECT ... FOR UPDATE SKIP LOCKED`
- Domain-typed errors via `services/error_handler.py`
- Structured logging via `structlog` and audit-log sidecar

### Infrastructure & Services

| Service        | Provider/Tech                                      | Purpose                      | Status    |
| -------------- | -------------------------------------------------- | ---------------------------- | --------- |
| **Database**   | PostgreSQL only (no SQLite fallback)               | Content and operational data | ✅ Active |
| **Embeddings** | pgvector (in PostgreSQL)                           | Semantic search and RAG      | ✅ Active |
| **Storage**    | File system / Cloud Storage                        | Media files and assets       | ✅ Active |
| **Task Queue** | Prefect (`services/flows/content_generation.py`)   | Async task processing        | ✅ Active |
| **Deployment** | Local docker-compose (backend) / Vercel (frontend) | Self-hosted on your machine  | ✅ Active |
| **Monitoring** | Grafana + Prometheus (self-hosted)                 | 13 dashboards, ~90 panels    | ✅ Active |

### AI Model Providers (Ollama-only pipeline)

| Provider        | Models (production)                                                    | Cost                               | Status                                                                                 |
| --------------- | ---------------------------------------------------------------------- | ---------------------------------- | -------------------------------------------------------------------------------------- |
| **Ollama**      | gemma-4-31b (writer), glm-4.7 (reviser), qwen3:8b, gemma3:27b (critic) | Free (local, GPU electricity only) | ✅ Primary                                                                             |
| **LiteLLM**     | Anthropic / OpenAI / Groq / Gemini / OpenRouter / Bedrock              | Per-token, cloud rates             | 🟡 Opt-in — `plugin.llm_provider.litellm.allow_paid_base_url=true` + `cost_guard` caps |
| **HuggingFace** | transformers direct (emergency fallback)                               | Free (CPU)                         | 🟡 Fallback                                                                            |

**Default chain:** Ollama primary → `pipeline_fallback_model` (also Ollama, default gemma3:27b).

Cloud LLM providers (Anthropic, OpenAI, Groq, etc.) are available via the LiteLLM provider plugin — gated off by default (`allow_paid_base_url=false`) and bounded by `cost_guard` daily/monthly caps when enabled. Operators opt in per the `feedback_no_paid_apis` policy. Local Ollama is the default and zero-cost path.

Use cost tiers (`free`/`budget`/`standard`/`premium`/`flagship`) for model selection — never hardcode model names. Cost tiers live in `app_settings` and map to Ollama models at runtime.

---

## 🧩 Component Design

### 1. Public Site (Next.js)

**Location:** `web/public-site/`

**Purpose:** Public-facing website showcasing content and brand

**Key Features:**

- Homepage with featured posts and content grid
- Individual post pages with full markdown rendering
- Category and tag-based content filtering
- SEO optimization with meta tags and Open Graph
- Newsletter signup integration
- Responsive design optimized for all devices

**Architecture:**

```text
public-site/
├── app/                        # Next.js 16 app router
│   ├── page.js                 # Homepage
│   ├── layout.js               # Root layout
│   ├── posts/[slug]/page.tsx   # Dynamic post pages
│   ├── category/[slug]/page.tsx # Category pages
│   ├── tag/[slug]/page.tsx     # Tag pages
│   ├── archive/[page]/page.tsx # Paginated archive
│   └── author/[id]/page.tsx   # Author pages
├── components/
│   ├── TopNav.js              # Navigation header
│   ├── PostCard.js            # Post preview card
│   ├── Footer.js              # Footer
│   └── [other components]
├── lib/
│   ├── api.js                 # FastAPI client
│   └── utils.js               # Helper functions
├── styles/
│   └── globals.css            # Tailwind styles
└── public/
    └── images/                # Static images
```

**Data Flow:**

```text
Build Time:
  app/posts/[slug]/page.tsx → generateStaticParams → FastAPI
  ↓
  generateMetadata + page render → Fetch post data
  ↓
  Generate static HTML (ISR enabled)

Runtime:
  User visits http://site.com/posts/post-slug
  ↓
  Serve pre-generated static HTML
  ↓
  React hydrates on client
```

### 2. CMS Data Layer (PostgreSQL)

**Location:** `src/cofounder_agent/routes/cms_routes.py`

**Purpose:** Database-driven content management via FastAPI routes (No separate CMS service)

**Data Models (PostgreSQL Tables):**

1. **Posts** (`posts` table)
   - title, slug, content (markdown/rich text)
   - excerpt, featured image, cover image
   - category (relation), tags (relation)
   - author, published date
   - SEO metadata (title, description, keywords)
   - Status (draft, published, archived)

2. **Categories** (`categories` table)
   - name, slug, description
   - Featured image
   - Posts relation
   - Meta description

3. **Tags** (`tags` table)
   - name, slug, description
   - Posts relation
   - Color/icon (for UI)

4. **Pages** (`pages` table)
   - title, slug, content
   - Featured image
   - SEO metadata
   - Visibility settings

5. **Tasks** (`tasks` table)
   - Title, description, type
   - Status (pending, in-progress, completed, failed)
   - Assigned agents
   - Created/updated timestamps
   - Result data

**API Endpoints (FastAPI):**

```bash
GET  /api/posts                    # List posts
GET  /api/posts/:id                # Get single post
POST /api/posts                    # Create post
PUT  /api/posts/:id                # Update post
DELETE /api/posts/:id              # Delete post

GET  /api/categories               # List categories
GET  /api/tags                     # List tags
```

### 3. Pipeline Templates + TemplateRunner

**Location:** `src/cofounder_agent/services/template_runner.py`, `services/pipeline_templates/__init__.py`, `services/canonical_blog_spec.py`; atom implementations live under `modules/content/stages/` + `modules/content/atoms/` (the legacy `services/stages/` tree was removed when the content pipeline moved into the content module, Phase 3, 2026-06-04)

**Purpose:** Compose and run the content pipeline as a LangGraph state machine. The `agents/` tree was deleted 2026-05-09 — there are no role-based "agents" anymore. LLM calls live inline in the stages that need them, dispatched via `services/llm_providers/dispatcher.py` (which routes to the LiteLLM provider on prod).

**How a pipeline is defined:**

A pipeline is a **template** — a LangGraph `StateGraph` plus a `PipelineState` `TypedDict`. As of atom-cutover #355 (2026-06-02) `canonical_blog` ships as a static `graph_def` row in the `pipeline_templates` table (authored in `services/canonical_blog_spec.py`, compiled by `services/pipeline_architect.py::build_graph_from_spec`), preferred by `TemplateRunner.run` when `pipeline_use_graph_def=true` (the prod default). `dev_diary` still ships in-tree as a Python factory in `services/pipeline_templates/__init__.py` — the only entry left in `TEMPLATES` after the hand-coded `canonical_blog` factory was deleted:

- `canonical_blog` — the 36-node default for blog posts (`services/canonical_blog_spec.py` is the authoritative node list). Six linear blocks: verify → **writer** (generate_draft → generate_title → check_title_originality → normalize_draft → optional `draft_gate` → writer_self_review → resolve_internal_link_placeholders → reconcile_citations) → **quality + images** (quality_evaluation → url_validation → plan/generate/inject inline images → source_featured_image → caption_images) → the **12-atom qa.\* rail block** (qa.programmatic → … → qa.aggregate, which replaced the deleted `cross_model_qa` stage) → **seo.generate_all_metadata** → media scripts → **finalize** (compile_meta → persist_task → record_pipeline_version → evaluate_auto_publish)
- `dev_diary` — 5-node subset for the build-in-public stream (verify_task → narrate_bundle → generate_seo_metadata → source_featured_image → finalize_task)

Per-task template selection lives on `pipeline_tasks.template_slug`. A NULL value fails loud per `feedback_no_silent_defaults`.

**How a run executes:**

`TemplateRunner.run(state, *, graph)` compiles the graph (optionally with `AsyncPostgresSaver` for resumable runs), drives it to completion or halt, and returns a `TemplateRunSummary` with per-node timing + metrics. Stages are adapted onto the graph via `make_stage_node(stage)` so the legacy `Stage.execute(context)` shape still works — no rewrite required to lift a stage into a template.

**Usage patterns:**

- **End-to-end content:** `POST /api/tasks` → Prefect `content_generation_flow` claims the row → `ContentRouterService` dispatches to `TemplateRunner.run(template_slug, context)`
- **Ad-hoc template use:** stages are called directly in tests and scripts; not exposed via the public API.

See [`services/template_runner.md`](services/template_runner) for the runner's invariants.

### 4. Poindexter Worker (FastAPI Backend)

**Location:** `src/cofounder_agent/`

**Purpose:** Central orchestrator for all AI-powered operations

**Core Components:**

#### Main API (`main.py`)

- FastAPI application
- ~70 REST endpoints (see [API reference](../api/index) for the inventory)
- Error handling and logging
- CORS middleware
- Request/response validation via Pydantic models

#### LLM Router (`services/llm_providers/litellm_provider.py` via dispatcher)

- LiteLLM-backed `LLMProvider` plugin — primary router as of 2026-05-16 (`plugin.llm_provider.primary.{free,budget,standard,premium,flagship}='litellm'` on prod)
- Cost-tier API: `await resolve_tier_model(pool, "standard")` from `services/llm_providers/dispatcher.py`; operators tune per-tier model via `app_settings.cost_tier.<tier>.model` rows
- Automatic provider routing + cost tracking + retries via mature OSS (LiteLLM)
- Langfuse callback auto-traces every call
- The hand-rolled `model_router.py` / `usage_tracker.py` / `model_constants.py` trio was deleted in Phase 2 cleanup (2026-05-08)

#### Pipeline Templates + Stages (`services/pipeline_templates/__init__.py` + `modules/content/stages/*`)

- `Stage` protocol: `name: str`, `async def run(context) -> context` — implemented per-stage in `modules/content/stages/`
- `TemplateRunner` (LangGraph) orchestrates the pipeline — `canonical_blog` from the DB `graph_def` (compiled by `pipeline_architect.build_graph_from_spec`) when `pipeline_use_graph_def=true` (the prod default since #355), `dev_diary` from its in-tree `TEMPLATES` factory. Halts naturally when a node returns a terminal state (e.g. `qa.aggregate` rejecting). The legacy `DEFAULT_STAGE_ORDER` list + `plugins/stage_runner.py` were deleted 2026-05-16 (Lane C Stage 4)
- Context dict threads through every stage — the pipeline's shared memory. Live service handles ride in `RunnableConfig.configurable["__services__"]` so they don't serialize into checkpoints (poindexter#382)
- Adding a new stage = drop a file in `modules/content/stages/`, register it in `plugins/registry.py`, then add it as a node: for `canonical_blog` edit the `graph_def` spec (`services/canonical_blog_spec.py`, re-seeded into `pipeline_templates.graph_def`); for `dev_diary` add it to the `StateGraph` factory in `services/pipeline_templates/__init__.py`

#### Semantic Memory (`services/embedding_service.py` + pgvector)

- pgvector extension in PostgreSQL 16 powers cosine-similarity search
- `embeddings` table stores 768-dim vectors keyed by `(source_table, source_id)`
- Writer-segregated: `brain`, `audit`, `posts`, `memory`, `claude_sessions`, `issues`
- Accessible via `poindexter memory search "..."` (CLI) or `GET /api/memory/search` (API)
- Retention policy (stale embedding cleanup) tracked at GH-106

**API Endpoints (Core):**

```bash
POST /api/tasks              # Create task
GET  /api/tasks/:id          # Get task status
GET  /api/tasks              # List tasks
PUT  /api/tasks/:id          # Update task

POST /api/models/test        # Test model connection
GET  /api/models             # List available models
POST /api/models/configure   # Configure model

GET  /api/health             # System health check
GET  /api/metrics            # Performance metrics
```

---

## 🗄️ Data Architecture

### Key tables

The full schema lives in `services/migrations/0000_baseline.py`. The most operationally important tables:

| Table                   | What it stores                                                                       |
| ----------------------- | ------------------------------------------------------------------------------------ |
| `posts`                 | Published blog posts. `metadata->>'pipeline_task_id'` links back to the source task. |
| `pipeline_tasks`        | Content pipeline queue — Prefect claims pending rows.                                |
| `pipeline_versions`     | Per-task generated content + qa_feedback snapshots.                                  |
| `atom_runs`             | Per-atom run + outcome for the graph_def pipeline path.                              |
| `app_settings`          | All runtime config (~1,090 keys, ~68 secret).                                        |
| `embeddings`            | pgvector 768-dim vectors (posts / audit / brain / memory / claude_sessions).         |
| `audit_log`             | Canonical historical record for every pipeline event.                                |
| `pipeline_gate_history` | HITL gate approvals + regen retries.                                                 |
| `qa_gates`              | Declarative QA gate definitions (required_to_pass, advisory).                        |
| `brain_knowledge`       | Self-maintained knowledge graph (entity / attribute / value).                        |
| `cost_logs`             | Per-call LLM cost tracking.                                                          |

See [`docs/architecture/database-schema.md`](database-schema.md) for the complete table inventory, and [`docs/operations/migrations.md`](../operations/migrations.md) for the migration system.

---

## Roadmap

The roadmap is tracked via GitHub milestones at
[Glad-Labs/poindexter/milestones](https://github.com/Glad-Labs/poindexter/milestones).

| Milestone                 | Status      | Description                                                                         |
| ------------------------- | ----------- | ----------------------------------------------------------------------------------- |
| M1: Stabilize             | Done        | Pipeline runs end-to-end, fresh clone works, tests pass                             |
| M3: Launch Poindexter Pro | In progress | Single subscription tier on Lemon Squeezy — $19/mo or $180/yr, Founding Member rate |
| Backlog                   | Ongoing     | 30+ issues for post-revenue features                                                |

---

## Security

- **OAuth 2.1 client credentials** for all API access (JWT minted via `POST /token` against a registered `oauth_clients` row — Glad-Labs/poindexter#241 / #249)
- **Dev-token bypass** blocked in production (`DEVELOPMENT_MODE` check)
- **Secrets in DB** (`is_secret=true` keys fetched via `site_config.get_secret()`, filtered from in-memory cache)
- **No cloud keys in env** — LLM API keys set via settings API, not env vars
- See [SECURITY.md](../../SECURITY.md) for the full model.

---

## Related Documentation

- **[Database Schema](database-schema)** — every table + migration system
- **[API Reference](../api/index)** — REST endpoints
- **[Local Development](../operations/local-development-setup)** — setup walkthrough
