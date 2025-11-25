# 🛠️ Implementation Guide - Code Examples

**This document shows concrete code changes needed for the modernization.**

---

## Phase 1: Create Modular Task System

### Step 1: Create Base Task Class

**File:** `src/cofounder_agent/tasks/base.py`

```python
from abc import ABC, abstractmethod
from typing import Any, Dict
from pydantic import BaseModel


class TaskInput(BaseModel):
    """Base input schema for all tasks"""
    pass


class TaskOutput(BaseModel):
    """Base output schema for all tasks"""
    success: bool
    data: Dict[str, Any]
    error: str = None


class Task(ABC):
    """Base class for all modular tasks"""

    def __init__(self, llm_client, memory_system, db_service):
        self.llm = llm_client
        self.memory = memory_system
        self.db = db_service

    @property
    @abstractmethod
    def task_id(self) -> str:
        """Unique identifier for this task (e.g., 'research', 'creative')"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this task does"""
        pass

    @abstractmethod
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the task.

        Args:
            input_data: Data from previous task(s) or initial input

        Returns:
            Dictionary with output that becomes input for next task
        """
        pass

    async def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """Override to validate input before execution"""
        return True

    async def save_execution(self, input_data, output_data, execution_time_ms):
        """Save task execution for auditing"""
        await self.db.save_task_execution(
            task_id=self.task_id,
            input=input_data,
            output=output_data,
            execution_time_ms=execution_time_ms
        )
```

### Step 2: Convert Existing Agents to Tasks

**File:** `src/cofounder_agent/tasks/research_task.py`

```python
from datetime import datetime
from .base import Task


class ResearchTask(Task):
    """Find information on a topic"""

    @property
    def task_id(self) -> str:
        return "research"

    @property
    def description(self) -> str:
        return "Research a topic and gather information"

    async def execute(self, input_data: dict) -> dict:
        """Execute research task"""

        # Get topic from input
        topic = input_data.get("topic", input_data.get("title", ""))

        if not topic:
            return {
                "success": False,
                "error": "No topic provided"
            }

        try:
            # Use LLM to research
            research_prompt = f"Research this topic and provide key information: {topic}"

            research_data = await self.llm.query(research_prompt)

            # Store in memory for other tasks
            await self.memory.store(f"research:{topic}", research_data)

            return {
                "success": True,
                "research_data": research_data,
                "topic": topic,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
```

**File:** `src/cofounder_agent/tasks/creative_task.py`

```python
from .base import Task


class CreativeTask(Task):
    """Generate creative content"""

    @property
    def task_id(self) -> str:
        return "creative"

    @property
    def description(self) -> str:
        return "Generate creative content based on research"

    async def execute(self, input_data: dict) -> dict:
        """Execute creative task"""

        # Get previous research if it exists
        research_data = input_data.get("research_data", "")
        topic = input_data.get("topic", "")
        style = input_data.get("style", "professional")
        tone = input_data.get("tone", "informative")
        length = input_data.get("target_length", 2000)

        try:
            # Build prompt
            prompt = f"""
            Generate {length}-word content about: {topic}
            Style: {style}
            Tone: {tone}
            Research context: {research_data}

            Please provide creative, well-written content.
            """

            content = await self.llm.query(prompt)

            return {
                "success": True,
                "content": content,
                "length": len(content.split()),
                "style": style,
                "tone": tone
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
```

**File:** `src/cofounder_agent/tasks/qa_task.py`

```python
from .base import Task


class QATask(Task):
    """Evaluate and provide feedback on content"""

    @property
    def task_id(self) -> str:
        return "qa"

    @property
    def description(self) -> str:
        return "Evaluate content quality and provide improvement suggestions"

    async def execute(self, input_data: dict) -> dict:
        """Execute QA task"""

        content = input_data.get("content", "")

        if not content:
            return {
                "success": False,
                "error": "No content provided for QA"
            }

        try:
            # Evaluate content
            eval_prompt = f"""
            Evaluate this content and provide:
            1. Quality score (0-10)
            2. Specific feedback
            3. Improvement suggestions

            Content: {content}
            """

            evaluation = await self.llm.query(eval_prompt)

            return {
                "success": True,
                "evaluation": evaluation,
                "needs_revision": False  # Logic to determine this
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
```

### Step 3: Create Task Registry

**File:** `src/cofounder_agent/services/task_registry.py`

```python
from typing import Dict
from ..tasks.base import Task
from ..tasks.research_task import ResearchTask
from ..tasks.creative_task import CreativeTask
from ..tasks.qa_task import QATask
from ..tasks.image_task import ImageTask
from ..tasks.publish_task import PublishTask


class TaskRegistry:
    """Central registry of all available tasks"""

    def __init__(self, llm_client, memory_system, db_service):
        self.llm = llm_client
        self.memory = memory_system
        self.db = db_service
        self.tasks: Dict[str, Task] = {}
        self._register_built_in_tasks()

    def _register_built_in_tasks(self):
        """Register all built-in tasks"""

        # Content generation tasks
        self.register("research", ResearchTask(self.llm, self.memory, self.db))
        self.register("creative", CreativeTask(self.llm, self.memory, self.db))
        self.register("qa", QATask(self.llm, self.memory, self.db))
        self.register("image", ImageTask(self.llm, self.memory, self.db))
        self.register("publish", PublishTask(self.llm, self.memory, self.db))

        # Add more tasks as created

    def register(self, task_id: str, task: Task):
        """Register a new task"""
        if task_id in self.tasks:
            raise ValueError(f"Task '{task_id}' already registered")
        self.tasks[task_id] = task

    def get(self, task_id: str) -> Task:
        """Get a task by ID"""
        if task_id not in self.tasks:
            raise ValueError(f"Task '{task_id}' not found. Available: {list(self.tasks.keys())}")
        return self.tasks[task_id]

    def list_all(self) -> Dict[str, str]:
        """List all available tasks"""
        return {
            task_id: task.description
            for task_id, task in self.tasks.items()
        }
```

---

## Phase 2: Create Modular Pipeline Executor

**File:** `src/cofounder_agent/services/pipeline_executor.py`

```python
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import time
import asyncio


@dataclass
class PipelineExecutionResult:
    """Result of pipeline execution"""
    workflow_id: str
    final_output: Dict[str, Any]
    intermediates: Dict[str, Dict[str, Any]]
    execution_time_ms: float
    status: str  # "success" or "failed"
    error: Optional[str] = None


class ModularPipelineExecutor:
    """Execute a pipeline of modular tasks"""

    def __init__(self, task_registry):
        self.task_registry = task_registry

    async def execute(
        self,
        workflow_id: str,
        pipeline: List[str],
        initial_input: Dict[str, Any],
        save_intermediates: bool = True,
        on_error: str = "fail"  # "fail" or "skip"
    ) -> PipelineExecutionResult:
        """
        Execute a pipeline of tasks.

        Args:
            workflow_id: Unique identifier for this execution
            pipeline: List of task IDs in order
            initial_input: Initial input data
            save_intermediates: Whether to save output of each task
            on_error: What to do if a task fails ("fail" or "skip")

        Returns:
            PipelineExecutionResult with outputs and metadata
        """

        start_time = time.time()
        intermediates = {}
        current_input = initial_input.copy()

        try:
            for task_id in pipeline:
                try:
                    # Get task
                    task = self.task_registry.get(task_id)

                    # Validate input
                    if not await task.validate_input(current_input):
                        if on_error == "fail":
                            raise ValueError(f"Task '{task_id}' validation failed")
                        else:
                            continue  # Skip this task

                    # Execute task
                    task_start = time.time()
                    task_output = await task.execute(current_input)
                    task_time = time.time() - task_start

                    # Save intermediate if requested
                    if save_intermediates:
                        intermediates[task_id] = {
                            "output": task_output,
                            "time_ms": task_time * 1000
                        }

                    # Check if task was successful
                    if not task_output.get("success", True):
                        error_msg = task_output.get("error", "Unknown error")
                        if on_error == "fail":
                            raise RuntimeError(f"Task '{task_id}' failed: {error_msg}")
                        else:
                            continue  # Skip this task

                    # Update input for next task
                    current_input = {
                        **initial_input,  # Always include original input
                        **task_output,    # Add task output
                        "_previous_output": task_output
                    }

                except Exception as e:
                    if on_error == "fail":
                        raise
                    else:
                        # Log and skip
                        print(f"Task '{task_id}' failed, skipping: {str(e)}")
                        continue

            elapsed_ms = (time.time() - start_time) * 1000

            return PipelineExecutionResult(
                workflow_id=workflow_id,
                final_output=current_input,
                intermediates=intermediates,
                execution_time_ms=elapsed_ms,
                status="success"
            )

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            return PipelineExecutionResult(
                workflow_id=workflow_id,
                final_output=current_input,
                intermediates=intermediates,
                execution_time_ms=elapsed_ms,
                status="failed",
                error=str(e)
            )
```

---

## Phase 3: Create Unified Workflow Router

**File:** `src/cofounder_agent/models/workflow.py`

```python
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel


class ExecutionOptions(BaseModel):
    """Workflow execution options"""
    model: str = "auto"  # LLM to use, or "auto" for router selection
    timeout_seconds: int = 300
    max_retries: int = 3
    save_intermediates: bool = True
    on_error: Literal["fail", "skip"] = "fail"


class WorkflowRequest(BaseModel):
    """Universal workflow request"""

    # What type of workflow?
    workflow_type: Literal[
        "content_generation",
        "financial_analysis",
        "market_research",
        "compliance_check",
        "social_media",
        "custom"
    ]

    # Input data
    input_data: Dict[str, Any]

    # Custom pipeline (optional - overrides default)
    custom_pipeline: Optional[List[str]] = None

    # Execution options
    options: ExecutionOptions = ExecutionOptions()

    # Metadata
    user_id: str
    workflow_id: str


class WorkflowResponse(BaseModel):
    """Workflow execution response"""
    workflow_id: str
    status: Literal["success", "failed"]
    final_output: Dict[str, Any]
    intermediates: Dict[str, Any] = {}
    execution_time_ms: float
    error: Optional[str] = None
```

**File:** `src/cofounder_agent/services/workflow_router.py`

```python
from typing import List, Dict, Any
from ..models.workflow import WorkflowRequest, WorkflowResponse


class UnifiedWorkflowRouter:
    """Routes requests through modular pipelines"""

    def __init__(self, executor, database_service, memory_system):
        self.executor = executor
        self.db = database_service
        self.memory = memory_system

        # Define default pipelines
        self.default_pipelines = {
            "content_generation": [
                "research",
                "creative",
                "qa",
                "image",
                "publish"
            ],
            "social_media": [
                "research",
                "creative_social",
                "image_social",
                "publish_social"
            ],
            "financial_analysis": [
                "financial_research",
                "financial_analysis",
                "report_generation"
            ],
            "compliance_check": [
                "research",
                "compliance_check",
                "report_generation"
            ],
            "market_research": [
                "market_research",
                "analysis",
                "report_generation"
            ]
        }

    async def route_and_execute(
        self,
        request: WorkflowRequest
    ) -> WorkflowResponse:
        """Route request to appropriate pipeline and execute"""

        # Determine pipeline to use
        if request.custom_pipeline:
            pipeline = request.custom_pipeline
        else:
            pipeline = self.default_pipelines.get(
                request.workflow_type,
                ["creative"]  # Fallback
            )

        # Execute pipeline
        result = await self.executor.execute(
            workflow_id=request.workflow_id,
            pipeline=pipeline,
            initial_input=request.input_data,
            save_intermediates=request.options.save_intermediates,
            on_error=request.options.on_error
        )

        # Save execution record
        await self.db.save_workflow_execution(
            workflow_id=request.workflow_id,
            user_id=request.user_id,
            request=request.dict(),
            result=result
        )

        # Return response
        return WorkflowResponse(
            workflow_id=result.workflow_id,
            status=result.status,
            final_output=result.final_output,
            intermediates=result.intermediates,
            execution_time_ms=result.execution_time_ms,
            error=result.error
        )
```

**File:** `src/cofounder_agent/routes/workflow_routes.py` (NEW!)

````python
from fastapi import APIRouter, Depends
from ..models.workflow import WorkflowRequest, WorkflowResponse
from ..services.workflow_router import UnifiedWorkflowRouter
from ..services.pipeline_executor import ModularPipelineExecutor
from ..services.task_registry import TaskRegistry


router = APIRouter(prefix="/api/workflow", tags=["workflow"])


@router.post("/execute", response_model=WorkflowResponse)
async def execute_workflow(
    request: WorkflowRequest,
    router_service: UnifiedWorkflowRouter = Depends()
) -> WorkflowResponse:
    """
    Execute a workflow with modular pipelines.

    This is the unified entry point for all workflows.
    Replace old endpoints:
    - POST /api/content/tasks
    - POST /api/tasks
    - POST /api/orchestration/process
    - POST /api/social/generate

    With:
    - POST /api/workflow/execute

    Example:
    ```json
    {
        "workflow_type": "content_generation",
        "input_data": {"topic": "AI Trends"},
        "user_id": "user123",
        "workflow_id": "wf123"
    }
    ```

    To use a custom pipeline:
    ```json
    {
        "workflow_type": "custom",
        "custom_pipeline": ["research", "creative", "publish"],
        "input_data": {"topic": "AI Trends"},
        "user_id": "user123",
        "workflow_id": "wf123"
    }
    ```
    """

    return await router_service.route_and_execute(request)


@router.get("/available-pipelines")
async def list_available_pipelines():
    """List all available workflow types and their default pipelines"""
    return {
        "content_generation": ["research", "creative", "qa", "image", "publish"],
        "social_media": ["research", "creative_social", "image_social", "publish_social"],
        "financial_analysis": ["financial_research", "financial_analysis", "report_generation"],
        "compliance_check": ["research", "compliance_check", "report_generation"],
        "market_research": ["market_research", "analysis", "report_generation"],
        "custom": "Provide your own pipeline in custom_pipeline field"
    }


@router.get("/available-tasks")
async def list_available_tasks(
    task_registry: TaskRegistry = Depends()
):
    """List all available tasks that can be used in pipelines"""
    return task_registry.list_all()
````

---

## Phase 4: Update main.py

**Before (current, with 17 routers):**

```python
from fastapi import FastAPI
from .routes import (
    content_routes, task_routes, command_queue_routes,
    intelligent_orchestrator_routes, poindexter_routes,
    social_routes, chat_routes, cms_routes, auth_routes,
    models, ollama_routes, agents_routes, settings_routes,
    metrics_routes, webhooks, bulk_task_routes
)

app = FastAPI()

app.include_router(content_routes.router)
app.include_router(task_routes.router)
app.include_router(command_queue_routes.router)
app.include_router(intelligent_orchestrator_routes.router)
# ... 13 more include_router calls
```

**After (updated, with new workflow router):**

```python
from fastapi import FastAPI
from .routes import (
    workflow_routes,  # NEW: Single unified entry point
    # Keep old routes for backward compatibility
    content_routes, task_routes, command_queue_routes,
    intelligent_orchestrator_routes, poindexter_routes,
    social_routes, chat_routes, cms_routes, auth_routes,
    models, ollama_routes, agents_routes, settings_routes,
    metrics_routes, webhooks, bulk_task_routes
)

app = FastAPI()

# NEW: Unified workflow endpoint (primary entry point)
app.include_router(workflow_routes.router)

# Keep old routes but mark as deprecated in docs
# These will internally route through UnifiedWorkflowRouter
app.include_router(content_routes.router)
app.include_router(task_routes.router)
# ... rest of old routes
```

---

## Migration Checklist

### Phase 1 Checklist

- [ ] Create `src/cofounder_agent/tasks/base.py`
- [ ] Create `src/cofounder_agent/tasks/research_task.py`
- [ ] Create `src/cofounder_agent/tasks/creative_task.py`
- [ ] Create `src/cofounder_agent/tasks/qa_task.py`
- [ ] Create `src/cofounder_agent/tasks/image_task.py`
- [ ] Create `src/cofounder_agent/tasks/publish_task.py`
- [ ] Create `src/cofounder_agent/tasks/__init__.py`
- [ ] Create `src/cofounder_agent/services/task_registry.py`
- [ ] Update `src/cofounder_agent/main.py` to initialize TaskRegistry in dependency injection

### Phase 2 Checklist

- [ ] Create `src/cofounder_agent/services/pipeline_executor.py`
- [ ] Create `src/cofounder_agent/models/workflow.py`
- [ ] Create `src/cofounder_agent/services/workflow_router.py`
- [ ] Create `src/cofounder_agent/routes/workflow_routes.py`
- [ ] Test new workflow endpoint with sample requests
- [ ] Document new endpoint in API docs

### Phase 3 Checklist

- [ ] Update `content_routes.py` to use `UnifiedWorkflowRouter` internally
- [ ] Update `task_routes.py` to use `UnifiedWorkflowRouter` internally
- [ ] Update `command_queue_routes.py` to use `UnifiedWorkflowRouter` internally
- [ ] Update `social_routes.py` to use `UnifiedWorkflowRouter` internally
- [ ] Add deprecation notices to old endpoints in docs
- [ ] Test backward compatibility with old endpoints

### Phase 4 Checklist

- [ ] Delete `multi_agent_orchestrator.py` (if no longer used)
- [ ] Delete `agents/content_agent/orchestrator.py` polling logic (replace with new executor)
- [ ] Delete `services/poindexter_orchestrator.py` variant
- [ ] Clean up empty agent files (`agents/content_agent.py`, etc.)
- [ ] Consolidate remaining orchestration logic

---

**Ready to start implementing? Begin with Phase 1 task creation.**
