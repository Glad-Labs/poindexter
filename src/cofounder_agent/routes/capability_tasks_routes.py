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

from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from services.capability_registry import get_registry, CapabilityMetadata, ParameterSchema
from services.capability_task_executor import (
    CapabilityTaskDefinition,
    CapabilityStep,
    execute_capability_task,
)
from services.capability_tasks_service import CapabilityTasksService


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
    steps: List[StepInputModel] = Field(..., min_items=1)
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
        name: metadata for name, metadata in all_caps.items()
        if (not tag or tag in metadata.tags)
        and (not cost_tier or metadata.cost_tier == cost_tier)
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
                    ParameterSchemaModel(**p.to_dict())
                    for p in cap_detail.input_schema.parameters
                ]
            )
            output_schema = OutputSchemaModel(**cap_detail.output_schema.to_dict())
        
        capabilities.append(CapabilityDetailResponse(
            name=name,
            description=metadata.description,
            version=metadata.version,
            tags=metadata.tags,
            cost_tier=metadata.cost_tier,
            timeout_ms=metadata.timeout_ms,
            input_schema=input_schema,
            output_schema=output_schema,
        ))
    
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
            parameters=[
                ParameterSchemaModel(**p.to_dict())
                for p in cap.input_schema.parameters
            ]
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


# ============ Task Management Endpoints ============

@router.post("/tasks/capability", response_model=TaskResponse)
async def create_capability_task(
    request: CreateTaskRequest,
    owner_id: str = Depends(lambda: "user-123"),  # TODO: Extract from auth
):
    """
    Create a new capability-based task.
    
    A task is a sequence of capabilities where outputs of one step can be used
    as inputs to the next step (pipeline data flow).
    """
    # Validate all capabilities exist
    registry = get_registry()
    for step in request.steps:
        if not registry.get_metadata(step.capability_name):
            raise HTTPException(
                status_code=400,
                detail=f"Capability '{step.capability_name}' not found"
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
    
    # Create task
    task = CapabilityTaskDefinition(
        name=request.name,
        description=request.description or "",
        steps=steps,
        tags=request.tags or [],
        owner_id=owner_id,
    )
    
    # TODO: Persist to database via CapabilityTasksService
    
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
    owner_id: str = Depends(lambda: "user-123"),  # TODO: Extract from auth
):
    """List capability tasks for the current user."""
    # TODO: Query from database via CapabilityTasksService
    
    return TaskListResponse(
        tasks=[],
        total=0,
        skip=skip,
        limit=limit,
    )


@router.get("/tasks/capability/{task_id}", response_model=TaskResponse)
async def get_capability_task(
    task_id: str,
    owner_id: str = Depends(lambda: "user-123"),  # TODO: Extract from auth
):
    """Get details of a specific task."""
    # TODO: Query from database
    raise HTTPException(status_code=404, detail="Task not found")


@router.put("/tasks/capability/{task_id}", response_model=TaskResponse)
async def update_capability_task(
    task_id: str,
    request: CreateTaskRequest,
    owner_id: str = Depends(lambda: "user-123"),  # TODO: Extract from auth
):
    """Update a capability task."""
    # TODO: Update in database
    raise HTTPException(status_code=404, detail="Task not found")


@router.delete("/tasks/capability/{task_id}")
async def delete_capability_task(
    task_id: str,
    owner_id: str = Depends(lambda: "user-123"),  # TODO: Extract from auth
):
    """Delete a capability task."""
    # TODO: Delete from database
    return {"message": "Task deleted"}


# ============ Execution Endpoints ============

@router.post("/tasks/capability/{task_id}/execute", response_model=ExecutionResponse)
async def execute_capability_task_endpoint(
    task_id: str,
    owner_id: str = Depends(lambda: "user-123"),  # TODO: Extract from auth
):
    """
    Execute a capability task.
    
    Runs all steps in sequence, passing outputs between steps according to
    the task definition. Returns execution result with all outputs.
    """
    # TODO: Get task from database
    # TODO: Execute task using CapabilityTaskExecutor
    # TODO: Persist result using CapabilityTasksService
    
    raise HTTPException(status_code=404, detail="Task not found")


@router.get("/tasks/capability/{task_id}/executions/{exec_id}", response_model=ExecutionResponse)
async def get_execution_result(
    task_id: str,
    exec_id: str,
    owner_id: str = Depends(lambda: "user-123"),  # TODO: Extract from auth
):
    """Get the result of a specific task execution."""
    # TODO: Query from database
    raise HTTPException(status_code=404, detail="Execution not found")


@router.get("/tasks/capability/{task_id}/executions")
async def list_executions(
    task_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None),
    owner_id: str = Depends(lambda: "user-123"),  # TODO: Extract from auth
):
    """List execution history for a task."""
    # TODO: Query from database
    
    return {
        "executions": [],
        "total": 0,
        "skip": skip,
        "limit": limit,
    }
