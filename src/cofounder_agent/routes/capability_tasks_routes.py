"""
Capability Tasks API Routes - REST endpoints for capability-based task system.

Endpoints:
- GET /api/capabilities - List all capabilities
- GET /api/capabilities/{name} - Get capability details with schema
- POST /api/tasks/capability - Create a new capability task
- GET /api/tasks/capability - List user's capability tasks
- GET /api/tasks/capability/{id} - Get task details
- PUT /api/tasks/capability/{id} - Update task
- DELETE /api/tasks/capability/{id} - Delete task
- POST /api/tasks/capability/{id}/execute - Execute task
- GET /api/tasks/capability/{id}/executions/{exec_id} - Get execution result
- GET /api/tasks/capability/{id}/executions - List execution history
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from routes.auth_unified import get_current_user
from services.capability_natural_language_composer import get_composer
from services.capability_registry import CapabilityMetadata, ParameterSchema, get_registry
from services.capability_task_executor import (
    CapabilityStep,
    CapabilityTaskDefinition,
    execute_capability_task,
)
import asyncpg
from services.capability_tasks_service import CapabilityTasksService
from services.database_service import DatabaseService
from utils.route_utils import get_database_dependency


def _require_pool(db_service: DatabaseService) -> asyncpg.Pool:
    """Return the connection pool or raise 503 if not yet initialised."""
    if db_service.pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialised")
    return db_service.pool


# ============ Request/Response Models ============


class ParameterSchemaModel(BaseModel):
    """Schema for a single parameter."""

    name: str
    type: str
    description: str = ""
    required: bool = True
    default: Optional[str] = None
    example: Optional[str] = None


class InputSchemaModel(BaseModel):
    """Input schema for a capability."""

    parameters: List[ParameterSchemaModel] = []


class OutputSchemaModel(BaseModel):
    """Output schema for a capability."""

    return_type: str = "any"
    description: str = ""
    output_format: str = "json"


class CapabilityDetailResponse(BaseModel):
    """Details of a single capability."""

    name: str
    description: str
    version: str = "1.0.0"
    tags: List[str] = []
    cost_tier: str
    timeout_ms: int = 60000
    input_schema: InputSchemaModel
    output_schema: OutputSchemaModel


class CapabilityListResponse(BaseModel):
    """List of available capabilities."""

    capabilities: List[CapabilityDetailResponse]
    total: int

    class Config:
        schema_extra = {
            "example": {
                "capabilities": [
                    {
                        "name": "research",
                        "description": "Gather research on a topic",
                        "tags": ["research"],
                        "cost_tier": "balanced",
                    }
                ],
                "total": 1,
            }
        }


class StepInputModel(BaseModel):
    """Input for a single step in a task."""

    capability_name: str = Field(..., description="Capability to execute")
    inputs: dict = Field(default_factory=dict, description="Input parameters")
    output_key: str = Field(..., description="Key to store output under")
    order: int = 0


class CreateTaskRequest(BaseModel):
    """Request to create a capability task."""

    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    steps: List[StepInputModel] = Field(..., min_length=1)
    tags: Optional[List[str]] = None


class TaskResponse(BaseModel):
    """Task definition response."""

    id: str
    name: str
    description: Optional[str]
    steps: List[StepInputModel]
    tags: List[str]
    owner_id: str
    created_at: str


class TaskListResponse(BaseModel):
    """List of tasks response."""

    tasks: List[TaskResponse]
    total: int
    skip: int
    limit: int


class StepResultModel(BaseModel):
    """Result of executing a single step."""

    step_index: int
    capability_name: str
    output_key: str
    output: Optional[dict] = None
    duration_ms: float
    error: Optional[str] = None
    status: str


class ExecutionResponse(BaseModel):
    """Execution result response."""

    execution_id: str
    task_id: str
    status: str
    step_results: List[StepResultModel]
    final_outputs: dict
    total_duration_ms: float
    progress_percent: int
    error: Optional[str] = None
    started_at: str
    completed_at: Optional[str] = None


# ============ Router ============

router = APIRouter(prefix="/api", tags=["capabilities"])


# ============ Capability Discovery Endpoints ============


@router.get("/capabilities", response_model=CapabilityListResponse)
async def list_capabilities(
    tag: Optional[str] = Query(None, description="Filter by tag"),
    cost_tier: Optional[str] = Query(None, description="Filter by cost tier"),
):
    """
    List all available capabilities.

    Capabilities are composable units of functionality that can be chained
    together into tasks.
    """
    registry = get_registry()

    all_caps = registry.list_capabilities()

    # Apply filters
    filtered = {
        name: metadata
        for name, metadata in all_caps.items()
        if (not tag or tag in metadata.tags) and (not cost_tier or metadata.cost_tier == cost_tier)
    }

    # Convert to response models
    capabilities = []
    for name, metadata in filtered.items():
        cap_detail = registry.get(name)
        input_schema = InputSchemaModel()
        output_schema = OutputSchemaModel()

        if cap_detail:
            # Extract schema from capability
            input_schema = InputSchemaModel(
                parameters=[
                    ParameterSchemaModel(**p.to_dict()) for p in cap_detail.input_schema.parameters
                ]
            )
            output_schema = OutputSchemaModel(**cap_detail.output_schema.to_dict())

        capabilities.append(
            CapabilityDetailResponse(
                name=name,
                description=metadata.description,
                version=metadata.version,
                tags=metadata.tags,
                cost_tier=metadata.cost_tier,
                timeout_ms=metadata.timeout_ms,
                input_schema=input_schema,
                output_schema=output_schema,
            )
        )

    return CapabilityListResponse(
        capabilities=capabilities,
        total=len(capabilities),
    )


@router.get("/capabilities/{name}", response_model=CapabilityDetailResponse)
async def get_capability(name: str):
    """Get detailed information about a specific capability."""
    registry = get_registry()

    metadata = registry.get_metadata(name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Capability not found")

    cap = registry.get(name)
    input_schema = InputSchemaModel()
    output_schema = OutputSchemaModel()

    if cap:
        input_schema = InputSchemaModel(
            parameters=[ParameterSchemaModel(**p.to_dict()) for p in cap.input_schema.parameters]
        )
        output_schema = OutputSchemaModel(**cap.output_schema.to_dict())

    return CapabilityDetailResponse(
        name=name,
        description=metadata.description,
        version=metadata.version,
        tags=metadata.tags,
        cost_tier=metadata.cost_tier,
        timeout_ms=metadata.timeout_ms,
        input_schema=input_schema,
        output_schema=output_schema,
    )


# ============ Natural Language Composition Endpoints - MUST COME BEFORE GENERIC /tasks/capability ============


class NaturalLanguageRequest(BaseModel):
    """Request to compose a task from natural language."""

    request: str = Field(..., min_length=10, description="Natural language request")
    auto_execute: bool = Field(False, description="Whether to execute task immediately")
    save_task: bool = Field(True, description="Whether to save the composed task")


class NaturalLanguageResponse(BaseModel):
    """Response containing composed task suggestion."""

    success: bool
    task_definition: Optional[dict] = None  # The suggested task
    explanation: str  # Human-readable explanation
    confidence: float  # 0-1 confidence in composition
    execution_id: Optional[str] = None  # If auto-executed
    error: Optional[str] = None


@router.post(
    "/tasks/capability/compose-from-natural-language", response_model=NaturalLanguageResponse
)
async def compose_task_from_natural_language(
    payload: NaturalLanguageRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Compose a capability task from a natural language request.

    The LLM analyzes the request and suggests a chain of capabilities to accomplish the goal.

    Example:
        Request: "Write a blog post about AI trends, add images, and post to social media"
        Result: Task with steps [research → generate_content → select_images → publish]
    """
    try:
        owner_id = current_user.get("id", "unknown")
        composer = get_composer()

        # Compose task from natural language
        result = await composer.compose_from_request(
            request=payload.request,
            auto_execute=payload.auto_execute,
            owner_id=owner_id,
        )

        if not result.success:
            return NaturalLanguageResponse(
                success=False,
                explanation=result.explanation,
                error=result.error,
                confidence=0.0,
            )

        # Convert task definition to dict for response
        task_dict = None
        if result.task_definition:
            task_dict = {
                "name": result.task_definition.name,
                "description": result.task_definition.description,
                "steps": [
                    {
                        "capability_name": step.capability_name,
                        "inputs": step.inputs,
                        "output_key": step.output_key,
                        "order": step.order,
                    }
                    for step in result.task_definition.steps
                ],
                "tags": result.task_definition.tags,
            }

        # Optionally save the task to database
        if payload.save_task and result.task_definition:
            task_service = CapabilityTasksService(_require_pool(db_service))
            await task_service.create_task(
                    name=result.task_definition.name,
                    description=result.task_definition.description,
                    steps=result.task_definition.steps,
                    owner_id=owner_id,
                    tags=result.task_definition.tags,
                )

        return NaturalLanguageResponse(
            success=True,
            task_definition=task_dict or result.suggested_task,
            explanation=result.explanation,
            confidence=result.confidence,
            execution_id=result.execution_id,
        )

    except Exception as e:
        return NaturalLanguageResponse(
            success=False,
            explanation="Error composing task from natural language",
            error=str(e),
            confidence=0.0,
        )


@router.post("/tasks/capability/compose-and-execute", response_model=NaturalLanguageResponse)
async def compose_and_execute(
    payload: NaturalLanguageRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Compose and immediately execute a task from natural language.

    Returns execution results as they complete.
    """
    # Use the same endpoint but force auto_execute
    payload.auto_execute = True
    payload.save_task = True
    return await compose_task_from_natural_language(payload, current_user, db_service)


# ============ Task Management Endpoints ============


@router.post("/tasks/capability", response_model=TaskResponse)
async def create_capability_task(
    request: CreateTaskRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Create a new capability-based task.

    A task is a sequence of capabilities where outputs of one step can be used
    as inputs to the next step (pipeline data flow).
    """
    owner_id = current_user.get("id", "unknown")

    # Validate all capabilities exist
    registry = get_registry()
    for step in request.steps:
        if not registry.get_metadata(step.capability_name):
            raise HTTPException(
                status_code=400, detail=f"Capability '{step.capability_name}' not found"
            )

    # Create capability steps
    steps = [
        CapabilityStep(
            capability_name=step.capability_name,
            inputs=step.inputs,
            output_key=step.output_key,
            order=i,
        )
        for i, step in enumerate(request.steps)
    ]

    # Create and persist task to database
    task_service = CapabilityTasksService(_require_pool(db_service))
    task = await task_service.create_task(
        name=request.name,
        description=request.description or "",
        steps=steps,
        owner_id=owner_id,
        tags=request.tags or [],
    )

    return TaskResponse(
        id=task.id,
        name=task.name,
        description=task.description,
        steps=[
            StepInputModel(
                capability_name=s.capability_name,
                inputs=s.inputs,
                output_key=s.output_key,
                order=s.order,
            )
            for s in task.steps
        ],
        tags=task.tags,
        owner_id=task.owner_id,
        created_at=task.created_at.isoformat(),
    )


@router.get("/tasks/capability", response_model=TaskListResponse)
async def list_capability_tasks(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """List capability tasks for the current user."""
    owner_id = current_user.get("id", "unknown")

    task_service = CapabilityTasksService(_require_pool(db_service))
    tasks, total = await task_service.list_tasks(
        owner_id=owner_id,
        skip=skip,
        limit=limit,
    )

    return TaskListResponse(
        tasks=[
            TaskResponse(
                id=t.id,
                name=t.name,
                description=t.description,
                steps=[
                    StepInputModel(
                        capability_name=s.capability_name,
                        inputs=s.inputs,
                        output_key=s.output_key,
                        order=s.order,
                    )
                    for s in t.steps
                ],
                tags=t.tags,
                owner_id=t.owner_id,
                created_at=t.created_at.isoformat(),
            )
            for t in tasks
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/tasks/capability/{task_id}", response_model=TaskResponse)
async def get_capability_task(
    task_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Get details of a specific task."""
    owner_id = current_user.get("id", "unknown")

    task_service = CapabilityTasksService(_require_pool(db_service))
    task = await task_service.get_task(task_id, owner_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskResponse(
        id=task.id,
        name=task.name,
        description=task.description,
        steps=[
            StepInputModel(
                capability_name=s.capability_name,
                inputs=s.inputs,
                output_key=s.output_key,
                order=s.order,
            )
            for s in task.steps
        ],
        tags=task.tags,
        owner_id=task.owner_id,
        created_at=task.created_at.isoformat(),
    )


@router.put("/tasks/capability/{task_id}", response_model=TaskResponse)
async def update_capability_task(
    task_id: str,
    request: CreateTaskRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Update a capability task."""
    owner_id = current_user.get("id", "unknown")

    # Validate all capabilities exist
    registry = get_registry()
    for step in request.steps:
        if not registry.get_metadata(step.capability_name):
            raise HTTPException(
                status_code=400, detail=f"Capability '{step.capability_name}' not found"
            )

    # Create capability steps
    steps = [
        CapabilityStep(
            capability_name=step.capability_name,
            inputs=step.inputs,
            output_key=step.output_key,
            order=i,
        )
        for i, step in enumerate(request.steps)
    ]

    task_service = CapabilityTasksService(_require_pool(db_service))
    # Check task exists and is owned by user
    task = await task_service.get_task(task_id, owner_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Update the task
    updated_task = await task_service.update_task(
        task_id=task_id,
        owner_id=owner_id,
        name=request.name,
        description=request.description or "",
        steps=steps,
        tags=request.tags or [],
    )

    if updated_task is None:
        raise HTTPException(status_code=404, detail="Task not found after update")

    return TaskResponse(
        id=updated_task.id,
        name=updated_task.name,
        description=updated_task.description,
        steps=[
            StepInputModel(
                capability_name=s.capability_name,
                inputs=s.inputs,
                output_key=s.output_key,
                order=s.order,
            )
            for s in updated_task.steps
        ],
        tags=updated_task.tags,
        owner_id=updated_task.owner_id,
        created_at=updated_task.created_at.isoformat(),
    )


@router.delete("/tasks/capability/{task_id}")
async def delete_capability_task(
    task_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Delete a capability task."""
    owner_id = current_user.get("id", "unknown")

    task_service = CapabilityTasksService(_require_pool(db_service))
    # Check task exists and is owned by user
    task = await task_service.get_task(task_id, owner_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Delete the task
    await task_service.delete_task(task_id, owner_id)

    return {"message": "Task deleted"}


# ============ Execution Endpoints ============


@router.post("/tasks/capability/{task_id}/execute", response_model=ExecutionResponse)
async def execute_capability_task_endpoint(
    task_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Execute a capability task.

    Runs all steps in sequence, passing outputs between steps according to
    the task definition. Returns execution result with all outputs.
    """
    owner_id = current_user.get("id", "unknown")

    # Get task from database
    task_service = CapabilityTasksService(_require_pool(db_service))
    task = await task_service.get_task(task_id, owner_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        # Execute task using CapabilityTaskExecutor
        execution_result = await execute_capability_task(task)

        # Persist result to database
        await task_service.persist_execution(execution_result)

        # Build response
        return ExecutionResponse(
            execution_id=execution_result.execution_id,
            task_id=task_id,
            status=execution_result.status,
            step_results=[
                StepResultModel(
                    step_index=sr.step_index,
                    capability_name=sr.capability_name,
                    output_key=sr.output_key,
                    output=sr.output,
                    duration_ms=sr.duration_ms,
                    error=sr.error,
                    status=sr.status,
                )
                for sr in execution_result.step_results
            ],
            final_outputs=execution_result.final_outputs,
            total_duration_ms=execution_result.total_duration_ms,
            progress_percent=execution_result.progress_percent,
            error=execution_result.error,
            started_at=execution_result.started_at.isoformat(),
            completed_at=(
                execution_result.completed_at.isoformat() if execution_result.completed_at else None
            ),
        )
    except Exception as e:
        return ExecutionResponse(
            execution_id="unknown",
            task_id=task_id,
            status="failed",
            step_results=[],
            final_outputs={},
            total_duration_ms=0,
            progress_percent=0,
            error=str(e),
            started_at=datetime.now(timezone.utc).isoformat(),
        )


@router.get("/tasks/capability/{task_id}/executions/{exec_id}", response_model=ExecutionResponse)
async def get_execution_result(
    task_id: str,
    exec_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Get the result of a specific task execution."""
    owner_id = current_user.get("id", "unknown")

    task_service = CapabilityTasksService(_require_pool(db_service))
    execution = await task_service.get_execution(exec_id, owner_id)

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return ExecutionResponse(
        execution_id=execution.execution_id,
        task_id=execution.task_id,
        status=execution.status,
        step_results=[
            StepResultModel(
                step_index=sr.step_index,
                capability_name=sr.capability_name,
                output_key=sr.output_key,
                output=sr.output,
                duration_ms=sr.duration_ms,
                error=sr.error,
                status=sr.status,
            )
            for sr in execution.step_results
        ],
        final_outputs=execution.final_outputs,
        total_duration_ms=execution.total_duration_ms,
        progress_percent=execution.progress_percent,
        error=execution.error,
        started_at=execution.started_at.isoformat(),
        completed_at=execution.completed_at.isoformat() if execution.completed_at else None,
    )


@router.get("/tasks/capability/{task_id}/executions")
async def list_executions(
    task_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """List execution history for a task."""
    owner_id = current_user.get("id", "unknown")

    # Verify task exists and is owned by user
    task_service = CapabilityTasksService(_require_pool(db_service))
    task = await task_service.get_task(task_id, owner_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Get execution history
    executions, total = await task_service.list_executions(
        task_id=task_id,
        owner_id=owner_id,
        skip=skip,
        limit=limit,
        status_filter=status,
    )

    return {
        "executions": [
            {
                "execution_id": e.execution_id,
                "task_id": e.task_id,
                "status": e.status,
                "progress_percent": e.progress_percent,
                "total_duration_ms": e.total_duration_ms,
                "started_at": e.started_at.isoformat(),
                "completed_at": e.completed_at.isoformat() if e.completed_at else None,
            }
            for e in executions
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }
