"""Brain daemon observability routes.

Thin adapter over services.brain_stats — no SQL here (transport-adapter
contract, ADR 2026-06-10, #1340). The brain daemon writes brain_decisions and
brain_knowledge; this route reads aggregates so the operator console and
Grafana can surface brain activity without touching the DB directly.

brain_queue was dropped in migration 0080 (2026-04-21); not referenced here.
"""

from fastapi import APIRouter, Depends

from middleware.api_token_auth import verify_api_token
from services.brain_stats import get_brain_stats
from utils.route_utils import get_database_dependency

router = APIRouter(
    prefix="/api/brain",
    tags=["brain"],
    dependencies=[Depends(verify_api_token)],
)


@router.get("/stats")
async def get_brain_stats_route(
    db=Depends(get_database_dependency),
):
    """Aggregate brain daemon activity.

    Returns decisions_24h/7d, avg confidence, last cycle timestamp,
    knowledge entry count, and the 10 most recent decision rows.
    """
    pool = getattr(db, "cloud_pool", None) or db.pool
    return await get_brain_stats(pool)
