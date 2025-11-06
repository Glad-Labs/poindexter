# 05 - AI Agents & Integration

**Last Updated:** November 5, 2025  
**Version:** 1.1  
**Status:** ✅ Production Ready

---

## 🎯 Quick Links

- **[Agent Architecture](#agent-architecture)** - How agents work
- **[Specialized Agents](#specialized-agents)** - Agent capabilities
- **[Multi-Agent Orchestration](#multi-agent-orchestration)** - Agent coordination
- **[Memory System](#memory-system)** - Context and learning
- **[MCP Integration](#mcp-integration)** - Model Context Protocol

---

## 🏗️ Agent Architecture

### Self-Critiquing Pipeline System

GLAD Labs implements a sophisticated self-critiquing content generation pipeline where agents evaluate each other's work and provide feedback for continuous improvement. This ensures high-quality, publication-ready content.

```text
┌─────────────────────────────────────────────┐
│         Oversight Hub (UI)                  │
└──────────────────┬──────────────────────────┘
                   │ REST API
┌──────────────────▼──────────────────────────┐
│  Co-Founder Orchestrator (FastAPI)          │
│  - Request routing                          │
│  - Agent coordination                       │
│  - Task distribution                        │
│  - Result aggregation                       │
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
        │ Model Router (Multi-Provider)       │
        │ Ollama → Claude → GPT → Gemini      │
        │ (Prioritized fallback chain)        │
        └─────────────────────────────────────┘
```

### Agent Base Class

```python
# src/agents/base_agent.py
from abc import ABC, abstractmethod

class BaseAgent(ABC):
    def __init__(self, name: str, model: str = None):
        self.name = name
        self.model = model  # Can be overridden per agent
        self.memory = MemorySystem()
        self.llm_client = LLMClient()  # Handles model routing

    @abstractmethod
    async def execute(self, task: Task) -> Result:
        """Execute agent task"""
        pass

    async def think(self, prompt: str) -> str:
        """Query LLM with model fallback (Ollama first)"""
        return await self.llm_client.query(prompt, self.model)

    async def critique(self, content: str, criteria: str) -> str:
        """Provide constructive feedback"""
        pass

    async def remember(self, context: str) -> None:
        """Store in memory for future use"""
        pass
```

---

## 👥 Specialized Agents (Content Creation Focus)

GLAD Labs includes both general-purpose agents (Financial, Market, Compliance) and a specialized self-critiquing content generation pipeline:

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

- Formats content for Strapi CMS
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
        "target": "strapi-cms"
    })

    return published
```

**Usage Patterns:**

- **End-to-end generation:** POST `/api/content/generate-blog-post` → Full 6-agent pipeline with self-critique
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

GLAD Labs uses MCP (Model Context Protocol) for:

- **Tool calling:** Agents can call external tools
- **Resource access:** Access to databases, APIs, files
- **Standard interface:** Consistent agent communication

### MCP Server Setup

```python
# src/mcp/base_server.py
from mcp.server import Server

class GLADLabsMCPServer(Server):
    def __init__(self):
        super().__init__("GLAD Labs")
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

### Model Fallback Chain (Prioritizes Ollama)

Agents automatically route requests through this prioritized fallback chain:

```text
1. Ollama (Local, Zero-Cost)
   ↓ [If unavailable or model not found]
2. Claude 3 Opus (Anthropic, Best Quality)
   ↓ [If quota exceeded or error]
3. GPT-4 (OpenAI, Fast & Capable)
   ↓ [If rate limited or expensive]
4. Gemini Pro (Google, Lower Cost)
   ↓ [If all else fails]
5. Fallback Model (Gemini Flash or equivalent)
```

**Benefits of Ollama-First Approach:**

- ✅ 100% free local inference
- ✅ No API rate limits
- ✅ No network latency
- ✅ Full privacy (no data leaves your machine)
- ✅ GPU acceleration (CUDA/Metal auto-detected)
- ✅ Perfect for development and high-volume deployments

### Model Selection per Agent

```python
# Configure which AI model each agent uses
# Agents inherit from LLMClient which handles fallback chain
AGENT_CONFIG = {
    "research": {
        "preferred_model": "gpt-4",  # Use search capability
        "fallback_chain": ["claude-opus", "gpt-4", "gemini-pro", "ollama"],
        "temperature": 0.3,  # Factual, precise
        "max_tokens": 2000,
    },
    "creative": {
        "preferred_model": "claude-opus",  # Best for writing
        "fallback_chain": ["gpt-4", "gemini-pro", "ollama"],
        "temperature": 0.7,  # Creative, varied
        "max_tokens": 3000,
    },
    "qa": {
        "preferred_model": "gpt-4",  # Good at evaluation
        "fallback_chain": ["claude-opus", "gemini-pro", "ollama"],
        "temperature": 0.2,  # Analytical, precise
        "max_tokens": 1000,
    },
    "image": {
        "preferred_model": "gpt-4-vision",  # Image understanding
        "fallback_chain": ["claude-opus", "ollama"],
        "temperature": 0.5,
        "max_tokens": 500,
    },
    "publishing": {
        "preferred_model": "gpt-3.5",  # Fast formatting
        "fallback_chain": ["gemini-pro", "ollama"],
        "temperature": 0.1,  # Precise formatting
        "max_tokens": 500,
    },
}
```

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
# Railway deployment
railway service add cofounder-agent
railway deploy

# Or Docker
docker build -t glad-labs-agents:latest .
docker run -p 8000:8000 glad-labs-agents:latest
```

### Agent Monitoring

```bash
# Check agent status
curl http://localhost:8000/api/agents/status

# View agent logs
railway logs --service=cofounder-agent

# Monitor memory usage
curl http://localhost:8000/api/agents/memory/stats
```

---

## 🔗 Related Documentation

- **[Architecture](./02-ARCHITECTURE_AND_DESIGN.md)** - System overview
- **[Setup Guide](./01-SETUP_AND_OVERVIEW.md)** - Getting started
- **[Development](./04-DEVELOPMENT_WORKFLOW.md)** - Development patterns
- **[Deployment](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Production setup

---

**[← Back to Documentation Hub](./00-README.md)**

[Setup](./01-SETUP_AND_OVERVIEW.md) • [Architecture](./02-ARCHITECTURE_AND_DESIGN.md) • [Development](./04-DEVELOPMENT_WORKFLOW.md) • [Operations](./06-OPERATIONS_AND_MAINTENANCE.md)
