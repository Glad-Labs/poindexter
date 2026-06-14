"""Findings-triage route — probe-findings summary for the operator console.

``GET /api/findings`` exposes the same data as the Findings Grafana dashboard
(#461) and the ``findings_list`` MCP tool: ``audit_log`` rows where
``event_type='finding'``, rolled up into emitted/pending counts, by-kind and
by-severity breakdowns, and per-finding routed/pending status + delivery policy.

The read lives in :func:`services.findings_read.read_findings` so the route
stays a thin, single-collaborator serializer (mirrors the topic-triage routes).
"""

from typing import Any

from fastapi import APIRouter, Depends, Query

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.findings_read import read_findings
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)

router = APIRouter(prefix="/api/findings", tags=["findings"])


@router.get(
    "",
    response_model=dict[str, Any],
    summary="Probe-findings summary — counts, by-kind/severity, routing status",
)
async def list_findings(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    kind: str = Query("", description="Filter by finding kind (e.g. broken_external_link)"),
    severity: str = Query("", description="Filter by severity (warn/warning, critical, info)"),
    hours: int = Query(168, ge=1, le=720, description="Look-back window in hours"),
    pending_only: bool = Query(
        False, description="Only routable findings above the route watermark"
    ),
    limit: int = Query(30, ge=1, le=100, description="Max detail rows"),
) -> dict[str, Any]:
    """Return the structured findings summary the console Findings panel renders.

    Mirrors the ``findings_list`` MCP tool but as JSON for the operator console:
    ``{findings[], counts{emitted,pending}, by_kind[], by_severity[],
    delivery_by_kind{}, watermark, hours}``.
    """
    return await read_findings(
        db_service.pool,
        kind=kind,
        severity=severity,
        hours=hours,
        pending_only=pending_only,
        limit=limit,
    )
