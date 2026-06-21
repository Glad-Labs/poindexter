"""Approval-gate admin routes — HTTP mirror of ``approval_service`` (#1343).

Operator surfaces that were previously only reachable in-process via CLI/MCP:

- ``GET  /api/gates``                   → list all known gates + pending counts
- ``PATCH /api/gates/{gate_name}``      → enable or disable a gate
- ``GET  /api/gates/pending``           → list tasks paused at any gate
- ``GET  /api/gates/pending/{task_id}`` → inspect a single paused task
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from schemas.task_schemas import GatePausedListResponse
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


@router.get(
    "",
    summary="List all known approval gates",
    response_model=dict[str, Any],
    status_code=200,
)
async def list_gates(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Return every gate the system has ever heard of, plus its enabled state
    and the number of tasks currently paused on it."""
    from services.approval_service import list_gates as _list_gates

    gates = await _list_gates(pool=db_service.pool, site_config=site_config)
    return {"gates": gates, "total": len(gates)}


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
