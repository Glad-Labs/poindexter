"""Operator-console QA read routes. Thin adapter over services.qa_trend
(adapter-purity ADR: no inline SQL here)."""

from typing import Any

from fastapi import APIRouter, Depends, Query

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from services.qa_trend import get_qa_pass_trend
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/qa",
    tags=["qa"],
    # Operator surface — auth enforced on every route.
    dependencies=[Depends(verify_api_token)],
)


@router.get("/trend", response_model=dict[str, Any])
async def qa_trend(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    range_seconds: int = Query(21600, ge=60, le=604800),
    step_seconds: int = Query(90, ge=15),
) -> dict[str, Any]:
    """QA pass-rate time-series over ``audit_log`` for the console History panel."""
    return await get_qa_pass_trend(
        db_service.pool, range_seconds=range_seconds, step_seconds=step_seconds
    )
