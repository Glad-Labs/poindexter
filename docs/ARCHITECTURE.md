# Poindexter Architecture

**Last Updated:** 2026-04-11
**Version:** 0.1.x (alpha)
**Status:** Production-ready on the author's daily-driver setup. Public alpha.

> This document is the mastery-grade reference for how Poindexter is
> built. It is intentionally long. For a guided setup in 30 minutes,
> see the [Quick Start Guide](https://www.gladlabs.io/products/quick-start).
> For running the stack locally right now, see
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
│  │  Multi-Provider Model Router (Ollama/OpenAI/Claude/Gemini)│ │
│  │  Multi-Agent Orchestrator & Task Distribution             │ │
│  │  Memory System & Context Management                       │ │
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
3. **TaskExecutor**: Background polling picks up the task and calls `UnifiedOrchestrator`.
4. **UnifiedOrchestrator**: Parses intent and routes to the correct Agent Pipeline.

### Data Architecture

- **Primary DB**: PostgreSQL 15+
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
1. User Action (API call or scheduled task)
   ↓
2. REST API call to the Poindexter worker
   ↓
3. Request Processing & Routing
   ↓
4. Multi-Agent Orchestrator selects agents
   ↓
5. Agents execute tasks (in parallel when possible)
   ↓
6. Model Router selects best AI model
   ↓
7. LLM API call (Ollama/OpenAI/Claude/Gemini)
   ↓
8. Response aggregation
   ↓
9. Result stored in PostgreSQL
   ↓
10. Response sent back to UI
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

- RESTful API (160+ endpoints)
- WebSocket support (planned)
- Multi-agent orchestration
- Model routing and fallback
- Memory system with context awareness
- Async task processing
- Error handling and recovery
- Comprehensive logging

### Infrastructure & Services

| Service        | Provider/Tech                                      | Purpose                      | Status    |
| -------------- | -------------------------------------------------- | ---------------------------- | --------- |
| **Database**   | PostgreSQL only (no SQLite fallback)               | Content and operational data | ✅ Active |
| **Embeddings** | pgvector (in PostgreSQL)                           | Semantic search and RAG      | ✅ Active |
| **Storage**    | File system / Cloud Storage                        | Media files and assets       | ✅ Active |
| **Task Queue** | REST API + async workers (dev/prod)                | Async task processing        | ✅ Active |
| **Deployment** | Local docker-compose (backend) / Vercel (frontend) | Self-hosted on your machine  | ✅ Active |
| **Monitoring** | Grafana + Prometheus (self-hosted)                 | 6 dashboards, ~90 panels     | ✅ Active |

### AI Model Providers (Multi-Provider Support)

| Provider      | Models (production)                  | Cost | Priority |
| ------------- | ------------------------------------ | ---- | -------- |
| **Ollama**    | gemma3:27b, qwen3:8b, phi4:14b, etc. | Free | #1       |
| **Anthropic** | Claude (via app_settings key)        | Paid | #2       |
| **OpenAI**    | GPT (via app_settings key)           | Paid | #3       |
| **Google**    | Gemini (via app_settings key)        | Paid | #4       |

**Fallback Chain (Automatic):** Ollama (local, free) → Anthropic → OpenAI → Google → echo/mock. Use cost tiers (`free`/`budget`/`standard`/`premium`), never hardcode model names.

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

### 3. Agent System Architecture (Self-Critiquing Pipeline)

**Location:** `src/agents/content_agent/`

**Purpose:** Modular AI agents for specialized tasks with self-critique feedback loops

**Key Features:**

- Self-critiquing pipeline: Creative generation → QA evaluation → Feedback → Refinement
- Individual agent capabilities: Research, Creative, Images, Publishing, QA, Summarizer
- Model fallback chain: Claude 3 Opus → GPT-4 → Gemini → Ollama (local, zero-cost)
- Modular usage: End-to-end blog generation OR individual agent access
- Output formatting: Markdown + SEO assets + Database compatible

**Core Agents:**

```python
# Agent roles and responsibilities
- CreativeAgent: Content generation with style consistency
- ResearchAgent: Topic research and fact gathering
- ImageAgent: Image selection and optimization
- PublishingAgent: Database formatting and publishing
- QAAgent: Quality evaluation and improvement suggestions
- SummarizerAgent: Extract key points and outline creation
```

**Self-Critiquing Pipeline Flow:**

```text
1. Input: Topic/Request
   ↓
2. ResearchAgent → Research data
   ↓
3. CreativeAgent → Draft content
   ↓
4. QAAgent → Evaluate & critique
   ↓
5. CreativeAgent (with feedback) → Refined content
   ↓
6. ImageAgent → Select visual assets
   ↓
7. PublishingAgent → Format for CMS
   ↓
8. Output: Publication-ready content
```

**Usage Patterns:**

- **End-to-end Content:** POST `/api/tasks` → Executes agent pipeline via TaskExecutor
- **Individual agents:** POST `/api/agents/{agent-name}` → Specific capability
- **Custom workflows:** Combine agents in any order for flexible pipelines

### 4. Poindexter Worker (FastAPI Backend)

**Location:** `src/cofounder_agent/`

**Purpose:** Central orchestrator for all AI-powered operations

**Core Components:**

#### Main API (`main.py`)

- FastAPI application
- 50+ REST endpoints
- Error handling and logging
- CORS middleware
- Request/response validation

#### Model Router (`services/model_router.py`)

- Multi-provider AI orchestration
- Automatic provider fallback (Claude → GPT → Gemini → Ollama)
- Cost tracking and optimization
- Rate limiting
- Token counting

#### Multi-Agent Orchestrator (`multi_agent_orchestrator.py`)

- Agent lifecycle management
- Task distribution and scheduling
- Parallel execution coordination
- Result aggregation
- Error recovery

#### Specialized Agents

```python
# Each agent inherits from BaseAgent

class ContentAgent(BaseAgent):
    """Generates and manages content"""
    - Content planning
    - Blog post generation
    - Social media content
    - Email campaigns

class FinancialAgent(BaseAgent):
    """Manages business financials"""
    - Cost tracking
    - Revenue calculations
    - Budget management
    - Financial projections

class MarketInsightAgent(BaseAgent):
    """Market analysis and trends"""
    - Competitor analysis
    - Trend identification
    - Audience insights
    - Opportunity detection

class ComplianceAgent(BaseAgent):
    """Legal and regulatory compliance"""
    - Content compliance checking
    - GDPR/CCPA checks
    - Risk assessment
    - Privacy policy management
```

#### Memory System (`memory_system.py`)

- Short-term context (current conversation)
- Long-term memory (persistent storage)
- Semantic search across memories
- Automatic cleanup and optimization

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

| Milestone           | Status      | Description                                             |
| ------------------- | ----------- | ------------------------------------------------------- |
| M1: Stabilize       | Done        | Pipeline runs end-to-end, fresh clone works, tests pass |
| M3: Ship the Guide  | In progress | $29 Quick Start Guide on Lemon Squeezy                  |
| M4: Premium Prompts | Planned     | $9/mo recurring subscription                            |
| Backlog             | Ongoing     | 30+ issues for post-revenue features                    |

---

## Security

- **Bearer token auth** for all API access (single `api_token` in bootstrap.toml + app_settings)
- **Dev-token bypass** blocked in production (`DEVELOPMENT_MODE` check)
- **Secrets in DB** (`is_secret=true` keys fetched via `site_config.get_secret()`, filtered from in-memory cache)
- **No cloud keys in env** — LLM API keys set via settings API, not env vars
- See [SECURITY.md](../SECURITY.md) for the full model.

---

## Related Documentation

- **[Multi-Agent Pipeline](architecture/multi-agent-pipeline.md)** — content pipeline + cross-model QA
- **[Database Schema](architecture/database-schema.md)** — every table + migration system
- **[API Reference](api/README.md)** — REST endpoints
- **[Local Development](operations/local-development-setup.md)** — setup walkthrough
- **[Feature Status](feature-status.md)** — honest inventory of what works
