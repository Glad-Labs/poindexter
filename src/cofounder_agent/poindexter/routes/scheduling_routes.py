"""Scheduling routes — HTTP mirror of ``scheduling_service`` (#1343).

Operator surfaces previously only reachable in-process:

- ``GET    /api/scheduling``           → list scheduled posts
- ``GET    /api/scheduling/{post_id}`` → show one post's schedule
- ``POST   /api/scheduling/{post_id}`` → assign a single publish slot
- ``POST   /api/scheduling/batch``     → batch-assign slots to N approved posts
- ``PATCH  /api/scheduling/shift``     → shift schedule(s) by a duration
- ``DELETE /api/scheduling``           → clear schedule(s)
"""

import dataclasses
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)
router = APIRouter(
    prefix="/api/scheduling",
    tags=["scheduling"],
    # Operator surface — auth enforced on every route (poindexter#752 item 2).
    dependencies=[Depends(verify_api_token)],
)


def _result_to_dict(result: Any) -> dict[str, Any]:
    """Convert a ScheduleResult dataclass to a JSON-serialisable dict."""
    d = dataclasses.asdict(result)
    # Convert any datetime objects inside rows to ISO strings
    for row in d.get("rows", []):
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    return d


class AssignSlotRequest(BaseModel):
    when: str
    force: bool = False


class AssignBatchRequest(BaseModel):
    count: int
    interval: str
    start: str
    quiet_hours: str | None = None
    ordered_by: str = "approved_at"
    force: bool = False


class ShiftRequest(BaseModel):
    by_delta: str
    post_ids: list[str] | None = None


@router.get(
    "",
    summary="List scheduled posts",
    response_model=dict[str, Any],
    status_code=200,
)
async def list_scheduled(
    upcoming_only: bool = Query(True, description="Only return future slots"),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Return all scheduled posts, defaulting to future-only."""
    from services.scheduling_service import list_scheduled as _list

    result = await _list(pool=db_service.pool, upcoming_only=upcoming_only)
    return _result_to_dict(result)


@router.get(
    "/{post_id}",
    summary="Show the schedule for a single post",
    response_model=dict[str, Any],
    status_code=200,
)
async def show_scheduled(
    post_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Return the schedule detail for one post. Returns ``ok=false`` when
    the post is not found rather than 404 to match the service contract."""
    from services.scheduling_service import show_scheduled as _show

    result = await _show(post_id, pool=db_service.pool)
    return _result_to_dict(result)


@router.post(
    "/batch",
    summary="Batch-assign publish slots to approved posts",
    response_model=dict[str, Any],
    status_code=200,
)
async def assign_batch(
    body: AssignBatchRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Assign sequential publish slots to the next N approved posts.

    ``interval`` and ``start`` are duration / datetime strings as accepted
    by ``scheduling_service.parse_duration`` and ``parse_when``.
    """
    from services.scheduling_service import assign_batch as _assign_batch

    try:
        result = await _assign_batch(
            count=body.count,
            interval=body.interval,
            start=body.start,
            quiet_hours=body.quiet_hours,
            ordered_by=body.ordered_by,
            pool=db_service.pool,
            site_config=site_config,
            force=body.force,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _result_to_dict(result)


@router.post(
    "/{post_id}",
    summary="Assign a publish slot to a single post",
    response_model=dict[str, Any],
    status_code=200,
)
async def assign_slot(
    post_id: str,
    body: AssignSlotRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Assign a single post to a specific publish time.

    ``when`` is a datetime string accepted by ``scheduling_service.parse_when``.
    Pass ``force=true`` to overwrite an existing schedule.
    """
    from services.scheduling_service import assign_slot as _assign

    try:
        result = await _assign(
            post_id,
            body.when,
            pool=db_service.pool,
            site_config=site_config,
            force=body.force,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _result_to_dict(result)


@router.patch(
    "/shift",
    summary="Shift one or all scheduled posts by a duration",
    response_model=dict[str, Any],
    status_code=200,
)
async def shift(
    body: ShiftRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Shift the schedule of one or more posts by ``by_delta``.

    ``by_delta`` is a duration string (e.g. ``"1h30m"``).
    When ``post_ids`` is omitted every still-future scheduled post is shifted.
    """
    from services.scheduling_service import shift as _shift

    try:
        result = await _shift(
            by_delta=body.by_delta,
            post_ids=body.post_ids,
            pool=db_service.pool,
            site_config=site_config,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _result_to_dict(result)


@router.delete(
    "",
    summary="Clear the schedule on one or all scheduled posts",
    response_model=dict[str, Any],
    status_code=200,
)
async def clear(
    post_ids: list[str] | None = Query(None, description="Post IDs to clear; omit to clear all future-dated scheduled posts"),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Clear (unschedule) one or more posts, returning them to ``approved`` status.

    When ``post_ids`` is omitted every future-dated scheduled post is cleared.
    """
    from services.scheduling_service import clear as _clear

    result = await _clear(
        post_ids=post_ids,
        pool=db_service.pool,
        site_config=site_config,
    )
    return _result_to_dict(result)
