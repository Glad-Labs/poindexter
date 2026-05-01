# Poindexter Architecture

**Last Updated:** 2026-04-23
**Version:** 0.1.x (alpha)
**Status:** Production-ready on the author's daily-driver setup. Public alpha.

> This document is the mastery-grade reference for how Poindexter is
> built. It is intentionally long. For a guided setup in 30 minutes,
> see [Poindexter Pro](https://www.gladlabs.ai/guide) which includes
> the full setup book. For running the stack locally right now, see
> [operations/local-development-setup.md](operations/local-development-setup.md).

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
│         POINDEXTER WORKER (Central Brain)                        │
│                     FastAPI + Python                             │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  TaskExecutor  →  ContentRouterService                     │ │
│  │     → StageRunner (12 sequential Stage plugins)            │ │
│  │  Provider Protocol (Ollama primary, cloud providers        │ │
│  │     pluggable for fallback — GH-104)                       │ │
│  │  Semantic Memory (pgvector, writer-segregated)             │ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                              ↕️ Internal APIs
### Backend: FastAPI worker (port 8002)

The worker is a FastAPI service that handles all asynchronous task
execution and multi-agent orchestration.

**Architecture conventions:**
- **Unified task API.** All task creation flows through
  `POST /api/tasks` with a `task_type` discriminator.
- **Async DB driver.** The worker uses **asyncpg** directly — no
  SQLAlchemy ORM. Pool lifecycle is managed by the FastAPI lifespan.
- **Polling executor.** The `TaskExecutor` service runs a background
  polling loop (every 5 seconds) that uses `SELECT ... FOR UPDATE
  SKIP LOCKED` to atomically claim pending tasks. Multiple workers
  can share one database without stepping on each other.

**Request Flow:**
1. **POST `/api/tasks`**: User creates a task (e.g., `task_type="blog_post"`).
2. **PostgreSQL**: Task is stored as `pending`.
3. **TaskExecutor**: Background polling picks up the task and dispatches by `task_type`.
4. **ContentRouterService**: For blog posts, runs the 12-stage `StageRunner` chain. Other task types route to their own service.

### Data Architecture

- **Primary DB**: PostgreSQL 16 with pgvector extension
- **Driver**: `asyncpg` (Full Async)
- **Schema Management**: Managed via `DatabaseService` delegates (`TasksDatabase`, `UsersDatabase`, etc.).
│  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────┐       │
│  │  PostgreSQL│ │            │ │ pgvector │ │ Storage  │       │
│  │ (Production)│ │            │ │  Vector  │ │ (Media)  │       │
│  └────────────┘ └────────────┘ └──────────┘ └──────────┘       │
└──────────────────────────────────────────────────────────────────┘
```

### Request Flow

```text
1. User Action (POST /api/tasks or scheduled task)
   ↓
2. FastAPI route handler validates payload, writes task row (status=pending)
   ↓
3. TaskExecutor polling loop claims the row (SELECT ... FOR UPDATE SKIP LOCKED)
   ↓
4. TaskExecutor dispatches by task_type → ContentRouterService
   ↓
5. StageRunner runs the 12-stage pipeline (generate → self-review → QA → ...)
   ↓
6. Each stage picks its own model via app_settings (writer, critic, research)
   ↓
7. Provider Protocol invokes the configured LLM (Ollama by default)
   ↓
8. cross_model_qa aggregates reviewer scores; rewrite loop if below threshold
   ↓
9. finalize_task writes content_tasks (status=awaiting_approval)
   ↓
10. Human operator approves via /api/tasks/{id}/approve → published
```

---

## 🔧 Technology Stack

### Frontend Architecture

| Component       | Technology                       | Port | Status        |
| --------------- | -------------------------------- | ---- | ------------- |
| **Public Site** | Next.js 15 + React 18 + Tailwind | 3000 | ✅ Production |

**Frontend Features:**

- Server-side rendering (SSR) and static generation (SSG)
- Responsive design with Tailwind CSS
- Component-based architecture
- RESTful API integration
- SEO optimization with sitemap and structured data

### Backend Architecture

| Component             | Technology                      | Port  | Status        |
| --------------------- | ------------------------------- | ----- | ------------- |
| **Poindexter Worker** | FastAPI + Python 3.12 + Uvicorn | 8002  | ✅ Production |
| **CMS Data**          | PostgreSQL (Direct Access)      | 15432 | ✅ Production |

**Backend Features:**

- RESTful API (~70 endpoints across tasks, posts, media, memory, pipeline, analytics, webhooks)
- WebSocket support (planned)
- Stage-plugin pipeline (12 sequential stages with halt semantics)
- Provider Protocol abstraction (Ollama primary; cloud providers pluggable per GH-104)
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
| **Task Queue** | REST API + async workers (dev/prod)                | Async task processing        | ✅ Active |
| **Deployment** | Local docker-compose (backend) / Vercel (frontend) | Self-hosted on your machine  | ✅ Active |
| **Monitoring** | Grafana + Prometheus (self-hosted)                 | 6 dashboards, ~90 panels     | ✅ Active |

### AI Model Providers (Ollama-only pipeline)

| Provider          | Models (production)                                               | Cost                               | Status      |
| ----------------- | ----------------------------------------------------------------- | ---------------------------------- | ----------- |
| **Ollama**        | gemma3:27b, qwen3:8b, phi4:14b, phi3, glm-4.7-5090 (custom build) | Free (local, GPU electricity only) | ✅ Primary  |
| **HuggingFace**   | transformers direct (emergency fallback)                          | Free (CPU)                         | 🟡 Fallback |
| ~~Anthropic~~     | _Removed session 55_                                              | —                                  | ❌          |
| ~~OpenAI~~        | _Removed session 55_                                              | —                                  | ❌          |
| ~~Google Gemini~~ | _Removed session 55_                                              | —                                  | ❌          |

**Current chain:** Ollama primary → `pipeline_fallback_model` (also Ollama, default gemma3:27b) → HuggingFace transformers (emergency, CPU).

Cloud LLM providers were removed from the pipeline in session 55 to honor the "no paid APIs" rule. Customers forking the repo can re-enable them via community plugins (future Phase J of the [plugin architecture refactor](architecture/plugin-architecture.md)).

Use cost tiers (`free`/`budget`/`standard`/`premium`) for model selection — never hardcode model names. Cost tiers live in `app_settings` and map to Ollama models at runtime.

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
├── app/                        # Next.js 15 app router
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

### 3. Agent System Architecture

**Location:** `src/cofounder_agent/agents/`

**Purpose:** Specialized role-based agents used by pipeline stages. Agents are stateless wrappers around LLM calls with typed inputs/outputs — not classical "autonomous agents" with long-lived state.

**Actual agents in the current codebase:**

- `blog_content_generator_agent.py` — drafts the post body, handles RAG context injection and title dedup
- `blog_image_agent.py` — routes between SDXL and Pexels for inline + featured images
- `blog_publisher_agent.py` — finalizes post metadata and formats for storage
- `blog_quality_agent.py` — the LLM critic that scores drafts on clarity / accuracy / completeness / relevance / SEO / readability / engagement
- `content_agent/` (subpackage) — research + sub-agents for fact gathering

A lightweight `registry.py` wires agent instances into the pipeline. No `BaseAgent` inheritance; agents are composed, not sub-classed.

**Stage-driven pipeline (not agent-driven):**

As of the Phase F+G refactor, the pipeline runs through `StageRunner` and 12 sequential stages (see `services/stages/`, catalogued in [`reference/services.md`](../reference/services.md)). Agents are called _by stages_ when an LLM invocation is needed — they don't orchestrate each other. The self-critiquing loop happens inside `services/stages/cross_model_qa.py`, not via agent-to-agent messaging.

**Usage patterns:**

- **End-to-end content:** `POST /api/tasks` → `TaskExecutor` claims the row → `ContentRouterService` runs the stage chain
- **Ad-hoc agent use:** Not exposed via the public API. Operators call stages directly in tests and scripts.

### 4. Poindexter Worker (FastAPI Backend)

**Location:** `src/cofounder_agent/`

**Purpose:** Central orchestrator for all AI-powered operations

**Core Components:**

#### Main API (`main.py`)

- FastAPI application
- ~70 REST endpoints (see [API reference](../api/README.md) for the inventory)
- Error handling and logging
- CORS middleware
- Request/response validation via Pydantic models

#### Model Router (`services/model_router.py`)

- Ollama-only orchestration with cost-tier routing (free/budget/standard/premium)
- Automatic fallback chain on Ollama: `pipeline_writer_model` → `pipeline_fallback_model` → HuggingFace transformers (CPU emergency)
- Electricity cost tracking (per-call, based on GPU wattage × inference time × `electricity_rate_kwh`)
- Token counting per task type (`model_token_limits_by_task` JSON in app_settings)
- Future refactor: extracts into `LLMProvider` plugin family (GitHub [#64 Phase J](https://github.com/Glad-Labs/poindexter/issues/64))

#### Stage Plugin System (`plugins/stage.py` + `services/stages/*`)

- `Stage` protocol: `name: str`, `async def run(context) -> context`
- `StageRunner` calls each stage in `DEFAULT_STAGE_ORDER` and short-circuits if a stage returns `halt=True` (e.g. `cross_model_qa` on an unrecoverable reject)
- Context dict threads through every stage — the pipeline's shared memory
- Adding a new stage = drop a file in `services/stages/`, register in `DEFAULT_STAGE_ORDER`, no other code changes

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

### Entity Relationship Diagram

```text
┌─────────────┐         ┌─────────────┐
│   Posts     │────────▶│ Categories  │
│ (many)      │  1..n   │   (1)       │
└─────────────┘         └─────────────┘

┌─────────────┐         ┌─────────────┐
│   Posts     │────────▶│    Tags     │
│ (many)      │  m..n   │  (many)     │
└─────────────┘         └─────────────┘

┌─────────────┐         ┌─────────────┐
│   Posts     │────────▶│   Authors   │
│ (many)      │  1..n   │   (1)       │
└─────────────┘         └─────────────┘

┌─────────────┐         ┌─────────────┐
│   Tasks     │────────▶│   Agents    │
│ (many)      │  1..n   │  (many)     │
└─────────────┘         └─────────────┘

┌──────────────┐        ┌─────────────┐
│  Memories    │────────▶│   Agents    │
│ (many)       │  1..n   │   (1)       │
└──────────────┘        └─────────────┘
```

### Database Schema

**Posts Table:**

```sql
CREATE TABLE posts (
  id UUID PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  slug VARCHAR(255) UNIQUE NOT NULL,
  content TEXT NOT NULL,
  excerpt VARCHAR(500),
  featured_image_id UUID,
  cover_image_id UUID,
  category_id UUID REFERENCES categories(id),
  author_id UUID REFERENCES authors(id),
  status VARCHAR(50) DEFAULT 'draft',
  seo_title VARCHAR(255),
  seo_description VARCHAR(500),
  seo_keywords VARCHAR(255),
  published_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);
```

**Tasks Table:**

```sql
CREATE TABLE tasks (
  id UUID PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  description TEXT,
  type VARCHAR(100) NOT NULL,
  status VARCHAR(50) DEFAULT 'pending',
  assigned_agents TEXT[],
  result_data JSONB,
  error_message TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  completed_at TIMESTAMP
);
```

**Memory Table:**

```sql
CREATE TABLE memories (
  id UUID PRIMARY KEY,
  agent_id UUID NOT NULL,
  content TEXT NOT NULL,
  embedding VECTOR(1536),
  memory_type VARCHAR(50),
  created_at TIMESTAMP DEFAULT NOW(),
  accessed_at TIMESTAMP DEFAULT NOW()
);
```

---

## Roadmap

The roadmap is tracked via GitHub milestones at
[Glad-Labs/poindexter/milestones](https://github.com/Glad-Labs/poindexter/milestones).

| Milestone                 | Status      | Description                                                                   |
| ------------------------- | ----------- | ----------------------------------------------------------------------------- |
| M1: Stabilize             | Done        | Pipeline runs end-to-end, fresh clone works, tests pass                       |
| M3: Launch Poindexter Pro | In progress | Single subscription tier on Lemon Squeezy — $9/mo or $89/yr, 7-day free trial |
| Backlog                   | Ongoing     | 30+ issues for post-revenue features                                          |

---

## Security

- **Bearer token auth** for all API access (single `api_token` in bootstrap.toml + app_settings)
- **Dev-token bypass** blocked in production (`DEVELOPMENT_MODE` check)
- **Secrets in DB** (`is_secret=true` keys fetched via `site_config.get_secret()`, filtered from in-memory cache)
- **No cloud keys in env** — LLM API keys set via settings API, not env vars
- See [SECURITY.md](../SECURITY.md) for the full model.

---

## Related Documentation

- **[Content Pipeline](architecture/content-pipeline.md)** — the 12-stage Stage plugin chain + cross-model QA
- **[Database Schema](architecture/database-schema.md)** — every table + migration system
- **[API Reference](api/README.md)** — REST endpoints
- **[Local Development](operations/local-development-setup.md)** — setup walkthrough
