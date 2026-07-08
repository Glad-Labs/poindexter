"""Operator-console task-trace read routes. Thin adapter over
``services.trace_read`` (adapter-purity ADR: no inline SQL here).

Three endpoints for the console Task Trace:

- ``GET /api/trace/active``   — the front-door board (running + recent tasks).
- ``GET /api/trace/summary``  — 24h KPI strip (cached in-process ≤60s).
- ``GET /api/trace/{task_id}``— the per-task deep-dive assembly.

Every handler guards to **honest-empty** on error, so a scattered or partial run
never makes the console throw. The literal ``/active`` and ``/summary`` routes
are declared before ``/{task_id}`` so the path-param route can't shadow them.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query

from middleware.api_token_auth import verify_api_token
from services import trace_read
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/trace",
    tags=["trace"],
    # Operator surface — auth enforced on every route.
    dependencies=[Depends(verify_api_token)],
)

# In-process TTL cache for the 24h summary: the board polls it often and the
# aggregate scans a whole day, so a short cache spares the DB without hiding
# meaningfully fresh data. Reset by the route tests.
_SUMMARY_CACHE: dict[str, Any] = {"ts": 0.0, "val": None}
_SUMMARY_TTL_S = 60.0


@router.get("/active", response_model=dict[str, Any])
async def active(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Front-door board: currently-running tasks (live progress) + recent finished."""
    limit = int(site_config.get("trace_recent_limit", "10") or 10)
    try:
        return await trace_read.get_active(db_service.pool, recent_limit=limit)
    except Exception:  # noqa: BLE001 — honest-empty; the board never throws
        logger.warning("[trace_routes] /active failed", exc_info=True)
        return {"runs": [], "recent": []}


@router.get("/summary", response_model=dict[str, Any])
async def summary(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """24h KPI strip for the board health row. Cached in-process for the TTL."""
    now = time.monotonic()
    cached = _SUMMARY_CACHE.get("val")
    if cached is not None and (now - float(_SUMMARY_CACHE.get("ts") or 0.0)) < _SUMMARY_TTL_S:
        return cached
    try:
        val = await trace_read.get_summary(db_service.pool)
    except Exception:  # noqa: BLE001
        logger.warning("[trace_routes] /summary failed", exc_info=True)
        return {"window_hours": 24, "tasks": 0, "by_status": {}}
    _SUMMARY_CACHE["val"] = val
    _SUMMARY_CACHE["ts"] = now
    return val


@router.get("/{task_id}", response_model=dict[str, Any])
async def trace(
    task_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    run_id: str = Query(
        "", description="Scope to one run of the task; empty = latest content run"
    ),
) -> dict[str, Any]:
    """The per-task deep-dive: run spine, corpus, QA verdicts, decisions, cost."""
    try:
        return await trace_read.get_trace(db_service.pool, task_id, run_id or None)
    except Exception:  # noqa: BLE001 — honest-empty deep-dive shell
        logger.warning("[trace_routes] /%s failed", task_id, exc_info=True)
        return {
            "task_id": task_id,
            "run_id": run_id or None,
            "runs": [],
            "task": None,
            "nodes": [],
            "corpus": "",
            "decisions": [],
            "qa": [],
            "cost_rollup": {"by_model": [], "total_usd": 0.0},
            "final": None,
            "halt": None,
            "langfuse": {"session_id": task_id, "run_id": run_id or None},
        }
