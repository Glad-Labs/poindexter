"""Topic-batch routes — HTTP mirror of ``TopicBatchService`` (#1343).

Operator surfaces previously only reachable in-process via CLI/MCP tools that
called ``_api()`` (expecting these routes to exist):

- ``GET  /api/topic-batches/{batch_id}``               → show batch + candidates
- ``POST /api/topic-batches/{batch_id}/rank``           → set operator_rank order
- ``POST /api/topic-batches/{batch_id}/edit-winner``    → edit rank-1 candidate
- ``POST /api/topic-batches/{batch_id}/resolve``        → hand winner to pipeline
- ``POST /api/topic-batches/{batch_id}/reject``         → discard the batch
"""

import dataclasses
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)
router = APIRouter(prefix="/api/topic-batches", tags=["topic-batches"])


def _batch_view_to_dict(view: Any) -> dict[str, Any]:
    """Convert a BatchView dataclass (with UUID fields) to a JSON-safe dict."""
    d = dataclasses.asdict(view)
    # UUIDs become strings so FastAPI can serialise them without a custom encoder.
    for field in ("id", "niche_id", "picked_candidate_id"):
        if d.get(field) is not None:
            d[field] = str(d[field])
    return d


def _parse_batch_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid batch_id: {raw}") from exc


class RankBatchRequest(BaseModel):
    ordered_candidate_ids: list[str]


class EditWinnerRequest(BaseModel):
    topic: str | None = None
    angle: str | None = None


class RejectBatchRequest(BaseModel):
    reason: str = ""


@router.get(
    "/{batch_id}",
    summary="Show a topic batch and its ranked candidates",
    response_model=dict[str, Any],
    status_code=200,
)
async def show_batch(
    batch_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Return the unified, ranked view of the batch and all candidates."""
    from services.topic_batch_service import TopicBatchService

    bid = _parse_batch_id(batch_id)
    svc = TopicBatchService(pool=db_service.pool, site_config=site_config)
    try:
        view = await svc.show_batch(batch_id=bid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _batch_view_to_dict(view)


@router.post(
    "/{batch_id}/rank",
    summary="Set operator_rank on batch candidates",
    response_model=dict[str, Any],
    status_code=200,
)
async def rank_batch(
    batch_id: str,
    body: RankBatchRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Assign operator ranks to candidates in the order provided."""
    from services.topic_batch_service import TopicBatchService

    bid = _parse_batch_id(batch_id)
    svc = TopicBatchService(pool=db_service.pool, site_config=site_config)
    try:
        await svc.rank_batch(batch_id=bid, ordered_candidate_ids=body.ordered_candidate_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "batch_id": batch_id}


@router.post(
    "/{batch_id}/edit-winner",
    summary="Edit the rank-1 candidate's topic or angle",
    response_model=dict[str, Any],
    status_code=200,
)
async def edit_winner(
    batch_id: str,
    body: EditWinnerRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Edit the operator_edited_topic / operator_edited_angle on the rank-1 candidate."""
    from services.topic_batch_service import TopicBatchService

    bid = _parse_batch_id(batch_id)
    svc = TopicBatchService(pool=db_service.pool, site_config=site_config)
    try:
        await svc.edit_winner(batch_id=bid, topic=body.topic, angle=body.angle)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "batch_id": batch_id}


@router.post(
    "/{batch_id}/resolve",
    summary="Resolve the batch — hand the rank-1 winner to the content pipeline",
    response_model=dict[str, Any],
    status_code=200,
)
async def resolve_batch(
    batch_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Resolve the batch: queue the rank-1 candidate as a pipeline task."""
    from services.topic_batch_service import TopicBatchService

    bid = _parse_batch_id(batch_id)
    svc = TopicBatchService(pool=db_service.pool, site_config=site_config)
    try:
        await svc.resolve_batch(batch_id=bid)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "batch_id": batch_id}


@router.post(
    "/{batch_id}/reject",
    summary="Reject (expire) the entire batch",
    response_model=dict[str, Any],
    status_code=200,
)
async def reject_batch(
    batch_id: str,
    body: RejectBatchRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Reject the batch — sets status to ``expired``."""
    from services.topic_batch_service import TopicBatchService

    bid = _parse_batch_id(batch_id)
    svc = TopicBatchService(pool=db_service.pool, site_config=site_config)
    await svc.reject_batch(batch_id=bid, reason=body.reason)
    return {"ok": True, "batch_id": batch_id}
