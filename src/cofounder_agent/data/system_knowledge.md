# Glad Labs System Knowledge Base

This is the authoritative knowledge base for accurate information about the Glad Labs platform.

## Platform Overview

Glad Labs is a production-ready AI orchestration system that combines autonomous agents, multi-provider LLM routing, and full-stack web applications into a unified platform for intelligent task automation and content generation.

## Core Architecture

### Technology Stack

**Backend:**
- **Framework:** FastAPI (Python 3.12+)
- **Language:** Python
- **Server:** Uvicorn
- **Port:** 8000
- **Async:** AsyncIO with event-based concurrency

**Frontend:**
- **Public Site:** Next.js 15
- **Language:** TypeScript
- **Framework:** React 18
- **Styling:** TailwindCSS
- **Port:** 3000

**Admin UI:**
- **Framework:** React 18
- **Component Library:** Material-UI
- **Language:** TypeScript
- **Port:** 3001

**Database:**
- **Primary:** PostgreSQL
- **Port:** 5432
- **ORM:** SQLAlchemy
- **Migrations:** Alembic

**Real-time Communication:**
- **WebSocket:** FastAPI WebSocket
- **Status:** Fully integrated for progress updates
- **Events:** Task execution, workflow progress, chat streaming

### Architecture Pattern

The system uses a **monorepo structure** with three independent services:
1. **Cofounder Agent:** Core orchestration system (FastAPI backend)
2. **Public Site:** Content distribution and user interface (Next.js)
3. **Oversight Hub:** Administrative control center (React + Material-UI)

All three services:
- Read configuration from single `.env.local` file at project root
- Listen on different ports (8000, 3000, 3001)
- Start together via `npm run dev` command
- Are fully async and non-blocking

## LLM Provider Integration

### Supported Providers (4 primary + fallback)

1. **Ollama** (Local)
   - Model: llama2 (default)
   - Cost: Free (runs locally)
   - Latency: ~20ms
   - Priority: PRIMARY (lowest cost)

2. **Anthropic Claude**
   - Models: Claude 3.5 Sonnet, Claude 3 Opus
   - Requires: ANTHROPIC_API_KEY
   - Priority: FALLBACK 1 (if Ollama unavailable)

3. **OpenAI**
   - Models: GPT-4, GPT-4 Turbo, GPT-3.5 Turbo
   - Requires: OPENAI_API_KEY
   - Priority: FALLBACK 2

4. **Google Gemini**
   - Models: Gemini Pro, Gemini Ultra
   - Requires: GOOGLE_API_KEY
   - Priority: FALLBACK 3

5. **HuggingFace Models**
   - Inference API access
   - Requires: HUGGINGFACE_API_KEY
   - Cost: Varies by model

### Intelligent Model Router

The system includes `model_router.py` that:
- Automatically selects the best available provider
- Implements fallback chain: Ollama → Anthropic → OpenAI → Google → Mock
- Routes based on API key availability (not manual selection)
- Supports cost-tier selection (ultra_cheap, cheap, balanced, premium, ultra_premium)
- Defaults to Ollama for zero-cost local inference
- No hardcoded model names — uses cost tiers for flexibility

### Model Selection Behavior

When user selects a model:
- Format: "provider-modelname" (e.g., "ollama-llama2", "openai-gpt4")
- Parser extracts provider and model name
- Router checks API key availability
- If unavailable: Falls back to next provider in chain
- If all fail: Returns mock response with graceful error

## Specialized Agent System

### 5 Core Agent Types

1. **Content Agent**
   - Purpose: Blog posts, articles, newsletters
   - Pipeline: 6-stage self-critiquing process
   - Features: Research, creative generation, QA critique, refinement, image selection, publishing
   - Quality Framework: Tone, structure, SEO, engagement, accuracy, style consistency

2. **Financial Agent**
   - Purpose: Cost tracking, ROI analysis, budget management
   - Features: Track spending by model/provider, optimize costs
   - Specialization: Multi-provider cost comparison, financial metrics

3. **Market Insight Agent**
   - Purpose: Trend analysis, market research, competitive intelligence
   - Features: Analyze market data, identify opportunities
   - Specialization: Data-driven insights and recommendations

4. **Compliance Agent**
   - Purpose: Legal review, risk assessment, regulatory compliance
   - Features: Check content for legal issues, compliance requirements
   - Specialization: GDPR, privacy, regulatory frameworks

5. **Orchestrator Agent (Co-Founder)**
   - Purpose: Master agent that coordinates other agents
   - Features: Route tasks to specialized agents, manage workflows
   - Specialization: Task routing, workflow choreography, multi-agent coordination

### Agent Orchestration

- Agents are coordinated by the Unified Orchestrator service
- Each agent has specific capabilities and tools
- Agent selection is available in UI with clear descriptions
- Agents communicate via standardized message format
- Results are persisted to PostgreSQL for audit trail

## Service Architecture

### 60+ Service Modules

The backend includes specialized service modules for:

**Core Orchestration:**
- `unified_orchestrator.py` — Master workflow coordination
- `task_executor.py` — Background task execution with polling
- `model_router.py` — Intelligent LLM provider selection

**Database:**
- `database_service.py` — Coordinator for 5 specialized DB modules
- `users_db.py` — User accounts, authentication, OAuth
- `tasks_db.py` — Task CRUD, filtering, status tracking
- `content_db.py` — Posts, quality scores, publishing metrics
- `admin_db.py` — Logging, financial tracking, health monitoring
- `writing_style_db.py` — Writing samples for RAG-based style matching

**AI & Quality:**
- `content_critique_loop.py` — 6-stage self-critiquing pipeline
- `writing_sample_rag.py` — Semantic similarity search for writing samples
- `unified_quality_service.py` — Quality assessment framework
- `prompt_templates.py` — Centralized prompt management

**Caching & Performance:**
- `redis_cache.py` — Redis-backed response caching
- `ai_cache.py` — Model response caching by prompt hash

**Real-time:**
- `websocket_manager.py` — WebSocket connection handling
- `event_broadcaster.py` — Publish/subscribe for real-time updates

**Integration:**
- `cms_integration.py` — Strapi CMS connectivity
- `webhook_service.py` — Inbound/outbound webhook handling
- `mcp_integration.py` — Model Context Protocol support

## API Routes

### 27 Route Modules

All endpoints available at `http://localhost:8000`:

**Core Operations:**
- `/api/tasks` — Create, read, update, delete tasks
- `/api/workflows` — Manage custom workflows
- `/api/agents` — Agent management and selection
- `/api/models` — Model availability and health
- `/api/chat` — Chat messages and conversations

**Content Management:**
- `/api/content` — Content CRUD (via CMS)
- `/api/posts` — Blog posts and articles
- `/api/media` — Image search and generation

**Analytics & Monitoring:**
- `/api/metrics` — Performance metrics and KPIs
- `/api/analytics` — Detailed analytics data
- `/api/health` — System health status

**Admin Functions:**
- `/api/settings` — Application configuration
- `/api/logs` — System logs and audit trail
- `/api/workflows/history` — Workflow execution history

**Documentation:**
- `/api/docs` — Interactive Swagger UI
- `/api/redoc` — ReDoc documentation
- `/api/openapi.json` — OpenAPI specification

### Authentication

- Most endpoints require Bearer token authentication
- Public endpoints: `/health`, `/api/chat` (limited)
- Authentication header: `Authorization: Bearer <token>`
- Token format: JWT
- Issued by: `/auth/login` or OAuth providers

## Workflow System

### Workflow Execution Architecture

**Workflow Phases:**
- Flexible phase-based system with automatic input/output mapping
- Sequential execution by phase index
- Input tracing to track data provenance
- Graceful error handling with optional retry logic

**Phase Types:**
- Research phase: Information gathering
- Generation phase: Content/code creation
- Verification phase: Quality assessment
- Publishing phase: Output finalization

**Task Execution:**
- Background polling every 5 seconds
- Status transitions: pending → in_progress → completed
- Results persisted to PostgreSQL
- Progress updates via WebSocket
- Quality assessment on completion

## Quality Assessment Framework

### 6-Point Evaluation System

The system assesses content quality using:

1. **Tone and Voice**
   - Brand consistency
   - Audience appropriateness
   - Emotional resonance

2. **Structure**
   - Heading hierarchy
   - Paragraph organization
   - Flow and readability

3. **SEO**
   - Keyword usage
   - Meta description quality
   - Internal linking

4. **Engagement**
   - Hook effectiveness
   - Call-to-action clarity
   - Reader retention

5. **Accuracy**
   - Fact checking
   - Source citations
   - Technical correctness

6. **Writing Style Consistency**
   - Alignment with user writing samples
   - Vocabulary matching
   - Sentence structure patterns

### Quality Scores

- Scale: 0-100
- Threshold for publication: 75+
- Categories: Draft (0-40), Good (40-75), Excellent (75-100)
- Scores saved per content piece for metrics tracking

## Key Features

### Multi-Agent Orchestration
- Coordinate multiple specialized agents on single task
- Route based on capability requirements
- Aggregate results from multiple agents
- Error handling and fallback logic

### Self-Critiquing Content Pipeline
- Generate initial content
- Quality assessment without rewriting
- Feedback incorporation
- Iterative refinement
- Optional human approval gates

### Cost Optimization
- Automatic provider fallback for cost savings
- Track spending per provider/model
- Compare costs across providers
- Recommend cheapest reliable option

### RAG (Retrieval-Augmented Generation)
- User writing style matching via semantic similarity
- Topic-aware content grounding
- Tone consistency maintenance
- Fact verification (when enabled)

### Real-time Feedback
- WebSocket for live progress updates
- Task execution status streaming
- Chat streaming (implementation in progress)
- Event-driven architecture

## Data Persistence

### PostgreSQL Database Schema

**Core Tables:**
- `users` — User accounts and authentication
- `tasks` — Task definitions and execution state
- `content` — Generated content and metadata
- `workflows` — Workflow definitions and history
- `agent_results` — Agent execution outputs
- `writing_samples` — User uploaded writing for RAG
- `quality_evaluations` — Quality scores and feedback
- `webhooks` — Configured webhook endpoints

**Features:**
- Full audit trail with timestamps
- Soft deletes for GDPR compliance
- Status history tracking
- Comprehensive indexing for performance

## Environment Configuration

### Configuration File: `.env.local`

Located at project root, read by all services.

**Required Variables:**
```
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs
OPENAI_API_KEY=sk-... (or other provider API keys)
```

**Optional Variables:**
```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
HUGGINGFACE_API_KEY=hf_...
OLLAMA_BASE_URL=http://localhost:11434
LLM_PROVIDER=claude (force specific provider, fallback still applies)
DEFAULT_MODEL_TEMPERATURE=0.7
SQL_DEBUG=false
LOG_LEVEL=info
SENTRY_DSN= (for error tracking)
```

## Development Environment

### Startup Commands

**Start all services:**
```bash
npm run dev
```

**Start individual services:**
```bash
npm run dev:cofounder    # Backend only
npm run dev:public       # Next.js public site
npm run dev:oversight    # React admin UI
```

### Testing

```bash
npm run test:python          # Full test suite
npm run test:python:smoke    # Fast smoke tests
npm run test                 # All workspace tests
npm run format:check         # Code formatting check
```

### Project Structure

```
src/cofounder_agent/
├── main.py                    # FastAPI app
├── routes/                    # 27 API route modules
├── services/                  # 60+ service modules
├── agents/                    # 4 specialized agents
├── models/                    # Pydantic schemas
├── tasks/                     # Task execution logic
├── middleware/                # Auth, logging, error handling
└── tests/                     # ~200+ pytest tests

web/public-site/              # Next.js application
web/oversight-hub/            # React admin UI
```

## Common Questions

**Q: What programming languages is Glad Labs built with?**
A: Python (backend with FastAPI), JavaScript and TypeScript (frontend with React 18), HTML/CSS (styling with TailwindCSS and Material-UI).

**Q: What are the main agent types?**
A: Content Agent (blog/article generation), Financial Agent (cost tracking), Market Insight Agent (trend analysis), Compliance Agent (legal/risk review), and Orchestrator Agent (coordination).

**Q: How many LLM providers are supported?**
A: 5 primary providers (Ollama, Anthropic Claude, OpenAI, Google Gemini, HuggingFace) with intelligent fallback routing.

**Q: How is the system deployed?**
A: Backend deploys to Railway (Python/FastAPI), Frontend deploys to Vercel (Next.js), Admin UI can deploy to Railway or Vercel.

**Q: What database is used?**
A: PostgreSQL for all persistent data (tasks, content, users, execution history).

**Q: Can I use local LLMs?**
A: Yes, Ollama is the default provider. Runs locally on port 11434 with zero API costs.

**Q: What is the typical chat response time?**
A: With caching: <500ms. Without cache: 1-3 seconds depending on model complexity.

## Performance Targets

- Backend response time: <500ms (with caching)
- Chat response time: <2 seconds (cached), <3 seconds (fresh)
- Database query: <100ms (indexed queries)
- API availability: 99.9% uptime
- Concurrent users: Tested with 50+ concurrent connections

## Security Features

- JWT-based authentication
- Bearer token validation on protected endpoints
- GDPR-compliant data deletion
- Audit logging for sensitive operations
- CORS properly configured
- Input validation on all endpoints
- SQL injection prevention via SQLAlchemy ORM

## Integration Capabilities

- Webhook support (inbound and outbound)
- REST API for all major operations
- WebSocket for real-time updates
- Model Context Protocol (MCP) support
- CMS integration (Strapi)
- Social media posting (in progress)

## Version & Maintenance

- **Version:** 2.0 (Production-ready)
- **Maintained by:** Glad Labs Development Team
- **Last Updated:** February 2026
- **Python:** 3.12+
- **Node.js:** 18+

---

**This knowledge base is authoritative. All system questions should reference this document for accurate information.**
