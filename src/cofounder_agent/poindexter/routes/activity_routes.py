"""Live-activity route — the console 'what is the system doing now' pulse.

``GET /api/activity`` returns the currently-running work (jobs / content /
media / brain, heartbeat within the freshness window), a recent-finished
trail, and a per-kind running summary. The read lives in
:func:`services.live_activity.get_live_activity` so the route stays a thin
serializer (mirrors the findings / topic-triage routes).
"""

from typing import Any

from fastapi import APIRouter, Depends

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.live_activity import get_live_activity
from services.logger_config import get_logger
from services.site_config import SiteConfig
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/activity",
    tags=["activity"],
    # Operator surface — auth enforced on every route.
    dependencies=[Depends(verify_api_token)],
)


async def _read_activity(pool: Any, *, site_config: SiteConfig) -> dict:
    """Thin adapter — resolve the tunable window/limit from settings and
    delegate to the service read. Kept separate so a test can drive it with
    a real pool + a stub config without standing up the full HTTP stack.
    """
    return await get_live_activity(
        pool,
        freshness_seconds=int(site_config.get("live_activity_freshness_seconds", "120")),
        recent_limit=int(site_config.get("live_activity_recent_limit", "20")),
    )


@router.get(
    "",
    response_model=dict[str, Any],
    summary="Live system activity — running work + recent trail + summary",
)
async def activity(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Return ``{running[], recent[], summary{running_by_kind}}`` for the console pulse."""
    return await _read_activity(db_service.pool, site_config=site_config)
