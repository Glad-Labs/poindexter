"""Langfuse trace proxy — ``GET /api/traces`` for the operator console.

Thin serializer over :func:`services.traces_read.read_traces`. Reads the Langfuse
keys as secrets (server-side only) and fails loud with a 503 when they're unset.
No SQL — it's an HTTP proxy.
"""
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from services.traces_read import LangfuseNotConfigured, read_traces
from utils.route_utils import get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/traces",
    tags=["traces"],
    dependencies=[Depends(verify_api_token)],
)


@router.get(
    "", response_model=dict[str, Any], summary="Recent Langfuse LLM traces for the console"
)
async def list_traces(
    request: Request,
    token: str = Depends(verify_api_token),
    site_config: Any = Depends(get_site_config_dependency),
    hours: int = Query(24, ge=1, le=720, description="Look-back window in hours"),
    limit: int = Query(50, ge=1, le=200, description="Max traces"),
    task_id: str = Query("", description="Scope to one pipeline task (Langfuse sessionId)"),
) -> dict[str, Any]:
    host = site_config.get("langfuse_host", "")
    public_key = await site_config.get_secret("langfuse_public_key", "")
    secret_key = await site_config.get_secret("langfuse_secret_key", "")
    client = getattr(request.app.state, "http_client", None)
    try:
        if client is not None:
            return await read_traces(
                client,
                host=host,
                public_key=public_key,
                secret_key=secret_key,
                hours=hours,
                limit=limit,
                task_id=task_id,
            )
        async with httpx.AsyncClient() as c:
            return await read_traces(
                c,
                host=host,
                public_key=public_key,
                secret_key=secret_key,
                hours=hours,
                limit=limit,
                task_id=task_id,
            )
    except LangfuseNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
