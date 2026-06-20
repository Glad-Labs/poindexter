"""Declarative data-plane CRUD routes — HTTP mirror of
``declarative_config_service`` (#1522, epic #1340).

Closes the last HTTP-coverage gap (Category C): the 5 declarative data-plane
tables (taps / retention / webhooks / publishers / qa-gates) were previously
reachable only through the CLI's raw SQL. These thin, OAuth-protected routes
mirror the service so a remote / cloud-coordinator / SaaS consumer reaches the
same surface. Every handler delegates to the service — no logic, no SQL here.

- ``GET    /api/data-plane/{surface}``       → list every row
- ``GET    /api/data-plane/{surface}/{key}`` → one row (404 if missing)
- ``PUT    /api/data-plane/{surface}/{key}`` → create or update (path key wins)
- ``DELETE /api/data-plane/{surface}/{key}`` → delete (404 if missing)
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
router = APIRouter(
    prefix="/api/data-plane",
    tags=["data-plane"],
    # Operator surface — auth enforced on every route (poindexter#752 item 2).
    dependencies=[Depends(verify_api_token)],
)


@router.get(
    "/{surface}",
    summary="List every row of a declarative data-plane surface",
    response_model=dict[str, Any],
    status_code=200,
)
async def list_surface(
    surface: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Return all rows for ``surface`` (taps / retention / webhooks /
    publishers / qa-gates) as ``{"rows": [...], "total": n}``."""
    from services.declarative_config_service import UnknownSurfaceError, list_rows

    try:
        rows = await list_rows(db_service.pool, surface)
    except UnknownSurfaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"rows": rows, "total": len(rows)}


@router.get(
    "/{surface}/{key}",
    summary="Get one data-plane row by its natural key",
    response_model=dict[str, Any],
    status_code=200,
)
async def get_surface_row(
    surface: str,
    key: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Return the single row whose key matches ``key`` (404 if none)."""
    from services.declarative_config_service import UnknownSurfaceError, get_row

    try:
        row = await get_row(db_service.pool, surface, key)
    except UnknownSurfaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"{surface} {key!r} not found")
    return row


@router.put(
    "/{surface}/{key}",
    summary="Create or update a data-plane row",
    response_model=dict[str, Any],
    status_code=200,
)
async def upsert_surface_row(
    surface: str,
    key: str,
    body: dict[str, Any] = Body(default={}),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Upsert the row. The path ``key`` is authoritative — it's merged into the
    payload as the surface's key column so the URL and body can't disagree."""
    from services.declarative_config_service import (
        SurfaceValidationError,
        UnknownSurfaceError,
        resolve_surface,
        upsert_row,
    )

    try:
        spec = resolve_surface(surface)
        payload = {**body, spec.key_column: key}
        row = await upsert_row(db_service.pool, surface, payload)
    except UnknownSurfaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SurfaceValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "row": row}


@router.delete(
    "/{surface}/{key}",
    summary="Delete a data-plane row by its natural key",
    response_model=dict[str, Any],
    status_code=200,
)
async def delete_surface_row(
    surface: str,
    key: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Delete the row (404 if no row matched ``key``)."""
    from services.declarative_config_service import UnknownSurfaceError, delete_row

    try:
        deleted = await delete_row(db_service.pool, surface, key)
    except UnknownSurfaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"{surface} {key!r} not found")
    return {"ok": True, "deleted": True}
