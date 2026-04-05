"""
Route Registration - Centralized route registration for FastAPI application

Handles registration of route modules with the FastAPI application.
Provides dependency injection of database service to route modules.

Deployment modes (controlled by DEPLOYMENT_MODE env var):
- coordinator (default): Railway — only routes the public site needs
- worker: Local PC heavy compute. Minimal routes — workers claim tasks from DB.

Coordinator routes (public site + essential ops):
- CMS (posts, categories, tags, search, status, page view beacon)
- Newsletter (subscribe, unsubscribe, count)
- Revalidation (ISR cache busting)
- Podcast (RSS feed, episodes, MP3 streaming)
- Tasks (create, list, status, publish — includes sub-routers)
- Settings (read/write)
- Approval (approve/reject tasks)
- Metrics (system metrics)

Worker routes:
- Task management (core CRUD + status)
- Metrics & analytics
- Settings
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

# Routes for coordinator mode (Railway — only what the public site needs)
_COORDINATOR_ROUTES = [
    ("routes.approval_routes", "router", "approval_router", "task approval workflow"),
    ("routes.task_routes", "router", "task_router", "task management"),
    ("routes.cms_routes", "router", "cms_router", "CMS (posts, categories, tags, search, beacon)"),
    ("routes.newsletter_routes", "router", "newsletter_router", "newsletter subscribe/unsubscribe"),
    ("routes.revalidate_routes", "router", "revalidate_router", "ISR cache invalidation"),
    ("routes.podcast_routes", "router", "podcast_router", "podcast RSS feed & episodes"),
    ("routes.settings_routes", "router", "settings_router", "settings read/write"),
    ("routes.metrics_routes", "metrics_router", "metrics_router", "system metrics"),
]

# Routes for worker mode (local PC — heavy compute)
# Workers primarily claim tasks from the DB and report results back.
# They don't need CMS or webhook routes.
_WORKER_ROUTES = [
    ("routes.task_routes", "router", "task_router", "task management"),
    ("routes.metrics_routes", "metrics_router", "metrics_router", "metrics & analytics"),
    ("routes.settings_routes", "router", "settings_router", "user settings"),
    ("routes.podcast_routes", "router", "podcast_router", "podcast RSS feed & episodes"),
    ("routes.video_routes", "router", "video_router", "video episodes & generation"),
]

# Backward-compatible alias: defaults to coordinator manifest
_ROUTE_MANIFEST = _COORDINATOR_ROUTES

def register_all_routes(
    app: FastAPI,
    deployment_mode: str = "coordinator",
    database_service: Optional[Any] = None,
    workflow_history_service: Optional[Any] = None,
    training_data_service: Optional[Any] = None,
    fine_tuning_service: Optional[Any] = None,
) -> Dict[str, bool]:
    """
    Register route routers with the FastAPI application based on deployment mode.

    This function consolidates all route registration into one place,
    making it easy to see which routes are available and to add/remove
    routes without cluttering the main.py file.

    Args:
        app: FastAPI application instance
        deployment_mode: "coordinator" (default) or "worker"
        database_service: Optional database service to inject into routes
        workflow_history_service: Optional workflow history service

    Returns:
        Dictionary with route registration status for each router

    Example:
        from utils.route_registration import register_all_routes
        from services.database_service import DatabaseService

        app = FastAPI()
        db = DatabaseService()

        registration_status = register_all_routes(
            app,
            deployment_mode="coordinator",
            database_service=db,
            workflow_history_service=wh,
        )

        # Check which routes were registered
        if registration_status['task_router']:
            logger.info("Task routes available")
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

    status: Dict[str, bool] = {}

    # Routes that are intentionally absent (module removed or registered elsewhere)
    # sample_upload_routes.py removed — functionality moved to writing_style_routes.py
    status["sample_upload_router"] = False
    # workflow_history registered via register_workflow_history_routes() in lifespan
    status["workflow_history_router"] = False

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


def register_workflow_history_routes(
    app: FastAPI, database_service: Any, workflow_history_service: Any
) -> bool:
    """
    Register workflow history routes once services are available during lifespan.

    Called from main.py lifespan after database and workflow_history services are initialized.
    Separated from register_all_routes because those services aren't available at module load time.
    """
    try:
        from routes.workflow_history import initialize_history_service
        from routes.workflow_history import router as workflow_history_router

        if not database_service or not workflow_history_service:
            logger.warning(
                "workflow_history routes skipped: database or workflow_history service not available"
            )
            return False

        initialize_history_service(database_service.pool)
        app.include_router(workflow_history_router)
        logger.info("workflow_history_router registered (/api/workflows/* paths)")
        return True
    except ImportError as e:
        logger.warning(f"workflow_history routes not available: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"workflow_history route registration failed: {e}", exc_info=True)
        return False
