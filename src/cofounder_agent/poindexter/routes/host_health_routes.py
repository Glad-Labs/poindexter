"""Liveness for host processes cAdvisor cannot see.

``GET /api/services/host-health`` — thin adapter over
``services.host_service_health`` (no SQL here; transport-adapter contract, ADR
2026-06-10, #1340).

The operator console reads every container's health straight from cAdvisor, but
a host process (Ollama at :11434) has no container and no series. The brain
daemon has always probed it; this route is the read path that was missing, so
the console can stop rendering the pipeline's LLM runtime as permanently dark.
See the service module for why the answer is freshness-gated rather than just
the probe's last value.
"""

from fastapi import APIRouter, Depends

from middleware.api_token_auth import verify_api_token
from poindexter.services.host_service_health import get_host_service_health
from poindexter.services.site_config import SiteConfig
from poindexter.utils.route_utils import (
    get_database_dependency,
    get_site_config_dependency,
)

router = APIRouter(
    prefix="/api/services",
    tags=["services"],
    dependencies=[Depends(verify_api_token)],
)


@router.get("/host-health")
async def get_host_health_route(
    db=Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
):
    """Current liveness for host services, derived from brain health probes.

    Each entry carries ``status`` (``ok`` / ``err`` / ``stale`` / ``unknown``),
    a short ``detail``, the originating ``probe`` name, and how old the reading
    is. ``stale`` and ``unknown`` are never healthy: a probe row survives its
    writer, so an un-aged answer would turn a stopped brain daemon into a
    permanently green runtime.
    """
    pool = getattr(db, "cloud_pool", None) or db.pool
    # Empty or unparseable falls back to the service's own default rather than
    # silently widening the window to "never stale" — a bad value must not
    # disable the age gate that keeps a dead daemon from reading green.
    raw = site_config.get("host_probe_staleness_seconds", "")
    try:
        staleness_seconds: int | None = int(raw) if raw else None
    except (TypeError, ValueError):
        staleness_seconds = None
    return await get_host_service_health(pool, staleness_seconds=staleness_seconds)
