"""
Route Registration - Centralized route registration for FastAPI application

Handles registration of route modules with the FastAPI application.
Provides dependency injection of database service to route modules.

Deployment modes (controlled by DEPLOYMENT_MODE env var):
- coordinator (default): cloud — minimal read-only API for Vercel frontend
- worker: Local PC — heavy compute, all write operations, admin APIs

Coordinator routes (public site only — least privilege):
- CMS (posts, categories, tags, search, status, page view beacon)
- Podcast (RSS feed, episodes, MP3 streaming)
- Revalidation (ISR cache busting — called BY backend, not by users)
- Newsletter (subscribe, unsubscribe — Vercel serverless handles Resend directly)

Worker routes (local PC — everything):
- All coordinator routes (for local dev/preview)
- Task management (create, list, status, publish, approval)
- Settings (read/write)
- Metrics & analytics
- Video (episodes & generation)
"""

import importlib
from typing import Any, Dict, Optional

from fastapi import FastAPI

from services.logger_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Route manifest — (module_path, router_attr, status_key, description)
#
# ORDER MATTERS: approval_router must precede task_router so that the concrete
# path /api/tasks/pending-approval is matched before the wildcard /{task_id}.
# ---------------------------------------------------------------------------

# Routes for coordinator mode (cloud — minimal read-only for Vercel)
# LEAST PRIVILEGE: only endpoints the public site actually calls
_COORDINATOR_ROUTES = [
    ("routes.cms_routes", "router", "cms_router", "CMS (posts, categories, tags, search, beacon)"),
    ("routes.podcast_routes", "router", "podcast_router", "podcast RSS feed & episodes"),
    ("routes.revalidate_routes", "router", "revalidate_router", "ISR cache invalidation"),
    ("routes.newsletter_routes", "router", "newsletter_router", "newsletter subscribe/unsubscribe"),
]

# Routes for worker mode (local PC — full power, all operations)
# Workers run content generation, task management, and serve preview.
_WORKER_ROUTES = [
    ("routes.approval_routes", "router", "approval_router", "task approval workflow"),
    ("routes.task_routes", "router", "task_router", "task management"),
    ("routes.cms_routes", "router", "cms_router", "CMS (posts, preview, categories)"),
    ("routes.newsletter_routes", "router", "newsletter_router", "newsletter subscribe/unsubscribe"),
    ("routes.revalidate_routes", "router", "revalidate_router", "ISR cache invalidation"),
    ("routes.podcast_routes", "router", "podcast_router", "podcast RSS feed & episodes"),
    ("routes.video_routes", "router", "video_router", "video episodes & generation"),
    ("routes.settings_routes", "router", "settings_router", "settings read/write"),
    ("routes.metrics_routes", "metrics_router", "metrics_router", "metrics & analytics"),
    ("routes.pipeline_events_routes", "router", "pipeline_events_router", "pipeline events observability (/api/pipeline, /pipeline)"),
]

# Backward-compatible alias: defaults to coordinator manifest
_ROUTE_MANIFEST = _COORDINATOR_ROUTES

def register_all_routes(
    app: FastAPI,
    deployment_mode: str = "coordinator",
    database_service: Any | None = None,
) -> dict[str, bool]:
    """
    Register route routers with the FastAPI application based on deployment mode.

    Args:
        app: FastAPI application instance
        deployment_mode: "coordinator" (default) or "worker"
        database_service: Optional database service to inject into routes

    Returns:
        Dictionary with route registration status for each router
    """
    if deployment_mode == "worker":
        manifest = _WORKER_ROUTES
    else:
        manifest = _COORDINATOR_ROUTES

    logger.info(
        " Deployment mode: %s — registering %d route modules",
        deployment_mode,
        len(manifest),
    )

    status: dict[str, bool] = {}

    # Routes that are intentionally absent (module removed)
    # sample_upload_routes.py removed — functionality moved to writing_style_routes.py
    status["sample_upload_router"] = False

    for module_path, router_attr, status_key, description in manifest:
        try:
            module = importlib.import_module(module_path)
            router = getattr(module, router_attr)
            app.include_router(router)
            logger.info(" %s registered (%s)", status_key, description)
            status[status_key] = True
        except Exception as e:
            logger.error(" %s failed: %s", status_key, e, exc_info=True)
            status[status_key] = False

    total_routes = len(status)
    registered_routes = sum(1 for v in status.values() if v)
    logger.info(
        " Route registration complete: %d/%d routers registered",
        registered_routes,
        total_routes,
    )

    return status


