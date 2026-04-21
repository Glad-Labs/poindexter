# 05 - AI Agents & Integration

**Last Updated:** March 10, 2026
**Version:** 0.1.0
**Status:** DEPRECATED — pre-Phase-E snapshot. Kept only for historical
reference.

> **Read this first.** The shape described below (6 named agents,
> files under `src/cofounder_agent/agents/content_agent/`, Financial /
> Market / Compliance specialized agents) does not match the code
> shipped today. The Phase E refactor replaced the agent model with a
> 12-stage `Stage` plugin chain. The current pipeline is documented in
> [content-pipeline.md](./content-pipeline.md). Footer links in this
> file to `System-Design.md`, `../01-Getting-Started/`, and
> `../00-README.md` point at files that have never existed in this
> repo.

---

## 🏗️ Agent Architecture

### Self-Critiquing Pipeline System

Poindexter implements a sophisticated self-critiquing content generation pipeline where agents evaluate each other's work and provide feedback.

**Location:** `src/cofounder_agent/agents/content_agent/`

```text
┌─────────────────────────────────────────────┐
│     API Client / OpenClaw                   │
└──────────────────┬──────────────────────────┘
                   │ POST /api/tasks
┌──────────────────▼──────────────────────────┐
│  Poindexter Worker (FastAPI)                 │
│  - Task Queueing                            │
│  - TaskExecutor (Polling Loop)              │
│  - UnifiedOrchestrator                      │
└──────────────────┬──────────────────────────┘
                   │
        ┌──────────┴──────────────────────────┐
        │                                     │
    ┌───▼──────────────────────┐     ┌───────▼──────┐
    │  Content Agent Pipeline  │     │  Other Agents│
    │ (Self-Critiquing Loop)   │     │ (Financial,  │
    │                          │     │  Market, etc)│
    │ 1. Research Agent        │     └──────────────┘
    │ 2. Creative Agent        │
    │ 3. QA Agent (critique)   │
    │ 4. Creative Agent refined│
    │ 5. Image Agent           │
    │ 6. Publishing Agent      │
    │                          │
    └───┬──────────────────────┘
        │
        └──────────────────────────┐
                                   │
        ┌──────────────────────────▼──────────┐
        │ Model Router (Ollama-only pipeline) │
        │ → Ollama local inference            │
        └─────────────────────────────────────┘
```

---

## 🔀 Model Router & LLM Access

Poindexter's default pipeline runs Ollama-only: all inference on local hardware, zero paid API cost. Anthropic / OpenAI / Google Gemini were removed from the pipeline to honor the "no paid APIs" rule (session 55). `services/model_router.py` still exists as the tier-selection logic; the `ModelProvider` enum currently enumerates `OLLAMA` and `HUGGINGFACE` (as emergency fallback).

**Location:** `src/cofounder_agent/services/model_router.py`

A future refactor (GitHub [#64](https://github.com/Glad-Labs/poindexter/issues/64) Phase J) will extract inference into an `LLMProvider` plugin family. The default shipping providers will remain OSS-only — Ollama, llama.cpp server, vllm, SGLang, HuggingFace TGI, LM Studio, LocalAI, LiteLLM gateway — all reachable via a generic `OpenAICompatProvider` by swapping one `app_settings` row. Community plugins can wrap paid providers (Anthropic, OpenAI, Groq, OpenRouter) and distribute via pypi; the core install stays free.

### Cost-Tier Execution Logic

Instead of hardcoding model names, the system uses **Cost Tiers** for intelligent execution:

| Tier         | Current Models                                                 | Usage Case                      |
| ------------ | -------------------------------------------------------------- | ------------------------------- |
| **Free**     | Ollama: qwen3:8b, phi4:14b, phi3                               | SEO, image decisions, summaries |
| **Budget**   | Ollama: gemma3:27b                                             | QA reviews, fallback critic     |
| **Standard** | Ollama: glm-4.7 (custom build)                                 | Writing, content generation     |
| **Premium**  | _(no active premium tier; cloud models retired from pipeline)_ | N/A                             |

---

## 🛠️ Model Context Protocol (MCP)

**Location:** `mcp-server/`

The MCP provides a standardized interface for agents to interact with external tools and resources:

**Available Tools:**

- **Search:** Serper integration for web search
- **Data Retrieval:** PostgreSQL database queries
- **Media:** Pexels for image selection
- **Context:** Dynamic memory and RAG-based context injection

### Implementing a New MCP Tool

To add a custom tool:

1. Define tool logic in `src/mcp_server/`
2. Register the tool in `MCPContentOrchestrator`
3. Update `unified_orchestrator` to include the tool in agent prompts
4. Test via `POST /api/agents/test-mcp-tool`

---

## 📊 Monitoring & Cost Tracking

Usage is tracked per-task and per-user using the `UsageTracker` service.

- **Metrics Service:** `src/cofounder_agent/services/usage_tracker.py`
- **Dashboard:** Real-time costs via **Grafana** monitoring
- **Reports:** Cost breakdown by model, agent, task type

---

### Agent Discovery & Execution

Agents are discovered dynamically via the `service_container` in `src/cofounder_agent/services/container.py`.

- **Entry Point:** `UnifiedOrchestrator.process_request()`
- **Routing:** Handled via `RequestType` detection and intent parsing.

---

## 👥 Specialized Agents (Content Creation Focus)

Poindexter includes both general-purpose agents (Financial, Market, Compliance) and a specialized self-critiquing content generation pipeline:

### Content Agent System (Self-Critiquing Pipeline)

**Location:** `src/agents/content_agent/`

**6-Agent Self-Critiquing Architecture:**

#### 1. Research Agent

- Gathers background information on topics
- Identifies key points and sources
- Provides factual foundation for content

#### 2. Creative Agent

- Generates initial draft content based on research
- Creates outlines, body text, and conclusions
- Applies brand voice and style guidelines

#### 3. QA Agent (Quality Assurance & Critique)

- Evaluates content quality against criteria
- Provides specific feedback for improvement
- Suggests edits without rewriting
- Identifies gaps or inconsistencies

#### 4. Creative Agent (Refinement Loop)

- Receives feedback from QA Agent
- Incorporates suggestions into refined content
- Maintains voice while improving quality
- Returns to QA Agent if needed for iteration

#### 5. Image Agent

- Selects or generates relevant visual assets
- Optimizes images for web
- Provides alt text and metadata
- Ensures visual consistency

#### 6. Publishing Agent

- Formats content for Database/CMS
- Adds SEO metadata (title, description, keywords)
- Creates structured frontmatter
- Handles markdown/rich text conversion

**Self-Critiquing Pipeline Example:**

```python
# Execution flow
async def generate_blog_post(topic: str):
    # 1. Research
    research_data = await research_agent.execute({
        "topic": topic
    })

    # 2. Create initial draft
    draft = await creative_agent.execute({
        "topic": topic,
        "research": research_data,
        "style": "professional",
        "length": "2000 words"
    })

    # 3. QA critique
    feedback = await qa_agent.execute({
        "content": draft,
        "criteria": ["clarity", "accuracy", "engagement", "length"]
    })

    # 4. Refinement (with feedback loop)
    if feedback.needs_improvement:
        refined = await creative_agent.execute({
            "topic": topic,
            "previous_draft": draft,
            "feedback": feedback,
            "revise": True
        })
        draft = refined

    # 5. Image selection
    images = await image_agent.execute({
        "content": draft,
        "topic": topic,
        "count": 3
    })

    # 6. Format for publishing
    published = await publishing_agent.execute({
        "content": draft,
        "images": images,
        "metadata": {
            "title": topic,
            "seo_keywords": ["key", "words"]
        },
        "target": "database"
    })

    return published
```

**Usage Patterns:**

- **End-to-end generation:** POST `/api/tasks` with `task_type: "blog_post"` → Full 6-agent pipeline with self-critique
- **Individual agents:** POST `/api/agents/{research|create|qa|image|publish}` → Use specific agent
- **Custom workflows:** Combine agents in any order for flexible content workflows

### Other Specialized Agents

#### 1. Financial Agent

**Responsibility:** Business metrics and financial management

**Capabilities:**

- Cost tracking (API usage, cloud services)
- Revenue projections
- Budget optimization
- ROI calculations
- Financial reporting

#### 2. Market Insight Agent

**Responsibility:** Market analysis and trend detection

**Capabilities:**

- Competitor analysis
- Trend forecasting
- Audience insights
- Market gap identification
- Opportunity detection

#### 3. Compliance Agent

**Responsibility:** Legal and regulatory compliance

**Capabilities:**

- GDPR/CCPA compliance checking
- Content moderation
- Privacy policy management
- Risk assessment
- Legal compliance validation

---

## 🔄 Multi-Agent Orchestration

### Task Distribution

```python
# src/cofounder_agent/multi_agent_orchestrator.py
class AgentOrchestrator:
    def __init__(self):
        self.agents = {
            "content": ContentAgent(),
            "financial": FinancialAgent(),
            "market": MarketInsightAgent(),
            "compliance": ComplianceAgent(),
        }

    async def execute(self, request: Request) -> Response:
        # Route to appropriate agent(s)
        tasks = self.decompose_request(request)

        # Execute in parallel
        results = await asyncio.gather(*[
            self.agents[task.agent_type].execute(task)
            for task in tasks
        ])

        # Aggregate results
        return self.aggregate_results(results)
```

### Parallel Execution

```python
async def execute_parallel(self, tasks: List[Task]) -> List[Result]:
    """Execute multiple tasks concurrently"""
    return await asyncio.gather(*[
        self.execute_task(task) for task in tasks
    ])
```

### Error Handling

```python
async def execute_with_fallback(self, task: Task) -> Result:
    """Execute with automatic fallback"""
    try:
        return await self.agents[task.primary_agent].execute(task)
    except Exception as e:
        logger.warning(f"Primary agent failed: {e}")
        # Fallback to secondary agent
        return await self.agents[task.fallback_agent].execute(task)
```

---

## 🧠 Memory System

### Memory Types

```python
# Short-term: Current conversation context
SHORT_TERM_MEMORY = {
    "messages": [...],
    "context": {...},
    "ttl": 3600  # 1 hour
}

# Long-term: Persistent knowledge
LONG_TERM_MEMORY = {
    "facts": [...],
    "learned_patterns": [...],
    "user_preferences": {...}
}
```

### Memory Operations

```python
class MemorySystem:
    async def store(self, key: str, value: Any) -> None:
        """Store information"""
        await self.db.set(key, value)

    async def retrieve(self, key: str) -> Any:
        """Retrieve information"""
        return await self.db.get(key)

    async def semantic_search(self, query: str) -> List[Result]:
        """Find related memories"""
        embedding = await self.embed(query)
        return await self.db.vector_search(embedding)

    async def forget(self, key: str) -> None:
        """Remove old memories"""
        await self.db.delete(key)
```

### Usage Example

```python
async def remember_user_preference(self, user_id: str, preference: str):
    await self.memory.store(f"user:{user_id}:preference", preference)

async def get_context_for_user(self, user_id: str) -> str:
    preference = await self.memory.retrieve(f"user:{user_id}:preference")
    return f"User prefers: {preference}"
```

---

## 🔌 MCP Integration

### Model Context Protocol

Poindexter uses MCP (Model Context Protocol) for:

- **Tool calling:** Agents can call external tools
- **Resource access:** Access to databases, APIs, files
- **Standard interface:** Consistent agent communication

### MCP Server Setup

```python
# mcp-server/base_server.py
from mcp.server import Server

class PoinexterMCPServer(Server):
    def __init__(self):
        super().__init__("Poindexter")
        self.register_tool("create_content", self.create_content)
        self.register_tool("query_database", self.query_database)
        self.register_tool("call_api", self.call_api)

    async def create_content(self, args: Dict) -> str:
        """Tool: Generate content"""
        pass

    async def query_database(self, args: Dict) -> str:
        """Tool: Query database"""
        pass

    async def call_api(self, args: Dict) -> str:
        """Tool: Call external API"""
        pass
```

### Agent Tool Usage

```python
# Agent calls tools via MCP
async def generate_post(self, topic: str) -> str:
    # Call MCP tool
    outline = await self.call_tool("create_content", {
        "type": "outline",
        "topic": topic
    })

    post = await self.call_tool("create_content", {
        "type": "full_article",
        "outline": outline
    })

    return post
```

---

## 📊 Agent Configuration

### Model Fallback Chain (Ollama-only)

The pipeline runs Ollama-only. There is no cloud fallback — cloud LLM providers (Anthropic, OpenAI, Google Gemini) were removed in session 55 to honor the "no paid APIs" rule.

```text
1. Ollama (primary — local inference on RTX 5090 32GB VRAM)
   ↓ [if primary model errors or returns empty]
2. pipeline_fallback_model (default: gemma3:27b, also on Ollama)
   ↓ [if Ollama itself is down]
3. HuggingFace transformers (emergency fallback, on-CPU)
```

The `cloud_api_mode` app_setting exists (`disabled` / `emergency_only` / `fallback` / `always`) and is set to `disabled` by default. Customers forking the repo can re-enable cloud providers by installing a community plugin (`pip install poindexter-llm-anthropic` etc., future Phase J) and flipping the setting.

**Benefits of the Ollama-only approach:**

- ✅ 100% free local inference
- ✅ No API rate limits
- ✅ No network latency
- ✅ Full privacy (no data leaves your machine)
- ✅ GPU acceleration (CUDA/Metal auto-detected)
- ✅ Perfect for development and high-volume deployments

### Model Selection per Agent

Agent-to-model assignment is **DB-configurable, Ollama-only**. The mapping lives in `app_settings` under keys like `pipeline_writer_model`, `pipeline_critic_model`, `model_role_writer`, `model_role_critic`, `model_role_factchecker`, etc. Changing an agent's model is a `UPDATE app_settings SET value = 'ollama/qwen3:8b' WHERE key = 'model_role_seo'` — no code change, no redeploy.

Current defaults (as of April 2026):

| Role                       | app_settings key                                        | Default model                                                                          |
| -------------------------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Writer (long-form content) | `pipeline_writer_model` / `model_role_writer`           | `ollama/gemma3:27b` (free-tier seed); `ollama/glm-4.7-5090:latest` (Matt's production) |
| Critic (QA review)         | `pipeline_critic_model` / `model_role_critic`           | `ollama/gemma3:27b`                                                                    |
| Fact-checker               | `model_role_factchecker`                                | `ollama/gemma3:27b`                                                                    |
| SEO / metadata             | `pipeline_seo_model` / `model_role_seo`                 | `ollama/qwen3:8b`                                                                      |
| Image prompt crafter       | `model_role_image_prompt`                               | `ollama/qwen3:8b`                                                                      |
| Summarizer / short-form    | `model_role_summarizer`                                 | `ollama/qwen3:8b` (also `phi3:latest` in some roles)                                   |
| Refinement / research      | `pipeline_refinement_model` / `pipeline_research_model` | `ollama/gemma3:27b` (free seed); `ollama/glm-4.7-5090:latest` (production)             |
| Fallback                   | `pipeline_fallback_model`                               | `ollama/gemma3:27b`                                                                    |

Temperatures are also DB-configurable (`content_temperature = 0.7`, `qa_temperature = 0.3`, defaults).

No cloud model references remain in the pipeline. The free-tier seed (`brain/seed_app_settings.json`) ships only Ollama models that a fresh install can actually pull; Matt's production DB overlays those with his custom 5090-tuned `glm-4.7-5090:latest` build via the premium pack.

### Agent Capabilities Matrix

| Agent      | Blog Posts | Research | QA/Critique | Publishing | Image Selection |
| ---------- | ---------- | -------- | ----------- | ---------- | --------------- |
| Research   | ⚠️         | ✅       | ❌          | ❌         | ❌              |
| Creative   | ✅         | ❌       | ❌          | ❌         | ❌              |
| QA         | ❌         | ❌       | ✅          | ❌         | ❌              |
| Image      | ❌         | ❌       | ❌          | ❌         | ✅              |
| Publishing | ❌         | ❌       | ❌          | ✅         | ❌              |
| Financial  | ❌         | ❌       | ❌          | ❌         | ❌              |
| Market     | ⚠️         | ✅       | ⚠️          | ❌         | ❌              |
| Compliance | ❌         | ✅       | ⚠️          | ⚠️         | ❌              |

---

## 🚀 Deploying Agents

### Local Testing

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

### Production Deployment

```bash
# Local docker-compose (the supported deployment for now)
docker compose -f docker-compose.local.yml build worker
docker compose -f docker-compose.local.yml up -d worker
```

The worker image has no source bind-mount: code changes require a
rebuild before they reach the running container. A dev-mode override
that bind-mounts `src/cofounder_agent` is tracked as an open
ergonomics issue; see the project's issue tracker.

### Agent Monitoring

```bash
# Check worker health (the top-level service health endpoint)
curl http://localhost:8002/api/health

# Stream container logs
docker logs -f poindexter-worker

# Monitor memory usage
curl http://localhost:8002/api/agents/memory/stats
```

---

## 🔗 Related Documentation

- **[Architecture](./System-Design.md)** - System overview
- **[Setup Guide](../01-Getting-Started/)** - Getting started
- **[Development](../04-Development/Development-Workflow.md)** - Development patterns
- **[Deployment](../05-Operations/Operations-Maintenance.md)** - Production setup

---

**[← Back to Documentation Hub](../00-README.md)**

[Setup](../01-Getting-Started/) • [Architecture](./System-Design.md) • [Development](../04-Development/Development-Workflow.md) • [Operations](../05-Operations/Operations-Maintenance.md)
