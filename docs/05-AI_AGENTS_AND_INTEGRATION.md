# 05 - AI Agents & Integration

**Last Updated:** October 22, 2025  
**Version:** 1.0  
**Status:** ✅ Production Ready

---

## 🎯 Quick Links

- **[Agent Architecture](#-agent-architecture)** - How agents work
- **[Specialized Agents](#-specialized-agents)** - Agent capabilities
- **[Multi-Agent Orchestration](#-multi-agent-orchestration)** - Agent coordination
- **[Memory System](#-memory-system)** - Context and learning
- **[MCP Integration](#-mcp-integration)** - Model Context Protocol

---

## 🏗️ Agent Architecture

### Multi-Agent System

```
┌─────────────────────────────────────────────┐
│         Oversight Hub (UI)                  │
└──────────────────┬──────────────────────────┘
                   │ REST API
┌──────────────────▼──────────────────────────┐
│  AI Co-Founder Orchestrator (FastAPI)       │
│  - Request routing                          │
│  - Agent coordination                       │
│  - Task distribution                        │
│  - Result aggregation                       │
└──────────────────┬──────────────────────────┘
                   │
        ┌──────────┼──────────┐
        │          │          │
    ┌───▼──┐  ┌───▼──┐  ┌───▼──┐
    │Content│  │Finance│  │Market │
    │Agent  │  │Agent  │  │Agent  │ etc.
    └───────┘  └───────┘  └───────┘
        │          │          │
        └──────────┼──────────┘
                   │
        ┌──────────▼──────────┐
        │ Model Router        │
        │ (Ollama/OpenAI/etc) │
        └─────────────────────┘
```

### Agent Base Class

```python
# src/agents/base_agent.py
from abc import ABC, abstractmethod

class BaseAgent(ABC):
    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model
        self.memory = MemorySystem()

    @abstractmethod
    async def execute(self, task: Task) -> Result:
        """Execute agent task"""
        pass

    async def think(self, prompt: str) -> str:
        """Query LLM with context"""
        pass

    async def remember(self, context: str) -> None:
        """Store in memory for future use"""
        pass
```

---

## 👥 Specialized Agents

### 1. Content Agent

**Responsibility:** Content creation and curation

**Capabilities:**

- Blog post generation
- Social media content
- Email campaigns
- SEO optimization
- Content calendar planning

**Example:**

```python
class ContentAgent(BaseAgent):
    async def execute(self, task: Task) -> Result:
        if task.type == "generate_blog":
            outline = await self.think(f"Create outline for: {task.topic}")
            content = await self.think(f"Write detailed article: {outline}")
            return Result(content=content)
```

### 2. Financial Agent

**Responsibility:** Business metrics and financial management

**Capabilities:**

- Cost tracking (API usage, cloud services)
- Revenue projections
- Budget optimization
- ROI calculations
- Financial reporting

**Example:**

```python
class FinancialAgent(BaseAgent):
    async def execute(self, task: Task) -> Result:
        if task.type == "calculate_roi":
            costs = await self.query_database("SELECT * FROM costs")
            revenue = await self.query_database("SELECT * FROM revenue")
            roi = self.calculate_roi(revenue, costs)
            return Result(roi=roi)
```

### 3. Market Insight Agent

**Responsibility:** Market analysis and trend detection

**Capabilities:**

- Competitor analysis
- Trend forecasting
- Audience insights
- Market gap identification
- Opportunity detection

**Example:**

```python
class MarketInsightAgent(BaseAgent):
    async def execute(self, task: Task) -> Result:
        if task.type == "analyze_trends":
            trends = await self.think("Analyze current market trends in AI")
            opportunities = await self.identify_opportunities(trends)
            return Result(trends=trends, opportunities=opportunities)
```

### 4. Compliance Agent

**Responsibility:** Legal and regulatory compliance

**Capabilities:**

- GDPR/CCPA compliance checking
- Content moderation
- Privacy policy management
- Risk assessment
- Legal compliance validation

**Example:**

```python
class ComplianceAgent(BaseAgent):
    async def execute(self, task: Task) -> Result:
        if task.type == "check_gdpr":
            content = task.data
            violations = await self.think(f"Check for GDPR violations: {content}")
            return Result(violations=violations)
```

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

### Model Selection

```python
# Configure which AI model each agent uses
AGENT_CONFIG = {
    "content": {
        "primary_model": "gpt-4",
        "fallback_model": "claude-3-sonnet",
        "temperature": 0.7,
    },
    "financial": {
        "primary_model": "gpt-4",
        "fallback_model": "gemini-pro",
        "temperature": 0.2,  # More deterministic
    },
    "market": {
        "primary_model": "claude-3-opus",
        "fallback_model": "gpt-4",
        "temperature": 0.6,
    },
}
```

### Agent Capabilities Matrix

| Agent      | Blog Posts | Reports | Compliance | Trend Analysis | Cost Tracking |
| ---------- | ---------- | ------- | ---------- | -------------- | ------------- |
| Content    | ✅         | ✅      | ⚠️         | ⚠️             | ❌            |
| Financial  | ❌         | ✅      | ❌         | ❌             | ✅            |
| Market     | ⚠️         | ⚠️      | ❌         | ✅             | ❌            |
| Compliance | ❌         | ⚠️      | ✅         | ❌             | ❌            |

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

<div align="center">

**[← Back to Documentation Hub](./00-README.md)**

[Setup](./01-SETUP_AND_OVERVIEW.md) • [Architecture](./02-ARCHITECTURE_AND_DESIGN.md) • [Development](./04-DEVELOPMENT_WORKFLOW.md) • [Operations](./06-OPERATIONS_AND_MAINTENANCE.md)

</div>
