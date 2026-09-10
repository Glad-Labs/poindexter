"""Approval-gate admin routes — HTTP mirror of ``approval_service`` (#1343).

Operator surfaces that were previously only reachable in-process via CLI/MCP:

- ``GET  /api/gates``                   → list all known gates + pending counts
- ``PATCH /api/gates/{gate_name}``      → enable or disable a gate
- ``GET  /api/gates/pending``           → list tasks paused at any gate
- ``GET  /api/gates/pending/{task_id}`` → inspect a single paused task
- ``POST /api/gates/pending/{task_id}/approve`` → approve + resume the graph
  (202 — the checkpoint resume runs in the background; see
  ``services.gate_resume``). This is the console NEEDS-YOU gate lane's action;
  the CLI equivalent is ``poindexter pipeline resume <task_id>``.
- ``POST /api/gates/pending/{task_id}/reject``  → reject the paused task
  (``poindexter reject`` equivalent; per-gate rejection handlers fire, e.g.
  seo_refresh_gate dismisses its linked seo_opportunities row)
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from schemas.task_schemas import GateListResponse, GatePausedListResponse
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)
router = APIRouter(
    prefix="/api/gates",
    tags=["gates"],
    # Operator surface — auth enforced on every route (poindexter#752 item 2).
    dependencies=[Depends(verify_api_token)],
)


class SetGateRequest(BaseModel):
    enabled: bool


class GateApproveRequest(BaseModel):
    """Optional operator note recorded on the approval's gate_history row."""

    feedback: str | None = None


class GateRejectRequest(BaseModel):
    """Optional veto reason recorded on the rejection's gate_history row."""

    reason: str | None = None


@router.get(
    "",
    summary="List all known approval gates",
    response_model=GateListResponse,
    status_code=200,
)
async def list_gates(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> GateListResponse:
    """Return every gate the system has ever heard of, plus its enabled state
    and the number of tasks currently paused on it."""
    from services.approval_service import list_gates as _list_gates

    gates = await _list_gates(pool=db_service.pool, site_config=site_config)
    # Canonical offset envelope (poindexter#745): `gates` → `items`. This is a
    # full unpaginated enumeration, so offset is 0 and limit == total (the whole
    # set is returned). Pydantic validates each row into a GateItem.
    return GateListResponse(
        items=gates,  # type: ignore[arg-type]
        total=len(gates),
        limit=len(gates),
        offset=0,
    )


@router.patch(
    "/{gate_name}",
    summary="Enable or disable an approval gate",
    response_model=dict[str, Any],
    status_code=200,
)
async def set_gate_enabled(
    gate_name: str,
    body: SetGateRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Toggle the ``pipeline_gate_<gate_name>`` app_settings row."""
    from services.approval_service import set_gate_enabled as _set_gate_enabled

    return await _set_gate_enabled(
        gate_name=gate_name,
        enabled=body.enabled,
        pool=db_service.pool,
        site_config=site_config,
    )


@router.get(
    "/pending",
    summary="List tasks currently paused at an approval gate",
    response_model=GatePausedListResponse,
    status_code=200,
)
async def list_pending(
    gate_name: str | None = Query(None, description="Filter to a specific gate"),
    limit: int = Query(100, ge=1, le=500),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> GatePausedListResponse:
    """Return every task currently paused at any gate (or one gate)."""
    from services.approval_service import list_pending as _list_pending

    tasks = await _list_pending(pool=db_service.pool, gate_name=gate_name, limit=limit)
    # Canonical offset envelope (poindexter#745): `tasks` → `items`. The list is
    # `limit`-capped with no cursor, so offset is always 0. Pydantic validates
    # each row into a GatePausedTaskItem.
    return GatePausedListResponse(
        items=tasks,  # type: ignore[arg-type]
        total=len(tasks),
        limit=limit,
        offset=0,
    )


@router.post(
    "/pending/{task_id}/approve",
    summary="Approve a paused task's gate and resume its graph",
    response_model=dict[str, Any],
    status_code=202,
)
async def approve_pending(
    task_id: str,
    body: GateApproveRequest | None = None,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Record the approval and schedule the LangGraph checkpoint resume.

    Returns 202 immediately (``mode='approve_resume_started'``) — the resume
    runs as a background task on the worker. If it fails, the approval is
    rolled back and the task reappears in ``GET /api/gates/pending`` (the
    console's next poll picks it up); the operator gets a Discord note either
    way. HTTP mirror of ``poindexter pipeline resume <task_id>``.
    """
    from services.approval_service import (
        ApprovalServiceError,
        TaskNotFoundError,
        TaskNotPausedError,
    )
    from services.gate_resume import (
        ResumeInFlightError,
        approve_and_schedule_resume,
    )

    try:
        return await approve_and_schedule_resume(
            task_id=task_id,
            feedback=(body.feedback if body else None),
            actor="human",
            db_service=db_service,
            site_config=site_config,
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TaskNotPausedError, ResumeInFlightError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApprovalServiceError as exc:
        # e.g. no template_slug to resume — a data problem, not a race.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/pending/{task_id}/reject",
    summary="Reject a task paused at an approval gate",
    response_model=dict[str, Any],
    status_code=200,
)
async def reject_pending(
    task_id: str,
    body: GateRejectRequest | None = None,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Reject the active gate on a paused task (HTTP mirror of ``poindexter
    reject``). Per-gate rejection handlers fire — for ``seo_refresh_gate``
    that dismisses the linked ``seo_opportunities`` row so it is never
    re-proposed."""
    from services.approval_service import (
        GateMismatchError,
        TaskNotFoundError,
        TaskNotPausedError,
    )
    from services.approval_service import (
        reject as _reject,
    )

    try:
        return await _reject(
            task_id=task_id,
            gate_name=None,
            reason=(body.reason if body else None),
            actor="human",
            site_config=site_config,
            pool=db_service.pool,
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TaskNotPausedError, GateMismatchError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/pending/{task_id}",
    summary="Inspect a single task paused at an approval gate",
    response_model=dict[str, Any],
    status_code=200,
)
async def show_pending(
    task_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Return the gate state and artifact for a single paused task."""
    from services.approval_service import (
        GateMismatchError,
        TaskNotFoundError,
        TaskNotPausedError,
    )
    from services.approval_service import (
        show_pending as _show_pending,
    )

    try:
        return await _show_pending(pool=db_service.pool, task_id=task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskNotPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GateMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
