# 💻 Code Examples: src/ Components in Action

**Practical code examples showing how each src/ component works**

---

## 1. main.py - FastAPI Application Setup

```python
# src/cofounder_agent/main.py (excerpt)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Import all route modules
from routes.content_routes import content_router
from routes.task_routes import task_router
from routes.models import models_router
from routes.agents_routes import router as agents_router
from routes.auth_routes import auth_router

# Import services
from services.database_service import DatabaseService
from services.task_store_service import TaskStore
from multi_agent_orchestrator import MultiAgentOrchestrator

# Global services (initialized at startup)
db_service: DatabaseService = None
task_store: TaskStore = None
orchestrator: MultiAgentOrchestrator = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global db_service, task_store, orchestrator
    
    print("🚀 Starting Glad Labs AI Co-Founder...")
    
    # Initialize database connection
    db_service = DatabaseService()
    await db_service.connect()
    print("✓ Database connected")
    
    # Initialize task queue
    task_store = TaskStore(db_service)
    print("✓ Task store initialized")
    
    # Initialize orchestrator with all agents
    orchestrator = MultiAgentOrchestrator(db_service, task_store)
    await orchestrator.initialize_agents()
    print("✓ Orchestrator ready with agents")
    
    yield  # Application runs here
    
    # Shutdown
    print("⏹ Shutting down...")
    await db_service.disconnect()
    print("✓ Goodbye!")

# Create FastAPI app
app = FastAPI(
    title="Glad Labs AI Co-Founder",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Register all route modules
app.include_router(content_router, prefix="/api/content", tags=["Content"])
app.include_router(task_router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(models_router, prefix="/api/models", tags=["Models"])
app.include_router(agents_router, prefix="/api/agents", tags=["Agents"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])

# Health check endpoint
@app.get("/api/health")
async def health_check():
    """Check if system is healthy"""
    return {
        "status": "healthy",
        "database": "connected",
        "agents": "ready",
        "timestamp": datetime.now().isoformat()
    }

# Run with: uvicorn main:app --reload
```

---

## 2. content_routes.py - API Route Handler

```python
# src/cofounder_agent/routes/content_routes.py (excerpt)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import uuid
from datetime import datetime

# Import the orchestrator from main.py
from main import orchestrator, db_service

content_router = APIRouter()

class BlogPostRequest(BaseModel):
    """Request model for blog post generation"""
    topic: str
    style: str = "professional"  # professional, casual, technical
    length: int = 2000  # words
    include_images: bool = True

class ContentResponse(BaseModel):
    """Response model for generated content"""
    task_id: str
    status: str
    content: dict = None
    created_at: str

@content_router.post("/generate-blog-post")
async def generate_blog_post(request: BlogPostRequest) -> ContentResponse:
    """
    Generate a blog post using the content agent pipeline
    
    Pipeline:
    1. Research Agent - Gather information
    2. Creative Agent - Write draft
    3. QA Agent - Evaluate quality
    4. Creative Agent - Refine if needed
    5. Image Agent - Select images
    6. Publishing Agent - Format for CMS
    """
    
    try:
        # Create task object
        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "type": "blog_post_generation",
            "status": "pending",
            "input": {
                "topic": request.topic,
                "style": request.style,
                "length": request.length,
                "include_images": request.include_images
            },
            "created_at": datetime.now().isoformat(),
            "assigned_agent": "content"
        }
        
        # Store task in database
        await db_service.store_task(task)
        print(f"📝 Task created: {task_id}")
        
        # Send to orchestrator for execution
        # The orchestrator will route this to ContentAgent
        await orchestrator.execute_task(task)
        
        return ContentResponse(
            task_id=task_id,
            status="pending",
            created_at=task["created_at"]
        )
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@content_router.get("/generate-blog-post/{task_id}")
async def get_blog_post_status(task_id: str) -> ContentResponse:
    """Get status and result of blog post generation"""
    
    task = await db_service.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return ContentResponse(
        task_id=task_id,
        status=task["status"],
        content=task.get("output") if task["status"] == "completed" else None,
        created_at=task["created_at"]
    )
```

---

## 3. multi_agent_orchestrator.py - Task Routing

```python
# src/cofounder_agent/multi_agent_orchestrator.py (excerpt)

from typing import List, Dict
from enum import Enum
import asyncio

class AgentStatus(Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"

class TaskPriority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class Agent:
    """Agent definition and state tracking"""
    def __init__(self, name: str, capabilities: List[str]):
        self.name = name
        self.capabilities = capabilities  # ["content_writing", "image_generation"]
        self.status = AgentStatus.IDLE
        self.current_task_id = None
        self.success_count = 0
        self.failure_count = 0
    
    def can_handle_task(self, task_requirements: List[str]) -> bool:
        """Check if this agent can handle the task"""
        return all(req in self.capabilities for req in task_requirements)

class MultiAgentOrchestrator:
    """Coordinates all agents"""
    
    def __init__(self, db_service, task_store):
        self.db_service = db_service
        self.task_store = task_store
        
        # Define all agents
        self.agents = {
            "content": Agent("ContentAgent", ["content_writing", "image_selection", "seo"]),
            "financial": Agent("FinancialAgent", ["financial_analysis", "cost_tracking"]),
            "market": Agent("MarketInsightAgent", ["market_analysis", "competitor_research"]),
            "compliance": Agent("ComplianceAgent", ["compliance_check", "risk_assessment"])
        }
    
    async def execute_task(self, task: dict):
        """Execute task by routing to appropriate agent(s)"""
        
        task_type = task.get("type")
        print(f"🔀 Orchestrator routing: {task_type}")
        
        if task_type == "blog_post_generation":
            # This task needs the content agent
            agent = self.agents["content"]
            
            # Check if agent is available
            if agent.status == AgentStatus.OFFLINE:
                raise Exception("Content agent is offline")
            
            # Mark agent as busy
            agent.status = AgentStatus.BUSY
            agent.current_task_id = task["id"]
            
            try:
                # Execute the task via the agent
                result = await agent.execute(task)
                
                # Update task in database
                await self.db_service.update_task(
                    task["id"],
                    {
                        "status": "completed",
                        "output": result,
                        "assigned_agent": agent.name
                    }
                )
                
                agent.success_count += 1
                print(f"✅ Task completed by {agent.name}")
                
            except Exception as e:
                agent.failure_count += 1
                await self.db_service.update_task(
                    task["id"],
                    {
                        "status": "failed",
                        "error": str(e),
                        "assigned_agent": agent.name
                    }
                )
                print(f"❌ Task failed: {e}")
            
            finally:
                # Mark agent as idle
                agent.status = AgentStatus.IDLE
                agent.current_task_id = None
    
    async def execute_complex_task(self, task: dict):
        """Execute task that requires multiple agents in parallel"""
        
        # Example: "Create marketing strategy" needs market + content agents
        market_task = {"type": "market_analysis", **task}
        content_task = {"type": "content_creation", **task}
        
        # Execute both agents in parallel
        results = await asyncio.gather(
            self.agents["market"].execute(market_task),
            self.agents["content"].execute(content_task)
        )
        
        return {
            "market_insights": results[0],
            "content_created": results[1]
        }
```

---

## 4. base_agent.py - Agent Parent Class

```python
# src/agents/base_agent.py (excerpt)

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any

class AgentRole(Enum):
    """Roles for specialized agents"""
    RESEARCH = "research"
    CREATIVE = "creative"
    QA = "qa"
    IMAGE = "image"
    PUBLISHING = "publishing"
    FINANCIAL = "financial"
    MARKET = "market"
    COMPLIANCE = "compliance"

class BaseAgent(ABC):
    """Parent class for all agents"""
    
    def __init__(self, name: str, role: AgentRole):
        self.name = name
        self.role = role
        self.cost_tracking = 0.0
        self.logs = []
    
    async def execute(self, task: dict) -> dict:
        """Execute task - must be overridden by subclasses"""
        self.log_activity(f"Starting execution of {task['type']}")
        
        try:
            result = await self._execute_impl(task)
            self.log_activity(f"Completed successfully")
            return result
        except Exception as e:
            self.log_activity(f"Failed: {e}")
            raise
    
    @abstractmethod
    async def _execute_impl(self, task: dict) -> dict:
        """Subclasses implement actual task logic here"""
        pass
    
    def log_activity(self, message: str):
        """Log agent activities"""
        entry = f"[{self.name}] {message}"
        self.logs.append(entry)
        print(entry)
    
    async def access_tool(self, tool_name: str, params: dict) -> Any:
        """Access external tools via MCP"""
        # Examples: web_search, image_generation, database_query
        print(f"🔧 Using tool: {tool_name} with {params}")
        
        # Would call actual MCP client
        # return mcp_client.call_tool(tool_name, params)
    
    async def track_cost(self, model: str, tokens: int, cost: float):
        """Track API usage and cost"""
        self.cost_tracking += cost
        self.log_activity(f"Cost tracked: {cost} USD ({tokens} tokens)")
    
    async def query_memory(self, key: str) -> Any:
        """Retrieve from agent memory"""
        # Memory system stores context and learning
        pass
    
    async def store_memory(self, key: str, value: Any):
        """Store in agent memory"""
        # Used for learning and context retention
        pass
```

---

## 5. content_agent/orchestrator.py - Specialized Agent

```python
# src/agents/content_agent/orchestrator.py (excerpt)

from agents.base_agent import BaseAgent, AgentRole
import asyncio

class ContentAgentOrchestrator(BaseAgent):
    """Content agent with 6-phase self-critiquing pipeline"""
    
    def __init__(self):
        super().__init__("ContentAgent", AgentRole.CREATIVE)
        
        # Load all sub-agents for the pipeline
        from agents.research_agent import ResearchAgent
        from agents.creative_agent import CreativeAgent
        from agents.qa_agent import QAAgent
        from agents.image_agent import ImageAgent
        from agents.publishing_agent import PublishingAgent
        
        self.research_agent = ResearchAgent()
        self.creative_agent = CreativeAgent()
        self.qa_agent = QAAgent()
        self.image_agent = ImageAgent()
        self.publishing_agent = PublishingAgent()
    
    async def _execute_impl(self, task: dict) -> dict:
        """
        6-Phase Self-Critiquing Content Pipeline
        """
        
        self.log_activity("Starting content generation pipeline")
        topic = task["input"]["topic"]
        
        # PHASE 1: RESEARCH
        self.log_activity(f"Phase 1: Researching '{topic}'")
        research = await self.research_agent.execute({
            "topic": topic,
            "task_id": task["id"]
        })
        self.log_activity(f"✓ Found {len(research['sources'])} sources")
        
        # PHASE 2: CREATE DRAFT
        self.log_activity("Phase 2: Writing initial draft")
        draft = await self.creative_agent.execute({
            "topic": topic,
            "research": research,
            "style": task["input"].get("style", "professional"),
            "length": task["input"].get("length", 2000)
        })
        self.log_activity(f"✓ Draft created: {len(draft['content'])} chars")
        
        # PHASE 3: QA / CRITIQUE
        self.log_activity("Phase 3: Quality evaluation")
        feedback = await self.qa_agent.execute({
            "content": draft["content"],
            "criteria": ["clarity", "accuracy", "engagement"]
        })
        self.log_activity(f"✓ Quality score: {feedback['quality_score']}/100")
        
        # PHASE 4: REFINE (if needed)
        if feedback["quality_score"] < 75:
            self.log_activity("Phase 4: Refining based on feedback")
            draft = await self.creative_agent.execute({
                "topic": topic,
                "research": research,
                "previous_draft": draft["content"],
                "feedback": feedback["suggestions"],
                "refine": True
            })
            self.log_activity("✓ Draft refined")
        
        # PHASE 5: IMAGE SELECTION
        self.log_activity("Phase 5: Selecting images")
        images = await self.image_agent.execute({
            "content": draft["content"],
            "topic": topic,
            "count": 3
        })
        self.log_activity(f"✓ Selected {len(images['urls'])} images")
        
        # PHASE 6: PUBLISHING FORMAT
        self.log_activity("Phase 6: Formatting for publishing")
        final = await self.publishing_agent.execute({
            "content": draft["content"],
            "images": images["urls"],
            "topic": topic,
            "target": "strapi-cms"
        })
        self.log_activity("✓ Ready for publishing")
        
        return {
            "status": "completed",
            "title": final["title"],
            "content": final["content"],
            "seo_data": final["seo"],
            "images": images["urls"],
            "quality_score": feedback["quality_score"],
            "execution_time": task.get("execution_time")
        }
```

---

## 6. model_router.py - Model Selection Service

```python
# src/cofounder_agent/services/model_router.py (excerpt)

from enum import Enum
import os

class ModelProvider(Enum):
    """Available LLM providers"""
    OLLAMA = "ollama"        # Local, free
    CLAUDE = "claude"        # Anthropic
    GPT = "gpt"             # OpenAI
    GEMINI = "gemini"       # Google

class DynamicModelRouter:
    """Select best LLM based on availability and cost"""
    
    def __init__(self):
        self.fallback_chain = [
            ModelProvider.OLLAMA,    # Try local first (free)
            ModelProvider.CLAUDE,    # Try Claude next (high quality)
            ModelProvider.GPT,       # Try GPT (proven)
            ModelProvider.GEMINI     # Try Gemini (cheap)
        ]
    
    async def query(self, prompt: str, model_type: str = "general") -> str:
        """
        Query an LLM with automatic fallback
        
        Fallback chain ensures:
        1. Cheapest option first (Ollama = free)
        2. Quality if needed (Claude Opus)
        3. Reliability (GPT-4)
        4. Fallback available (Gemini)
        """
        
        for provider in self.fallback_chain:
            try:
                print(f"🤖 Trying {provider.value}...")
                
                if provider == ModelProvider.OLLAMA:
                    response = await self._query_ollama(prompt)
                    print(f"✓ Used Ollama (free)")
                    return response
                
                elif provider == ModelProvider.CLAUDE:
                    response = await self._query_claude(prompt)
                    print(f"✓ Used Claude Opus ($0.02)")
                    return response
                
                elif provider == ModelProvider.GPT:
                    response = await self._query_gpt(prompt)
                    print(f"✓ Used GPT-4 ($0.03)")
                    return response
                
                elif provider == ModelProvider.GEMINI:
                    response = await self._query_gemini(prompt)
                    print(f"✓ Used Gemini ($0.01)")
                    return response
            
            except Exception as e:
                print(f"✗ {provider.value} failed: {e}")
                continue
        
        raise Exception("All model providers failed!")
    
    async def _query_ollama(self, prompt: str) -> str:
        """Query local Ollama (free)"""
        # Would call ollama API at localhost:11434
        pass
    
    async def _query_claude(self, prompt: str) -> str:
        """Query Anthropic Claude 3 Opus"""
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = await client.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    
    async def _query_gpt(self, prompt: str) -> str:
        """Query OpenAI GPT-4"""
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    
    async def _query_gemini(self, prompt: str) -> str:
        """Query Google Gemini (fallback)"""
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        response = await genai.generate_text(prompt=prompt)
        return response.result
```

---

## 7. Database Service - Persistence

```python
# src/cofounder_agent/services/database_service.py (excerpt)

import asyncpg
from typing import Dict, List

class DatabaseService:
    """PostgreSQL database operations"""
    
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.pool = None
    
    async def connect(self):
        """Create connection pool"""
        self.pool = await asyncpg.create_pool(self.connection_string)
        print("✓ Connected to PostgreSQL")
    
    async def disconnect(self):
        """Close connection pool"""
        await self.pool.close()
        print("✓ Disconnected from PostgreSQL")
    
    async def store_task(self, task: dict):
        """Store task in database"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tasks (id, type, status, input, created_at)
                VALUES ($1, $2, $3, $4, $5)
                """,
                task["id"],
                task["type"],
                task["status"],
                task["input"],
                task["created_at"]
            )
        print(f"💾 Task stored: {task['id']}")
    
    async def get_task(self, task_id: str) -> Dict:
        """Retrieve task from database"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id = $1",
                task_id
            )
        return dict(row) if row else None
    
    async def update_task(self, task_id: str, updates: Dict):
        """Update task status and results"""
        async with self.pool.acquire() as conn:
            # Build dynamic SET clause
            set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates.keys()))
            query = f"UPDATE tasks SET {set_clause} WHERE id = $1"
            
            await conn.execute(
                query,
                task_id,
                *updates.values()
            )
        print(f"🔄 Task updated: {task_id}")
    
    async def list_tasks(self, status: str = None, limit: int = 10) -> List[Dict]:
        """List all tasks with optional filtering"""
        async with self.pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    "SELECT * FROM tasks WHERE status = $1 ORDER BY created_at DESC LIMIT $2",
                    status,
                    limit
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT $1",
                    limit
                )
        return [dict(row) for row in rows]
```

---

## Summary: Component Interactions

```
Request Flow:

1. REST Call
   ↓
2. Routes (parse, validate)
   ↓
3. Orchestrator (route to agent)
   ↓
4. Agent (execute, inherits from BaseAgent)
   ↓
5. Model Router (select LLM with fallback)
   ↓
6. Database (store results)
   ↓
7. Response (return to frontend)
```

**Key Insight:** Each component has a single responsibility and passes work up/down the chain.

