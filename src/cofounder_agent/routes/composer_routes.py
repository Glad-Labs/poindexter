"""
Process Composer API Routes — execute business processes from intent.

POST /api/compose/plan — Create an execution plan (propose)
POST /api/compose/execute — Execute immediately (trusted)
POST /api/compose/approve/{plan_id} — Approve a pending plan
GET  /api/compose/steps — List available building blocks
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from services.process_composer import create_default_composer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/compose", tags=["composer"], dependencies=[Depends(verify_api_token)])

# In-memory plan store (production would use DB)
_pending_plans: dict = {}


class ComposeRequest(BaseModel):
    intent: str
    context: Optional[dict] = None


class ApproveRequest(BaseModel):
    approve: bool = True
    reason: Optional[str] = None


def _get_composer(request: Request):
    settings = getattr(request.app.state, "settings_service", None)
    return create_default_composer(settings_service=settings)


@router.post("/plan")
async def create_plan(req: ComposeRequest, request: Request):
    """Create an execution plan from intent — returns plan for review."""
    composer = _get_composer(request)
    plan = await composer.plan(req.intent, req.context)
    _pending_plans[plan.plan_id] = plan
    return {
        "plan_id": plan.plan_id,
        "process_name": plan.process_name,
        "intent": plan.intent,
        "reason": plan.reason,
        "status": plan.status,
        "steps": plan.steps,
        "summary": plan.summary,
    }


@router.post("/approve/{plan_id}")
async def approve_plan(plan_id: str, req: ApproveRequest, request: Request):
    """Approve or reject a pending plan, then execute if approved."""
    plan = _pending_plans.get(plan_id)
    if not plan:
        return {"error": f"Plan {plan_id} not found"}

    if not req.approve:
        plan.reject(req.reason or "")
        return {"plan_id": plan_id, "status": "rejected", "reason": req.reason}

    plan.approve()
    composer = _get_composer(request)
    result = await composer.execute_plan(plan)
    del _pending_plans[plan_id]

    return {
        "plan_id": plan_id,
        "status": "executed",
        "success": result.success,
        "summary": result.summary,
        "steps": [
            {"name": s.step_name, "success": s.success, "output": s.output, "error": s.error, "duration_ms": s.duration_ms}
            for s in result.steps
        ],
    }


@router.post("/execute")
async def execute_immediately(req: ComposeRequest, request: Request):
    """Execute a process immediately — skip approval (for trusted/automated use)."""
    composer = _get_composer(request)
    result = await composer.execute(req.intent, req.context)
    return {
        "process_name": result.process_name,
        "intent": result.intent,
        "success": result.success,
        "summary": result.summary,
        "steps": [
            {"name": s.step_name, "success": s.success, "output": s.output, "error": s.error, "duration_ms": s.duration_ms}
            for s in result.steps
        ],
    }


@router.get("/steps")
async def list_steps(request: Request):
    """List all available building block steps."""
    composer = _get_composer(request)
    return {"steps": composer.list_steps(), "processes": composer.list_processes()}
