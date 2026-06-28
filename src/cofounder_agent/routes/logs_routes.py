"""Loki log proxy — ``GET /api/logs`` for the operator console.

Thin serializer over :func:`services.logs_read.read_logs` (the HTTP-proxy logic
lives there). Mirrors ``routes/findings_routes.py``: the route does auth + param
validation, the read-service does the work. No SQL — it's an HTTP proxy.
"""
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from services.logs_read import read_logs
from utils.route_utils import get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/logs",
    tags=["logs"],
    dependencies=[Depends(verify_api_token)],
)


@router.get("", response_model=dict[str, Any], summary="Loki log tail/search for the console")
async def list_logs(
    request: Request,
    token: str = Depends(verify_api_token),
    site_config: Any = Depends(get_site_config_dependency),
    query: str = Query("", description="Full LogQL selector; overrides service/level"),
    service: str = Query("", description="Filter by the `service` Loki label"),
    level: str = Query("", description="Filter by the `level` Loki label"),
    since: str = Query("1h", description="Look-back window (Loki duration, e.g. 1h, 30m)"),
    limit: int = Query(500, ge=1, le=1000, description="Max log lines"),
) -> dict[str, Any]:
    loki_url = site_config.get("data_fabric_loki_url", "http://loki:3100")
    client = getattr(request.app.state, "http_client", None)
    if client is not None:
        return await read_logs(
            client,
            loki_url=loki_url,
            query=query,
            service=service,
            level=level,
            since=since,
            limit=limit,
        )
    async with httpx.AsyncClient() as c:
        return await read_logs(
            c,
            loki_url=loki_url,
            query=query,
            service=service,
            level=level,
            since=since,
            limit=limit,
        )
