"""Operator-triggered container restart (poindexter#909).

- ``POST /api/services/{container}/restart`` → queue a restart intent
- ``GET  /api/services/restart/{request_id}`` → poll its outcome

The worker has no docker.sock — only ``poindexter-brain-daemon`` does (the
self-healing firefighter's ``docker_restart_container``). This route only
writes/reads the intent queue (``services/service_restart_requests.py``);
``brain/service_restart.py`` claims rows and does the actual restart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from services.service_restart_requests import (
    InvalidContainerName,
    SelfDefeatingRestart,
    create_restart_request,
    get_restart_request,
)
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/services",
    tags=["services"],
    dependencies=[Depends(verify_api_token)],
)


class RestartRequestResponse(BaseModel):
    id: str
    container: str
    status: str
    requested_by: str | None = None
    detail: str | None = None
    requested_at: datetime
    claimed_at: datetime | None = None
    completed_at: datetime | None = None


def _to_response(row: dict[str, Any]) -> RestartRequestResponse:
    return RestartRequestResponse(**{**row, "id": str(row["id"])})


@router.post(
    "/{container}/restart",
    response_model=RestartRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a docker restart for one container",
    responses={
        202: {"description": "Restart queued — poll GET /api/services/restart/{id} for the outcome"},
        400: {"description": "container is not a valid poindexter-* name"},
        401: {"description": "Unauthorized"},
        409: {"description": "container hosts the queue itself — restart it by hand"},
    },
)
async def post_restart(
    container: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> RestartRequestResponse:
    """Queue a restart intent. Brain's poll loop claims + executes it — this
    route never touches docker directly (the worker has no docker.sock)."""
    del token
    try:
        row = await create_restart_request(db_service.pool, container)
    except InvalidContainerName as e:
        # Interpolate the request's OWN `container` path param, not the
        # exception `e` — str(e) happens to equal it today, but keying on the
        # request-scoped variable (not the caught exception) is what the
        # HTTP detail-leak lint (scripts/ci/lint_http_detail_leak.py,
        # poindexter#724) is designed to allow: this echoes back exactly what
        # the caller already sent, nothing internal.
        logger.warning("[service_restart] rejected invalid container name: %r", container)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{container}' is not a valid poindexter-* container name",
        ) from e
    except SelfDefeatingRestart as e:
        # Same detail-leak posture as above: echo the caller's own path param.
        # 409 (not 400) — the name is well-formed and the container is real;
        # the request conflicts with the queue's own liveness requirement.
        logger.warning(
            "[service_restart] refused self-defeating restart of %r "
            "(would orphan its own intent row)", container,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{container}' cannot be restarted through this queue — it "
                f"hosts the queue itself, so the restart would orphan its own "
                f"request row. Restart it by hand: docker restart {container}"
            ),
        ) from e
    logger.info("[service_restart] queued restart for %s (id=%s)", container, row["id"])
    return _to_response(row)


@router.get(
    "/restart/{request_id}",
    response_model=RestartRequestResponse,
    summary="Poll a queued restart's outcome",
    responses={
        200: {"description": "Current status of the restart request"},
        404: {"description": "No such restart request"},
    },
)
async def get_restart_status(
    request_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> RestartRequestResponse:
    del token
    row = await get_restart_request(db_service.pool, request_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="restart request not found")
    return _to_response(row)
