"""
Poindexter API Routes - Proof of Concept

Exposes Poindexter orchestrator via FastAPI endpoints:
- POST /api/v2/orchestrate - Main orchestration endpoint
- GET /api/v2/orchestrate/{workflow_id} - Get workflow status
- GET /api/v2/orchestrate-status - System status

These routes are the interface between users/UI and Poindexter's autonomous orchestration.
"""

from fastapi import APIRouter, HTTPException, Query, Body, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

# Router for v2 API (Poindexter)
poindexter_router = APIRouter(
    prefix="/api/v2",
    tags=["poindexter-v2"]
)


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class Constraint(BaseModel):
    """Constraint on orchestration."""
    name: str = Field(..., description="Constraint name (budget, quality, time, model_preference)")
    value: Any = Field(..., description="Constraint value")
    unit: Optional[str] = Field(None, description="Unit (USD, seconds, percent, etc.)")


class OrchestrationRequest(BaseModel):
    """Request to orchestrate a command."""
    command: str = Field(
        ...,
        description="Natural language command (e.g., 'Create a blog post about AI trends')"
    )
    constraints: Optional[List[Constraint]] = Field(
        None,
        description="Constraints on execution (budget, quality thresholds, etc.)"
    )
    context: Optional[Dict[str, Any]] = Field(
        None,
        description="User/project context for personalization"
    )
    background: bool = Field(
        False,
        description="Run in background and return workflow_id"
    )


class WorkflowStep(BaseModel):
    """A step in the planned workflow."""
    step: int
    tool: str
    description: str
    estimated_cost: float
    estimated_time: float
    input_params: Dict[str, Any]
    status: str = "pending"


class ReasoningStep(BaseModel):
    """A step in Poindexter's reasoning process."""
    step: str
    input: Optional[Any] = None
    reasoning: str
    output: Optional[Any] = None


class OrchestrationResponse(BaseModel):
    """Response from orchestration."""
    workflow_id: str = Field(..., description="Unique workflow identifier")
    status: str = Field(..., description="success, partial, error, in_progress")
    result: Optional[Any] = Field(default=None, description="Final result")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    
    # Workflow details
    workflow_planned: List[WorkflowStep] = Field(default_factory=list)
    workflow_executed: List[Dict[str, Any]] = Field(default_factory=list)
    reasoning_trace: List[ReasoningStep] = Field(default_factory=list)
    
    # Metrics
    total_time: float = Field(default=0.0, description="Total execution time in seconds")
    total_cost: float = Field(default=0.0, description="Total cost in USD")
    tools_used: List[str] = Field(default_factory=list)
    critique_loops: int = Field(default=0, description="Number of self-critique iterations")
    
    # Metadata
    created_at: datetime
    completed_at: Optional[datetime] = Field(default=None)


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    poindexter_ready: bool
    smolagents_available: bool
    mcp_available: bool
    model_router_available: bool
    agents_available: List[str]


# ============================================================================
# GLOBAL STATE (in real app, inject via dependency)
# ============================================================================

# In-memory storage for proof-of-concept (use DB in production)
_workflows: Dict[str, OrchestrationResponse] = {}
_poindexter_instance = None


def set_poindexter_instance(poindexter):
    """Set the Poindexter orchestrator instance."""
    global _poindexter_instance
    _poindexter_instance = poindexter


# ============================================================================
# ORCHESTRATION ENDPOINT - Main API
# ============================================================================

@poindexter_router.post(
    "/orchestrate",
    response_model=OrchestrationResponse,
    summary="Orchestrate a complex task autonomously",
    description="""
    Main Poindexter endpoint. Accepts a natural language command and optional constraints,
    then autonomously reasons about the best workflow to complete the task.
    
    The orchestrator:
    1. Parses your command to understand intent
    2. Discovers available tools (agents + MCP servers)
    3. Plans the optimal workflow
    4. Validates against constraints
    5. Executes with error recovery
    6. Runs self-critique if configured
    
    Returns full workflow trace for debugging/transparency.
    """
)
async def orchestrate(
    request: OrchestrationRequest,
    background_tasks: BackgroundTasks
) -> OrchestrationResponse:
    """
    Orchestrate a complex task using Poindexter.
    
    Example request:
    ```json
    {
        "command": "Create a blog post about AI trends with images and publish to Strapi",
        "constraints": [
            {"name": "budget", "value": 0.50, "unit": "USD"},
            {"name": "quality_threshold", "value": 0.90},
            {"name": "max_runtime", "value": 300, "unit": "seconds"}
        ],
        "context": {
            "user_id": "user123",
            "project": "marketing",
            "style": "professional"
        }
    }
    ```
    
    Response includes:
    - Complete workflow plan (steps, tools, estimates)
    - Execution trace (what actually happened)
    - Reasoning steps (Poindexter's thought process)
    - Metrics (cost, time, quality)
    - Full error details if anything failed
    """
    
    if _poindexter_instance is None:
        raise HTTPException(
            status_code=503,
            detail="Poindexter not initialized. Check system health."
        )
    
    workflow_id = str(uuid.uuid4())
    
    try:
        logger.info(f"[ORCHESTRATE] Starting workflow {workflow_id}")
        logger.info(f"[ORCHESTRATE] Command: {request.command}")
        
        # Convert constraints to dict format Poindexter expects
        constraints_dict = {}
        if request.constraints:
            for constraint in request.constraints:
                constraints_dict[constraint.name] = constraint.value
        
        # Execute orchestration
        if request.background:
            # Start in background
            logger.info(f"[ORCHESTRATE] Running workflow {workflow_id} in background")
            background_tasks.add_task(
                _execute_orchestration_background,
                workflow_id,
                request.command,
                constraints_dict,
                request.context
            )
            
            # Return immediate response
            return OrchestrationResponse(
                workflow_id=workflow_id,
                status="in_progress",
                total_time=0.0,
                total_cost=0.0,
                created_at=datetime.now(),
                error="Background execution initiated. Poll status endpoint for results."
            )
        else:
            # Execute synchronously
            orchestration_result = await _poindexter_instance.orchestrate(
                command=request.command,
                constraints=constraints_dict,
                context=request.context
            )
            
            # Build response
            response = _build_orchestration_response(
                workflow_id,
                orchestration_result
            )
            
            _workflows[workflow_id] = response
            logger.info(f"[ORCHESTRATE] Completed workflow {workflow_id}")
            
            return response
    
    except Exception as e:
        logger.error(f"[ORCHESTRATE] Error in workflow {workflow_id}: {e}", exc_info=True)
        
        response = OrchestrationResponse(
            workflow_id=workflow_id,
            status="error",
            error=str(e),
            total_time=0.0,
            total_cost=0.0,
            created_at=datetime.now()
        )
        
        _workflows[workflow_id] = response
        return response


# ============================================================================
# WORKFLOW STATUS ENDPOINT
# ============================================================================

@poindexter_router.get(
    "/orchestrate/{workflow_id}",
    response_model=OrchestrationResponse,
    summary="Get workflow status and results",
    description="Retrieve the status and results of a previously started orchestration."
)
async def get_workflow_status(workflow_id: str) -> OrchestrationResponse:
    """Get status of a workflow (for background execution)."""
    
    if workflow_id not in _workflows:
        raise HTTPException(
            status_code=404,
            detail=f"Workflow {workflow_id} not found"
        )
    
    return _workflows[workflow_id]


# ============================================================================
# ORCHESTRATION STATUS ENDPOINT
# ============================================================================

@poindexter_router.get(
    "/orchestrate-status",
    response_model=HealthResponse,
    summary="System health and readiness"
)
async def get_orchestration_status() -> HealthResponse:
    """
    Check if Poindexter system is ready to orchestrate.
    
    Returns:
    - status: "healthy" or "degraded"
    - Individual component statuses
    - Available agents
    """
    
    try:
        # Check if Poindexter is initialized
        poindexter_ready = _poindexter_instance is not None
        
        # Check smolagents availability
        smolagents_available = _check_smolagents()
        
        # Check MCP integration
        mcp_available = _poindexter_instance.mcp_integration is not None if poindexter_ready else False
        
        # Check model router
        model_router_available = _poindexter_instance.model_router is not None if poindexter_ready else False
        
        # Get list of available agents
        agents = ["research", "generate", "critique", "publish", "track_metrics", "fetch_images"]
        
        # Overall status
        all_ready = poindexter_ready and smolagents_available and mcp_available and model_router_available
        status = "healthy" if all_ready else "degraded"
        
        return HealthResponse(
            status=status,
            poindexter_ready=poindexter_ready,
            smolagents_available=smolagents_available,
            mcp_available=mcp_available,
            model_router_available=model_router_available,
            agents_available=agents
        )
    
    except Exception as e:
        logger.error(f"[HEALTH] Error checking status: {e}")
        return HealthResponse(
            status="error",
            poindexter_ready=False,
            smolagents_available=False,
            mcp_available=False,
            model_router_available=False,
            agents_available=[]
        )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _check_smolagents() -> bool:
    """Check if smolagents is available."""
    try:
        import smolagents
        return True
    except ImportError:
        return False


def _build_orchestration_response(
    workflow_id: str,
    orchestration_result: Any
) -> OrchestrationResponse:
    """Convert Poindexter orchestration result to API response."""
    
    trace = orchestration_result.workflow_trace
    
    # Build workflow steps
    workflow_planned = [
        WorkflowStep(
            step=step["step"],
            tool=step["tool"],
            description=step["description"],
            estimated_cost=step["estimated_cost"],
            estimated_time=step["estimated_time"],
            input_params=step["input"]
        )
        for step in trace.get("workflow_planned", [])
    ]
    
    # Build reasoning steps
    reasoning_steps = [
        ReasoningStep(
            step=step.get("step", ""),
            input=step.get("input"),
            reasoning=step.get("reasoning", ""),
            output=step.get("output")
        )
        for step in trace.get("reasoning_steps", [])
    ]
    
    # Calculate completed time
    completed_at = None
    if trace["status"] in ["success", "error", "partial"]:
        completed_at = datetime.now()
    
    return OrchestrationResponse(
        workflow_id=workflow_id,
        status=orchestration_result.status,
        result=orchestration_result.result,
        error=orchestration_result.error,
        workflow_planned=workflow_planned,
        workflow_executed=trace.get("workflow_executed", []),
        reasoning_trace=reasoning_steps,
        total_time=trace.get("total_time", 0.0),
        total_cost=trace.get("total_cost", 0.0),
        tools_used=trace.get("tools_used", []),
        critique_loops=trace.get("critique_loops", 0),
        created_at=datetime.now(),
        completed_at=completed_at
    )


async def _execute_orchestration_background(
    workflow_id: str,
    command: str,
    constraints: Dict,
    context: Optional[Dict]
):
    """Execute orchestration in background task."""
    try:
        logger.info(f"[BG] Executing workflow {workflow_id} in background")
        
        orchestration_result = await _poindexter_instance.orchestrate(
            command=command,
            constraints=constraints,
            context=context
        )
        
        response = _build_orchestration_response(
            workflow_id,
            orchestration_result
        )
        
        _workflows[workflow_id] = response
        logger.info(f"[BG] Background workflow {workflow_id} completed")
    
    except Exception as e:
        logger.error(f"[BG] Background workflow {workflow_id} failed: {e}")
        
        response = OrchestrationResponse(
            workflow_id=workflow_id,
            status="error",
            error=str(e),
            total_time=0.0,
            total_cost=0.0,
            created_at=datetime.now()
        )
        
        _workflows[workflow_id] = response


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

"""
Example workflow using the Poindexter API:

1. User sends command:
   POST /api/v2/orchestrate
   {
       "command": "Create and publish a blog post about machine learning",
       "constraints": [
           {"name": "budget", "value": 0.50, "unit": "USD"},
           {"name": "quality_threshold", "value": 0.90},
           {"name": "max_runtime", "value": 300, "unit": "seconds"}
       ]
   }

2. Poindexter reasons:
   - Intent: Create and publish content
   - Needed capabilities: research, generate, critique, publish
   - Best workflow:
     1. Research machine learning topics
     2. Generate blog post
     3. Critique quality
     4. Refine if needed
     5. Publish to Strapi

3. Response includes:
   {
       "workflow_id": "uuid-here",
       "status": "success",
       "result": {...},
       "workflow_planned": [...],
       "workflow_executed": [...],
       "reasoning_trace": [...],
       "total_time": 87.5,
       "total_cost": 0.35
   }

4. User can track progress:
   GET /api/v2/orchestrate/{workflow_id}

5. Check system status:
   GET /api/v2/orchestrate-status
"""
