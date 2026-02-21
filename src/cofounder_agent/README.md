# 🧠 AI Co-Founder Agent

Central orchestrator for the Glad Labs AI system, coordinating specialized agents through a multi-provider model router with comprehensive task management and real-time monitoring.

> **Documentation Update (Feb 21, 2026):** Legacy documentation indexes have been moved to `archive/cleanup-feb2026/` for better organization. See [archive index](archive/cleanup-feb2026/INDEX.md) for access.

**Status:** ✅ Production Ready v3.0  
**Technology:** Python 3.12+ with FastAPI  
**Port:** 8000  
**Architecture:** Multi-Agent Orchestration + Model Router with Fallback Chain

---

## 📖 Overview

The AI Co-Founder Agent serves as the central hub of the Glad Labs AI system, providing:

- **Multi-Agent Orchestration**: Coordinates Content, Financial, Market, and Compliance agents
- **Model Router**: Intelligent LLM provider selection with automatic fallback (Ollama → Claude → GPT → Gemini)
- **Task Management**: RESTful API for task creation, tracking, and status monitoring
- **Agent Communication**: REST-based agent coordination and result aggregation
- **Memory System**: Persistent context storage with semantic search capabilities
- **Real-time Monitoring**: Performance metrics, health checks, and error tracking

---

## 🏗️ Architecture

### Core Components

```text
src/cofounder_agent/
├── __init__.py
├── main.py                    # FastAPI application, route registration
├── orchestrator_logic.py      # Core orchestration & multi-agent coordination
├── multi_agent_orchestrator.py # Agent lifecycle & execution management
├── memory_system.py           # Context storage & semantic search
├── notification_system.py     # Real-time alerts and updates
├── services/
│   ├── __init__.py
│   ├── model_router.py        # LLM provider routing with fallback chain
│   ├── performance_monitor.py # Metrics collection and tracking
│   └── database.py            # PostgreSQL/SQLite ORM (SQLAlchemy)
├── routes/
│   ├── __init__.py
│   ├── task_routes.py         # Task management endpoints
│   ├── agent_routes.py        # Agent status and command endpoints
│   ├── model_routes.py        # Model configuration endpoints
│   ├── content_routes.py      # Content generation endpoints
│   └── health_routes.py       # System health checks
├── middleware/
│   ├── __init__.py
│   ├── auth.py                # JWT authentication
│   └── audit_logging.py       # Request/response logging
├── tests/
│   ├── conftest.py            # pytest configuration & fixtures
│   ├── test_main_endpoints.py # API endpoint tests
│   ├── test_orchestrator.py   # Orchestration tests
│   └── test_e2e_fixed.py      # End-to-end tests (smoke suite)
├── start_server.py            # Server startup script
├── requirements.txt           # Python dependencies
└── README.md                  # This file
```

### Key Features

- **Async-First Architecture**: Built on FastAPI with full async/await support
- **Multi-Provider Model Routing**: Ollama (local) → Claude 3 Opus → GPT-4 → Gemini → Fallback
- **PostgreSQL-Based**: Production data storage with SQLAlchemy ORM
- **REST API First**: 50+ endpoints for tasks, agents, models, and content
- **Structured Logging**: Production-ready logging with error tracking
- **OpenAPI Documentation**: Automatic Swagger UI at `/docs`
- **Error Handling**: Comprehensive error recovery and circuit breakers

---

## 🛠️ Installation & Setup

### Prerequisites

- Python 3.12+
- PostgreSQL (production) or SQLite (development)
- Node.js 18+ (for running alongside frontend services)

### Local Development

```bash
# From project root
cd src/cofounder_agent

# Create virtual environment
python -m venv venv

# Activate venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your API keys (Ollama, OpenAI, Anthropic, Google)

# Run server with auto-reload
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### With npm (Recommended)

```bash
# From project root
npm run dev:cofounder

# Or in watch mode
npm run dev -- --workspace=src/cofounder_agent
```

---

## 🤖 Multi-Agent Orchestration

### Agent Types

The orchestrator coordinates specialized agents that handle different responsibilities:

#### Content Agent

- **Responsibility:** Content creation and management
- **Capabilities:**
  - Blog post generation with self-critique pipeline
  - Social media content creation
  - Email campaign generation
  - SEO optimization
  - Multi-format content output (markdown, HTML, JSON)

#### Financial Agent

- **Responsibility:** Business metrics and financial analysis
- **Capabilities:**
  - API usage cost tracking
  - Revenue projections
  - Budget optimization
  - ROI calculations
  - Financial reporting

#### Market Insight Agent

- **Responsibility:** Market analysis and trend detection
- **Capabilities:**
  - Competitor analysis
  - Market gap identification
  - Trend forecasting
  - Audience insights
  - Opportunity detection

#### Compliance Agent

- **Responsibility:** Legal and regulatory compliance
- **Capabilities:**
  - GDPR/CCPA compliance checking
  - Content moderation
  - Privacy policy management
  - Risk assessment
  - Legal compliance validation

### Agent Execution Model

Agents are executed in parallel when possible using `asyncio.gather()`:

```python
async def execute_request(request):
    # Decompose request into tasks
    tasks = decompose(request)

    # Execute in parallel
    results = await asyncio.gather(*[
        self.agents[task.type].execute(task)
        for task in tasks
    ])

    # Aggregate results
    return aggregate(results)
```

---

## 🧠 Memory System

### Types of Memory

**Short-term (Session):**

- Current conversation context
- Recent interactions (TTL: 1 hour)
- Contextual information for current task

**Long-term (Persistent):**

- Learned patterns and preferences
- Historical data and facts
- User preferences and settings

### Memory Operations

```bash
# Store information
POST /api/memory
{
  "agent_id": "content",
  "content": "Knowledge to store",
  "memory_type": "learning"
}

# Retrieve by semantic search
POST /api/memory/search
{
  "query": "Find related memories about...",
  "limit": 10
}

# Get stats
GET /api/memory/stats
```

---

## 🧬 Model Router & Fallback Chain

### Provider Prioritization

The model router automatically selects providers in this order:

```
1. Ollama (Local)
   ├─ Zero cost ✅
   ├─ No API rate limits ✅
   ├─ No latency ✅
   ├─ Full privacy ✅
   └─ GPU acceleration (CUDA/Metal) ✅

2. Claude 3 Opus (Anthropic)
   ├─ Best quality for writing ✅
   ├─ Superior reasoning ✅
   └─ Excellent for content generation ✅

3. GPT-4 (OpenAI)
   ├─ Consistent performance ✅
   ├─ Great for analysis ✅
   └─ Good cost/quality balance ✅

4. Gemini Pro (Google)
   ├─ Lower cost ✅
   ├─ Fast responses ✅
   └─ Good for most tasks ✅

5. Fallback Model
   └─ Ensures system availability ✅
```

### Configuration

```python
# Per-agent model configuration
MODEL_CONFIG = {
    "content": {
        "primary": "ollama:mistral",
        "fallback_chain": ["claude-opus", "gpt-4", "gemini-pro"],
        "temperature": 0.7,  # Creative writing
        "max_tokens": 3000,
    },
    "financial": {
        "primary": "ollama:llama3.2",
        "fallback_chain": ["gpt-4", "claude-opus", "gemini-pro"],
        "temperature": 0.2,  # Analytical
        "max_tokens": 2000,
    },
}
```

### Testing Model Connectivity

```bash
# Test all providers
curl -X GET http://localhost:8000/api/models/test-all

# Test specific provider
curl -X GET http://localhost:8000/api/models/test?provider=ollama

# Get current status
curl -X GET http://localhost:8000/api/models/status
```

---

## 📚 API Documentation

### Base URLs

- **Development:** `http://localhost:8000`
- **Interactive Docs:** `http://localhost:8000/docs` (Swagger UI)
- **Alternative Docs:** `http://localhost:8000/redoc` (ReDoc)
- **OpenAPI Schema:** `http://localhost:8000/openapi.json`

### Core Endpoints

#### System Health

```bash
GET /api/health
# Returns: {"status": "healthy", "timestamp": "...", "agents": {...}}

GET /api/metrics
# Returns: Performance metrics, uptime, request counts
```

#### Task Management

```bash
# Create task
POST /api/tasks
{
  "title": "Generate blog post",
  "description": "About AI trends",
  "type": "content_generation",
  "parameters": {
    "topic": "AI trends",
    "length": "2000 words"
  }
}

# Get task by ID
GET /api/tasks/{task_id}

# List all tasks
GET /api/tasks?status=in_progress&limit=20

# Update task
PUT /api/tasks/{task_id}
{
  "status": "paused"
}

# Delete task
DELETE /api/tasks/{task_id}
```

#### Agent Management

```bash
# Get all agent status
GET /api/agents/status

# Get specific agent
GET /api/agents/{agent_name}/status

# Send command to agent
POST /api/agents/{agent_name}/command
{
  "command": "generate_content",
  "parameters": {...}
}

# View agent logs
GET /api/agents/logs?agent={agent_name}&level=error&limit=50
```

#### Model Management

```bash
# List available models
GET /api/models

# Test model connection
POST /api/models/test
{
  "provider": "ollama",
  "model": "mistral"
}

# Configure model settings
PUT /api/models/{model_id}/configure
{
  "temperature": 0.7,
  "max_tokens": 2000,
  "active": true
}

# Get provider status
GET /api/models/status
```

#### Content Generation

```bash
# Generate blog post (full pipeline)
POST /api/content/generate-blog-post
{
  "topic": "AI in Business",
  "style": "professional",
  "length": "2000 words",
  "include_images": true
}

# Use specific agent
POST /api/agents/content/generate
{
  "task": "research",
  "topic": "Market trends"
}
```

---

## 🔄 Task Lifecycle

```
┌─────────────┐
│   Created   │  POST /api/tasks
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│   Pending       │  Waiting for execution
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│   In Progress   │  Agent executing
└──────┬──────────┘
       │
       ├─→ ┌─────────┐  (on error)
       │   │ Failed  │
       │   └─────────┘
       │
       ▼
┌─────────────────┐
│   Completed     │  Result stored in database
└─────────────────┘
```

---

## 🧪 Testing

### Run All Tests

```bash
# From src/cofounder_agent/
pytest tests/ -v

# Quick smoke tests (5-10 min)
pytest tests/test_e2e_fixed.py -v

# With coverage
pytest tests/ -v --cov=. --cov-report=html
```

### Test Structure

```bash
tests/
├── conftest.py                    # pytest fixtures and config
├── test_main_endpoints.py         # API endpoint tests
├── test_orchestrator.py           # Orchestration tests
├── test_e2e_fixed.py              # Smoke tests (quick validation)
└── test_e2e_comprehensive.py      # Full pipeline tests
```

### Key Test Files

- **test_main_endpoints.py**: Health, task, model endpoints (20+ tests)
- **test_orchestrator.py**: Agent coordination and execution (12+ tests)
- **test_e2e_fixed.py**: Quick smoke tests for CI/CD (8-10 tests, 5-10 min)
- **test_e2e_comprehensive.py**: Full pipeline validation (15+ tests, 20+ min)

Current test count: **50+ passing tests** ✅

---

## 🚀 Deployment

### Development

```bash
npm run dev:cofounder
# Or
python -m uvicorn main:app --reload
```

### Production (Docker)

```bash
# Build image
docker build -t glad-labs-cofounder:latest .

# Run container
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host/db \
  -e OPENAI_API_KEY=sk-... \
  glad-labs-cofounder:latest
```

### Production (Railway)

```bash
# Deploy to Railway
railway link --project <project-id>
railway deploy

# View logs
railway logs --follow

# Restart service
railway redeploy
```

### Environment Variables

```bash
# Required
OPENAI_API_KEY=sk-...  # Or use Anthropic/Google/Ollama
DATABASE_URL=postgresql://user:pass@localhost/dbname

# Optional
DEBUG=False
LOG_LEVEL=INFO
RATE_LIMIT_PER_MINUTE=100
CORS_ORIGINS=http://localhost:3000,http://localhost:3001
USE_OLLAMA=true  # Prioritize local Ollama
OLLAMA_HOST=http://localhost:11434
```

---

## 🐛 Troubleshooting

### Ollama Not Connecting

```bash
# Verify Ollama is running
curl http://localhost:11434/api/tags

# If not running:
ollama serve

# Test in system
GET http://localhost:8000/api/models/test?provider=ollama
```

### Database Connection Error

```bash
# Test PostgreSQL connection
psql $DATABASE_URL -c "SELECT 1"

# Verify connection string format
# postgresql://user:password@host:5432/dbname

# Check firewall rules allow connection
```

### API Key Authentication Failed

```bash
# Verify key is set in .env
echo $OPENAI_API_KEY

# Test with curl
curl -H "Authorization: Bearer $OPENAI_API_KEY" http://localhost:8000/api/health

# Check key validity with provider
# OpenAI: https://platform.openai.com/api-keys
# Anthropic: https://console.anthropic.com/
# Google: https://makersuite.google.com/app/apikey
```

### High Memory Usage

```bash
# Check memory stats
curl http://localhost:8000/api/memory/stats

# Monitor in real-time
while true; do curl -s http://localhost:8000/api/memory/stats | jq '.memory_usage'; sleep 5; done

# Restart service if memory leak suspected
railway redeploy
```

---

## 📊 Monitoring

### Health Check (Every 30 seconds)

```bash
# System health
curl http://localhost:8000/api/health

# Agent status
curl http://localhost:8000/api/agents/status

# Model provider status
curl http://localhost:8000/api/models/status

# Recent errors
curl http://localhost:8000/api/agents/logs?level=error&limit=10
```

### Key Metrics to Monitor

| Metric                         | Alert Threshold | Endpoint             |
| ------------------------------ | --------------- | -------------------- |
| API Response Time (p95)        | >2s             | `/api/metrics`       |
| Error Rate                     | >1%             | `/api/metrics`       |
| Memory Usage                   | >500MB          | `/api/memory/stats`  |
| Agent Execution Time           | >5 min          | `/api/tasks/{id}`    |
| Model Fallback Chain Exhausted | Any             | `/api/models/status` |

---

## 📚 Related Documentation

- **[Architecture Guide](../../docs/02-ARCHITECTURE_AND_DESIGN.md)** - System design
- **[AI Agents & Integration](../../docs/05-AI_AGENTS_AND_INTEGRATION.md)** - Agent details
- **[Development Workflow](../../docs/04-DEVELOPMENT_WORKFLOW.md)** - Testing & CI/CD
- **[Operations Guide](../../docs/06-OPERATIONS_AND_MAINTENANCE.md)** - Production support
- **[Agent System](../agents/README.md)** - Individual agent documentation
- **[Testing Guide](../../docs/reference/TESTING.md)** - Comprehensive test documentation

---

**Maintained by:** Glad Labs Development Team  
**Last Updated:** October 26, 2025  
**Status:** ✅ Production Ready | PostgreSQL Backend | Multi-Provider LLM Support
