"""
Task Intent Routes - Intent-based task creation (Natural Language Support).

Sub-router for task_routes.py. Handles:
- POST /intent — Parse natural language input and create execution plan
- POST /confirm-intent — Confirm execution plan and create task
"""

import uuid as uuid_lib
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from middleware.api_token_auth import verify_api_token
from schemas.task_schemas import (
    IntentTaskRequest,
    TaskConfirmRequest,
    TaskConfirmResponse,
    TaskIntentResponse,
)
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)

intent_router = APIRouter(tags=["Task Intent"])


# ============================================================================
# PHASE 1: INTENT-BASED TASK CREATION (Natural Language Support)
# ============================================================================


@intent_router.post("/intent", response_model=TaskIntentResponse)
async def create_task_from_intent(
    request: IntentTaskRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Phase 1: Parse natural language input and create execution plan.

    This endpoint:
    1. Takes user NL input
    2. Detects intent (content_generation, social_media, etc.)
    3. Extracts parameters (topic, style, budget, deadline)
    4. Determines subtasks
    5. Creates execution plan
    6. Returns plan to UI for confirmation

    User then calls /api/tasks/confirm to execute.
    """

    try:
        from services.task_intent_router import TaskIntentRouter
        from services.task_planning_service import TaskPlanningService

        # Initialize services
        intent_router_svc = TaskIntentRouter()
        planner = TaskPlanningService()

        # Step 1: Parse NL input into TaskIntentRequest
        intent_request = await intent_router_svc.route_user_input(
            request.user_input, request.user_context or {}
        )

        logger.info(
            f"[INTENT] Detected intent: {intent_request.intent_type} → task_type: {intent_request.task_type}"
        )
        logger.info(f"[INTENT] Suggested subtasks: {intent_request.suggested_subtasks}")
        logger.info(f"[INTENT] Parameters: {intent_request.parameters}")

        # Step 2: Generate execution plan
        plan = await planner.generate_plan(intent_request, request.business_metrics or {})

        logger.info(
            f"[INTENT] Generated plan: {plan.total_estimated_duration_ms}ms, ${plan.total_estimated_cost:.2f}"
        )

        # Step 3: Convert plan to summary for UI
        plan_summary = planner.plan_to_summary(plan)

        # Serialize plan and return to client for confirmation (client-supplied confirmation flow)
        plan_dict = planner.serialize_plan(plan)

        response = TaskIntentResponse(
            task_id=None,  # No task created yet - waiting for confirmation
            intent_request={
                "intent_type": intent_request.intent_type,
                "task_type": intent_request.task_type,
                "confidence": float(intent_request.confidence),
                "parameters": intent_request.parameters,
                "suggested_subtasks": intent_request.suggested_subtasks,
                "requires_confirmation": intent_request.requires_confirmation,
                "execution_strategy": intent_request.execution_strategy,
            },
            execution_plan={
                "title": plan_summary.title,
                "description": plan_summary.description,
                "estimated_time": plan_summary.estimated_time,
                "estimated_cost": plan_summary.estimated_cost,
                "confidence": plan_summary.confidence,
                "key_stages": plan_summary.key_stages,
                "warnings": plan_summary.warnings,
                "opportunities": plan_summary.opportunities,
                "full_plan": plan_dict,  # Store full plan for confirmation
            },
            ready_to_execute=not intent_request.requires_confirmation,
            warnings=plan_summary.warnings,
        )

        logger.info("[INTENT] Response ready to send to UI")
        return response

    except Exception as e:
        logger.error(f"[INTENT] Intent parsing failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Intent parsing failed") from e


@intent_router.post("/confirm-intent", response_model=TaskConfirmResponse)
async def confirm_and_execute_task(
    request: TaskConfirmRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Phase 1: Confirm execution plan and create task.

    This endpoint:
    1. Receives confirmed execution plan from UI
    2. Creates task in database
    3. Stores execution plan in metadata
    4. Marks task as pending for execution
    5. Starts background task executor

    Task executor will follow the execution plan stages.
    """

    if not request.user_confirmed:
        raise HTTPException(status_code=400, detail="User did not confirm execution plan")

    try:
        task_id = str(uuid_lib.uuid4())
        intent_req = request.intent_request
        plan = request.execution_plan

        # Build execution metadata
        execution_metadata = {
            "intent": {
                "intent_type": intent_req.get("intent_type"),
                "task_type": intent_req.get("task_type"),
                "parameters": intent_req.get("parameters"),
            },
            "plan": plan,
            "user_confirmed": request.user_confirmed,
            "modifications": request.modifications or {},
            "created_from_intent": True,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Create task in database
        await db_service.add_task(
            {
                "id": task_id,
                "task_name": intent_req.get("parameters", {}).get("topic", "Task from Intent"),
                "task_type": intent_req.get("task_type", "generic"),
                "status": "pending",
                "metadata": execution_metadata,
            }
        )

        logger.info(
            f"[CONFIRM] Created task {task_id} from intent plan — TaskExecutor will pick up automatically"
        )

        return TaskConfirmResponse(
            task_id=task_id,
            status="pending",
            message=f"Task created and queued for execution. Plan: {len(plan.get('stages', []))} stages",
            execution_plan_id=plan.get("task_id", task_id),
        )

    except Exception as e:
        logger.error(f"[CONFIRM] Task confirmation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Task confirmation failed") from e
