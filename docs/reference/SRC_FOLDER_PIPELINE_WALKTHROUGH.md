# 🏗️ Glad Labs src/ Folder Structure & Pipeline Walkthrough

**Comprehensive Guide to How Each Component Works Together**

---

## 📍 Overview: The Complete Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                      USER REQUEST (REST API)                        │
│              Oversight Hub (React) → POST http://localhost:8000     │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────────────┐
        │  src/cofounder_agent/main.py                   │
        │  FastAPI Application - Central Hub             │
        │  - Route registration                          │
        │  - CORS middleware                             │
        │  - Database initialization                     │
        │  - Request handling                            │
        └────────────┬───────────────────────────────────┘
                     │
     ┌───────────────┼───────────────────────────────┐
     │               │                               │
     ▼               ▼                               ▼
 ┌─────────┐  ┌──────────────┐  ┌────────────────────────┐
 │ ROUTES  │  │ ORCHESTRATOR │  │ SERVICES               │
 │ (step 2)│  │ (step 3)     │  │ (step 4)               │
 └─────────┘  └──────────────┘  └────────────────────────┘
     │               │                       │
     │               │         ┌─────────────┼────────────┐
     │               │         │             │            │
     ▼               ▼         ▼             ▼            ▼
  Parse       Route to    AGENTS        DATABASE      MODEL
  Request     Agents      EXECUTE       PERSISTENCE   ROUTER
                          (Step 5)
                             │
                             ▼
                  ┌──────────────────────────┐
                  │  Multi-Agent System      │
                  │  - Content Agent         │
                  │  - Financial Agent       │
                  │  - Market Agent          │
                  │  - Compliance Agent      │
                  └──────────────────────────┘
                             │
                             ▼
                  ┌──────────────────────────┐
                  │  Model Router Selection  │
                  │  - Ollama (local)        │
                  │  - Claude (Anthropic)    │
                  │  - GPT-4 (OpenAI)        │
                  │  - Gemini (Google)       │
                  └──────────────────────────┘
                             │
                             ▼
                  ┌──────────────────────────┐
                  │  Generate Response       │
                  │  Store in Database       │
                  │  Return to Frontend      │
                  └──────────────────────────┘
```

---

## 🗂️ STEP-BY-STEP: How src/ Components Work

### **STEP 1: Application Entry Point**

**File:** `src/cofounder_agent/main.py`

```python
# What it does:
# - FastAPI app initialization
# - Route registration
# - Database connection setup
# - Middleware configuration
# - Lifespan management (startup/shutdown)

from fastapi import FastAPI
from routes.content_routes import content_router
from routes.models import models_router
from routes.agents_routes import router as agents_router
# ... import all other routers

app = FastAPI(title="Glad Labs AI Co-Founder", version="1.0.0")

# Register all routes
app.include_router(content_router, prefix="/api")
app.include_router(models_router, prefix="/api/models")
app.include_router(agents_router, prefix="/api/agents")
# ... more routers
```

**What Happens:**

1. FastAPI app starts at `http://localhost:8000`
2. All routes are registered and ready to handle requests
3. Database connection pool initialized
4. Services initialized (model router, task store, etc.)

**Entry Points:**

```
GET  /api/health                    # Check system status
POST /api/tasks                     # Create a task
GET  /api/agents/status             # Check agent status
```

---

### **STEP 2: Routes Layer - Request Handling**

**Location:** `src/cofounder_agent/routes/`

**What These Do:**

- Accept REST API requests from frontend
- Parse and validate request data
- Route to appropriate orchestrator or service
- Return responses to client

**Key Route Files:**

```
routes/
├── content_routes.py          # Content generation endpoints
│   └── POST /api/generate-blog-post
│   └── POST /api/generate-content
│
├── task_routes.py             # Task management
│   └── POST /api/tasks
│   └── GET /api/tasks/{id}
│   └── GET /api/tasks
│
├── models.py                  # Model configuration
│   └── GET /api/models
│   └── POST /api/models/test
│   └── PUT /api/models/configure
│
├── agents_routes.py           # Agent status & commands
│   └── GET /api/agents/status
│   └── POST /api/agents/{name}/command
│
├── auth_routes.py             # Authentication
│   └── POST /api/auth/login
│   └── POST /api/auth/logout
│
├── settings_routes.py         # Configuration
│   └── GET /api/settings
│   └── PUT /api/settings
│
└── chat_routes.py             # Chat interface
    └── POST /api/chat/message
```

**Example Flow (Content Generation):**

```python
# User sends request from Oversight Hub:
POST /api/generate-blog-post
{
  "topic": "AI in Business",
  "style": "professional",
  "length": 2000
}

# Route handler receives it:
@app.post("/api/generate-blog-post")
async def generate_blog_post(request: BlogPostRequest):
    # Validate request
    # Pass to orchestrator
    # Return task ID to user
    return {"task_id": "xyz123", "status": "pending"}
```

---

### **STEP 3: Orchestrator - Request Routing & Coordination**

**File:** `src/cofounder_agent/multi_agent_orchestrator.py`

**What It Does:**

- Receives requests from routes
- Determines which agents are needed
- Distributes work to appropriate agents
- Coordinates parallel execution via asyncio
- Aggregates results
- Handles errors and fallbacks

**Architecture:**

```python
class MultiAgentOrchestrator:
    """Coordinates multiple specialized agents"""

    def __init__(self):
        self.agents = {
            "content": ContentAgent(),
            "financial": FinancialAgent(),
            "market": MarketInsightAgent(),
            "compliance": ComplianceAgent()
        }

    async def execute_task(self, task):
        """Route task to appropriate agent(s)"""

        if task.type == "content_generation":
            # Route to content agent
            result = await self.agents["content"].execute(task)

        elif task.type == "financial_analysis":
            # Route to financial agent
            result = await self.agents["financial"].execute(task)

        return result
```

**Key Responsibility:**

- **Task Decomposition:** Break complex tasks into sub-tasks
- **Agent Selection:** Choose best agent for each sub-task
- **Parallel Execution:** Run agents concurrently via asyncio
- **Result Aggregation:** Combine sub-task results
- **Error Handling:** Fallback if agent fails

---

### **STEP 4: Agents - Specialized Execution**

**Location:** `src/agents/`

**The Agent System:**

Each agent is a specialized worker that inherits from `BaseAgent`:

```
agents/
├── base_agent.py              # Base class all agents inherit from
│   ├── Tool access (MCP)
│   ├── Memory management
│   ├── Model selection
│   ├── Error handling
│   └── Cost tracking
│
├── content_agent/             # Content generation pipeline
│   ├── orchestrator.py        # 6-agent self-critiquing pipeline
│   ├── agents/
│   │   ├── research_agent.py  # 1. Research
│   │   ├── creative_agent.py  # 2. Create draft
│   │   ├── qa_agent.py        # 3. Evaluate & critique
│   │   ├── image_agent.py     # 4. Select images
│   │   └── publishing_agent.py# 5. Format for CMS
│   └── utils/
│       ├── tools.py           # CrewAI tools
│       └── prompts.py         # Agent prompts
│
├── financial_agent/           # Financial analysis
│   ├── financial_agent.py     # Main agent
│   ├── cost_tracking.py       # Track API costs
│   └── tests/
│
├── market_insight_agent/      # Market analysis
│   ├── market_insight_agent.py
│   └── test_market_insight_agent.py
│
├── compliance_agent/          # Legal/compliance
│   └── agent.py
│
└── social_media_agent/        # Social media
    └── social_media_agent.py
```

**How Content Agent Works (Most Complex Example):**

```
┌─────────────────────────────────────────────────────────┐
│   ContentAgentOrchestrator.execute("blog post")         │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
     ┌───────────────────────────────────┐
     │  Phase 1: Research Agent           │
     │  - Gather background info          │
     │  - Identify key points             │
     │  - Return research data            │
     └───────────┬───────────────────────┘
                 │
                 ▼
     ┌───────────────────────────────────┐
     │  Phase 2: Creative Agent (Draft)   │
     │  - Use research data               │
     │  - Write initial draft             │
     │  - Apply brand voice               │
     └───────────┬───────────────────────┘
                 │
                 ▼
     ┌───────────────────────────────────┐
     │  Phase 3: QA Agent (Critique)      │
     │  - Evaluate quality                │
     │  - Identify improvements           │
     │  - Provide feedback                │
     └───────────┬───────────────────────┘
                 │
        ┌────────┴────────┐
        │                 │
        ▼ (if issues)     ▼ (if good)
    Creative Agent     Image Agent
    (Refine)          (Select images)
        │                 │
        └────────┬────────┘
                 │
                 ▼
     ┌───────────────────────────────────┐
     │  Phase 4: Publishing Agent         │
     │  - Format for Strapi CMS           │
     │  - Add SEO metadata                │
     │  - Prepare for publication         │
     └───────────┬───────────────────────┘
                 │
                 ▼
     ┌───────────────────────────────────┐
     │  Return Publication-Ready Content  │
     └───────────────────────────────────┘
```

**Each Agent's Structure:**

```python
# Example: ResearchAgent
class ResearchAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="ResearchAgent",
            role=AgentRole.RESEARCH,
            description="Gather and verify information"
        )

    async def execute(self, task):
        """
        1. Parse task input
        2. Use tools for web search (via MCP)
        3. Query databases
        4. Format results
        5. Return to orchestrator
        """

        # Use tools from Model Context Protocol
        search_results = await self.tools.web_search(task.topic)
        verified_data = await self.verify_sources(search_results)

        return {
            "research_data": verified_data,
            "confidence_score": 0.92,
            "sources_count": len(search_results)
        }
```

---

### **STEP 5: Services - Supporting Infrastructure**

**Location:** `src/cofounder_agent/services/`

**Key Services:**

#### 1. **Database Service** - Data Persistence

```python
# src/cofounder_agent/services/database_service.py
class DatabaseService:
    """PostgreSQL connection and CRUD operations"""

    async def save_task(self, task_data):
        # Store task in PostgreSQL
        # Replaced Google Firestore

    async def get_task(self, task_id):
        # Retrieve task status

    async def list_tasks(self):
        # Get all tasks with pagination
```

**What It Stores:**

- Task queue and history
- User data
- Task results
- Agent performance metrics

#### 2. **Model Router Service** - LLM Selection

```python
# src/cofounder_agent/services/model_router.py
class DynamicModelRouter:
    """Select and route to best LLM"""

    async def query(self, prompt, model_type="creative"):
        """
        Fallback chain:
        1. Try Ollama (local, free) ← First choice
        2. Try Claude 3 Opus (Anthropic) ← If Ollama fails
        3. Try GPT-4 (OpenAI) ← If Claude fails
        4. Try Gemini (Google) ← If GPT fails
        5. Use fallback model ← Last resort
        """
```

**Why This Matters:**

- Free local inference when possible (Ollama)
- Automatic fallback if one provider fails
- Cost optimization
- Privacy protection (local processing first)

#### 3. **Task Store Service** - Command Queue

```python
# src/cofounder_agent/services/task_store_service.py
class TaskStore:
    """Manage task queue"""

    async def enqueue(self, task):
        # Add to PostgreSQL queue

    async def dequeue(self):
        # Get next task for agent

    async def update_status(self, task_id, status):
        # Update task progress
```

**Flow:**

```
User Creates Task
    ↓
TaskStore.enqueue()  # Add to PostgreSQL
    ↓
Agent polls TaskStore
    ↓
Agent picks up task
    ↓
Agent executes
    ↓
TaskStore.update_status()  # Update progress
    ↓
Frontend polls for updates
    ↓
User sees result
```

#### 4. **Memory System** - Context & Learning

```python
# src/cofounder_agent/memory_system.py
class MemorySystem:
    """Short-term and long-term memory"""

    async def store_short_term(self, context):
        # Store current conversation
        # TTL: 1 hour

    async def store_long_term(self, knowledge):
        # Store persistent knowledge
        # TTL: permanent

    async def search_semantic(self, query):
        # Find related memories
        # Used for context in prompts
```

#### 5. **Logging & Monitoring**

```python
# src/cofounder_agent/services/logger_config.py
# Centralized logging for all components
```

---

## 🔄 COMPLETE REQUEST-TO-RESPONSE CYCLE

### **Example: Generate Blog Post**

**Request Comes In:**

```
User in Oversight Hub clicks "Generate Blog Post"

POST http://localhost:8000/api/generate-blog-post
{
  "topic": "AI in Business",
  "style": "professional",
  "length": 2000
}
```

**Step 1: Route Handler** (`src/cofounder_agent/routes/content_routes.py`)

```python
@app.post("/api/generate-blog-post")
async def generate_blog_post(request: BlogPostRequest):
    # Create task object
    task = Task(
        id=uuid4(),
        type="content_generation",
        input=request.dict(),
        status="pending"
    )

    # Pass to orchestrator
    result = await orchestrator.execute_task(task)

    # Return to user
    return result
```

**Step 2: Orchestrator Routes** (`src/cofounder_agent/multi_agent_orchestrator.py`)

```python
async def execute_task(self, task):
    # Identify task type
    if task.type == "content_generation":
        # Route to content agent
        agent = self.agents["content"]
        result = await agent.execute(task)

    return result
```

**Step 3: Content Agent Executes** (`src/agents/content_agent/orchestrator.py`)

```python
async def execute(self, task):
    # Phase 1: Research
    research = await self.research_agent.execute(task)

    # Phase 2: Create draft
    draft = await self.creative_agent.execute({
        **task,
        "research": research
    })

    # Phase 3: QA/Critique
    feedback = await self.qa_agent.execute(draft)

    # Phase 4: Refine if needed
    if feedback.needs_improvement:
        draft = await self.creative_agent.execute({
            **task,
            "draft": draft,
            "feedback": feedback
        })

    # Phase 5: Add images
    images = await self.image_agent.execute(draft)

    # Phase 6: Format for CMS
    final = await self.publishing_agent.execute({
        "content": draft,
        "images": images
    })

    return final
```

**Step 4: Model Selection** (Each Agent)

```python
# When agent needs to call LLM:
response = await model_router.query(
    prompt=prompt_text,
    model_type="creative"  # For creative tasks
)

# Model router decides:
# Try Ollama locally → Success! Use it
# Cost: $0, Speed: Fast, Privacy: Full
```

**Step 5: Store Results** (`src/cofounder_agent/services/database_service.py`)

```python
# Save to PostgreSQL
await database_service.save_task({
    "task_id": task_id,
    "status": "completed",
    "result": final_content,
    "agents_used": ["research", "creative", "qa", "image", "publishing"],
    "cost": 0.00,  # Used local Ollama
    "execution_time": 45.3,
    "timestamp": datetime.now()
})
```

**Step 6: Return to Frontend**

```json
{
  "task_id": "abc123xyz",
  "status": "completed",
  "result": {
    "title": "AI in Business: A Comprehensive Guide",
    "content": "...",
    "images": ["image1.jpg", "image2.jpg"],
    "seo_title": "AI in Business 2025",
    "seo_description": "...",
    "reading_time": 8
  },
  "execution_time": 45.3,
  "cost": 0.0
}
```

**Frontend Updates:**

- Oversight Hub receives response
- Displays generated content
- Shows cost ($0 - used local Ollama)
- Allows publish to Strapi CMS

---

## 🗺️ Data Flow Visualization

```
FRONTEND (Oversight Hub - React)
    ↓ (REST POST)
    │
ROUTES LAYER (FastAPI endpoints)
    ↓ (Request object)
    │
ORCHESTRATOR (Request routing)
    ↓ (Task decomposition)
    │
AGENTS (Parallel execution)
    ├─→ ContentAgent
    │   ├─→ ResearchAgent
    │   ├─→ CreativeAgent
    │   ├─→ QAAgent
    │   ├─→ ImageAgent
    │   └─→ PublishingAgent
    │
    ├─→ FinancialAgent
    ├─→ MarketInsightAgent
    └─→ ComplianceAgent

    ↓ (Each agent needs LLM)
    │
MODEL ROUTER (LLM selection)
    ├─→ Ollama (local) ✓ Preferred
    ├─→ Claude 3 Opus (Anthropic)
    ├─→ GPT-4 (OpenAI)
    └─→ Gemini (Google)

    ↓ (Results aggregated)
    │
DATABASE SERVICE (Store results)
    ├─→ PostgreSQL (replace Firestore)
    └─→ Store task history & results

    ↓ (Format response)
    │
ROUTES LAYER (JSON response)
    ↓ (REST response)
    │
FRONTEND (Display to user)
```

---

## 📊 Key Design Patterns

### **1. Multi-Agent Architecture**

- Each agent is specialized
- Agents run in parallel (async)
- Results are aggregated
- Failures trigger fallbacks

### **2. Model Fallback Chain**

- Ollama first (free, local, fast)
- Anthropic Claude second (quality)
- OpenAI GPT-4 third (proven)
- Google Gemini last (cost-effective)

### **3. Task Queue System**

- PostgreSQL replaced Firestore
- Tasks stored with status
- Agents poll for work
- Frontend polls for updates

### **4. Self-Critiquing Pipeline**

- Generate content
- Evaluate quality (QA Agent)
- Get feedback
- Refine if needed
- Ensures high quality

### **5. Service-Oriented**

- Database service handles persistence
- Model router handles LLM selection
- Task store manages queue
- Memory system stores context
- Each service is independent

---

## 🔧 How to Use This Knowledge

### **When You Need To...**

**Add a new AI capability:**

1. Create new agent in `src/agents/`
2. Inherit from `BaseAgent`
3. Implement `execute()` method
4. Register in MultiAgentOrchestrator
5. Create route in `src/cofounder_agent/routes/`

**Fix an agent issue:**

1. Check `src/agents/` for the specific agent
2. Review logs in `src/cofounder_agent/services/logger_config.py`
3. Check model router fallback chain
4. Verify database persistence

**Improve performance:**

1. Check Model Router (prefer Ollama)
2. Review agent parallel execution
3. Check database query optimization
4. Monitor memory system

**Debug task failures:**

1. Check `src/cofounder_agent/services/task_store_service.py`
2. Review task status in PostgreSQL
3. Check agent logs
4. Verify model availability

---

## 📈 Summary Table

| Component            | Location                      | Purpose           | Used For                      |
| -------------------- | ----------------------------- | ----------------- | ----------------------------- |
| **FastAPI App**      | `main.py`                     | Entry point       | All requests start here       |
| **Routes**           | `routes/`                     | Request handlers  | Accept & parse requests       |
| **Orchestrator**     | `multi_agent_orchestrator.py` | Task routing      | Distribute to agents          |
| **Agents**           | `agents/`                     | Execution         | Do the actual work            |
| **Base Agent**       | `agents/base_agent.py`        | Agent interface   | Common functionality          |
| **Content Agent**    | `agents/content_agent/`       | Content creation  | Self-critiquing pipeline      |
| **Other Agents**     | `agents/{type}_agent/`        | Specialized tasks | Financial, Market, Compliance |
| **Model Router**     | `services/`                   | LLM selection     | Choose AI model               |
| **Database Service** | `services/`                   | Data persistence  | Store tasks & results         |
| **Task Store**       | `services/`                   | Queue management  | Task queue operations         |
| **Memory System**    | `memory_system.py`            | Context storage   | Agent context & learning      |

---

**Next Steps:** Want to explore any specific component deeper? I can show you code examples or explain specific agent implementations!
