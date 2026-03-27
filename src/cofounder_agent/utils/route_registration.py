"""
Route Registration - Centralized route registration for FastAPI application

Handles registration of all 28 route routers with the FastAPI application.
Provides dependency injection of database service to route modules.

Unified Task Endpoint (/api/tasks):
- Single endpoint for all task types (blog_post, social_media, email, newsletter, business_analytics, data_retrieval, market_research, financial_analysis)
- Routes to appropriate handler based on task_type parameter
- Subtasks bypassed - use /api/tasks with task_type instead

Includes:
- Core business logic routes (tasks, content, bulk operations)
- API integration routes (models, auth, chat)
- System routes (health, metrics, webhooks)
- Feature-specific routes (agents, social, CMS, WebSocket)
- Optional routes (workflow history, intelligent orchestrator)
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

_ROUTE_MANIFEST = [
    # ----- Authentication -----
    ("routes.auth_unified", "router", "auth_router", "auth"),
    # ----- Approval workflow (MUST be before task_router) -----
    ("routes.approval_routes", "router", "approval_router", "task approval workflow"),
    # ----- Task management (core) -----
    ("routes.task_routes", "router", "task_router", "task management"),
    # ----- Bulk task operations -----
    ("routes.bulk_task_routes", "router", "bulk_task_router", "bulk task operations"),
    # ----- Writing style / RAG -----
    ("routes.writing_style_routes", "router", "writing_style_router", "RAG style matching"),
    # ----- Media & image management -----
    ("routes.media_routes", "media_router", "media_router", "image generation & search"),
    # ----- CMS -----
    ("routes.cms_routes", "router", "cms_router", "FastAPI CMS"),
    # ----- Models & AI backends -----
    ("routes.model_routes", "models_router", "models_router", "AI model backends"),
    # ----- Settings -----
    ("routes.settings_routes", "router", "settings_router", "user settings"),
    # ----- Command queue -----
    ("routes.command_queue_routes", "router", "command_queue_router", "command queue"),
    # ----- Chat & AI integration -----
    ("routes.chat_routes", "router", "chat_router", "chat & AI integration"),
    # ----- Ollama integration -----
    ("routes.ollama_routes", "router", "ollama_router", "Ollama integration"),
    # ----- Social media management -----
    ("routes.social_routes", "social_router", "social_router", "social media management"),
    # ----- Metrics & analytics -----
    ("routes.metrics_routes", "metrics_router", "metrics_router", "metrics & analytics"),
    # ----- KPI dashboard analytics -----
    ("routes.analytics_routes", "analytics_router", "analytics_router", "KPI dashboard"),
    # ----- Performance profiling -----
    ("routes.profiling_routes", "router", "profiling_router", "performance profiling"),
    # ----- AI agent management -----
    ("routes.agents_routes", "router", "agents_router", "agent management"),
    # ----- Privacy & GDPR compliance -----
    ("routes.privacy_routes", "router", "privacy_router", "GDPR data subject requests"),
    # ----- Newsletter & email campaigns -----
    ("routes.newsletter_routes", "router", "newsletter_router", "email campaigns & subscriptions"),
    # ----- Service registry -----
    ("routes.service_registry_routes", "router", "service_registry_router", "service discovery"),
    # ----- Agent registry -----
    ("routes.agent_registry_routes", "router", "agent_registry_router", "agent discovery"),
    # ----- Workflow orchestration -----
    ("routes.workflow_routes", "router", "workflow_router", "workflow orchestration"),
    # ----- Custom workflow builder -----
    (
        "routes.custom_workflows_routes",
        "router",
        "custom_workflows_router",
        "custom workflow builder",
    ),
    # ----- Real-time workflow progress -----
    (
        "routes.workflow_progress_routes",
        "router",
        "workflow_progress_router",
        "progress tracking & WebSocket broadcasting",
    ),
    # ----- Capability-based tasks -----
    (
        "routes.capability_tasks_routes",
        "router",
        "capability_tasks_router",
        "capability composition",
    ),
    # ----- WebSocket real-time tracking -----
    (
        "routes.websocket_routes",
        "websocket_router",
        "websocket_router",
        "real-time progress tracking",
    ),
    # ----- Cache revalidation -----
    ("routes.revalidate_routes", "router", "revalidate_router", "secure cache invalidation"),
]


def register_all_routes(
    app: FastAPI,
    database_service: Optional[Any] = None,
    workflow_history_service: Optional[Any] = None,
    training_data_service: Optional[Any] = None,
    fine_tuning_service: Optional[Any] = None,
) -> Dict[str, bool]:
    """
    Register all route routers with the FastAPI application.

    This function consolidates all route registration into one place,
    making it easy to see which routes are available and to add/remove
    routes without cluttering the main.py file.

    Args:
        app: FastAPI application instance
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
            database_service=db,
            workflow_history_service=wh,
        )

        # Check which routes were registered
        if registration_status['task_router']:
            logger.info("Task routes available")
    """
    status: Dict[str, bool] = {}

    # Routes that are intentionally absent (module removed or registered elsewhere)
    # sample_upload_routes.py removed — functionality moved to writing_style_routes.py
    status["sample_upload_router"] = False
    # workflow_history registered via register_workflow_history_routes() in lifespan
    status["workflow_history_router"] = False

    for module_path, router_attr, status_key, description in _ROUTE_MANIFEST:
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
