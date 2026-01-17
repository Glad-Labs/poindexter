"""
Route Registration - Centralized route registration for FastAPI application

Handles registration of all 15+ route routers with the FastAPI application.
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

import logging
from fastapi import FastAPI
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)


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
        intelligent_orchestrator: Optional intelligent orchestrator service

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
            intelligent_orchestrator=io
        )

        # Check which routes were registered
        if registration_status['task_router']:
            logger.info("✅ Task routes available")
    """

    status = {}

    try:
        # ===== AUTHENTICATION =====
        from routes.auth_unified import router as auth_router

        app.include_router(auth_router)
        logger.info(" auth_unified registered")
        status["auth_router"] = True
    except Exception as e:
        logger.error(f" auth_unified failed: {e}")
        status["auth_router"] = False

    try:
        # ===== TASK MANAGEMENT (CORE) =====
        from routes.task_routes import router as task_router

        # Database service now injected via Depends(get_database_dependency) in routes
        app.include_router(task_router)
        logger.info(" task_router registered")
        status["task_router"] = True
    except Exception as e:
        logger.error(f" task_router failed: {e}")
        status["task_router"] = False

    try:
        # ===== BULK TASK OPERATIONS =====
        from routes.bulk_task_routes import router as bulk_task_router

        app.include_router(bulk_task_router)
        logger.info(" bulk_task_router registered")
        status["bulk_task_router"] = True
    except Exception as e:
        logger.error(f" bulk_task_router failed: {e}")
        status["bulk_task_router"] = False

    try:
        # ===== WRITING STYLE MANAGEMENT (RAG) =====
        from routes.writing_style_routes import router as writing_style_router

        # Database service now injected via Depends(get_database_dependency) in routes
        app.include_router(writing_style_router)
        logger.info(" writing_style_router registered (RAG style matching)")
        status["writing_style_router"] = True
    except Exception as e:
        logger.error(f" writing_style_router failed: {e}")
        status["writing_style_router"] = False

    # ===== WRITING SAMPLE UPLOAD (Phase 3.1) - REMOVED =====
    # Note: sample_upload_routes.py was removed in previous cleanup
    # Functionality moved to writing_style_routes.py if needed
    status["sample_upload_router"] = False

    try:
        # ===== MEDIA & IMAGE MANAGEMENT =====
        from routes.media_routes import media_router

        # Image service injected via Depends(get_image_service) in routes
        app.include_router(media_router)
        logger.info(" media_router registered (image generation & search)")
        status["media_router"] = True
    except Exception as e:
        logger.error(f" media_router failed: {e}")
        status["media_router"] = False

    try:
        # ===== CMS (Simple CMS - replaces Strapi) =====
        from routes.cms_routes import router as cms_router

        app.include_router(cms_router)
        logger.info(" cms_router registered")
        status["cms_router"] = True
    except Exception as e:
        logger.error(f" cms_router failed: {e}")
        status["cms_router"] = False

    try:
        # ===== MODELS & AI BACKENDS =====
        from routes.model_routes import models_router, models_list_router

        app.include_router(models_router)
        app.include_router(models_list_router)
        logger.info(" models_router registered")
        status["models_router"] = True
    except Exception as e:
        logger.error(f" models_router failed: {e}")
        status["models_router"] = False

    try:
        # ===== SETTINGS =====
        from routes.settings_routes import router as settings_router

        # Database service now injected via Depends(get_database_dependency) in routes
        app.include_router(settings_router)
        logger.info(" settings_router registered")
        status["settings_router"] = True
    except Exception as e:
        logger.error(f" settings_router failed: {e}")
        status["settings_router"] = False

    try:
        # ===== COMMAND QUEUE =====
        from routes.command_queue_routes import router as command_queue_router

        app.include_router(command_queue_router)
        logger.info(" command_queue_router registered")
        status["command_queue_router"] = True
    except Exception as e:
        logger.error(f" command_queue_router failed: {e}")
        status["command_queue_router"] = False

    try:
        # ===== CHAT & AI INTEGRATION =====
        from routes.chat_routes import router as chat_router

        app.include_router(chat_router)
        logger.info(" chat_router registered")
        status["chat_router"] = True
    except Exception as e:
        logger.error(f" chat_router failed: {e}")
        status["chat_router"] = False

    try:
        # ===== OLLAMA INTEGRATION =====
        from routes.ollama_routes import router as ollama_router

        app.include_router(ollama_router)
        logger.info(" ollama_router registered")
        status["ollama_router"] = True
    except Exception as e:
        logger.error(f" ollama_router failed: {e}")
        status["ollama_router"] = False

    try:
        # ===== WEBHOOKS =====
        from routes.webhooks import webhook_router

        app.include_router(webhook_router)
        logger.info(" webhook_router registered")
        status["webhook_router"] = True
    except Exception as e:
        logger.error(f" webhook_router failed: {e}")
        status["webhook_router"] = False

    try:
        # ===== SOCIAL MEDIA MANAGEMENT =====
        from routes.social_routes import social_router

        app.include_router(social_router)
        logger.info(" social_router registered")
        status["social_router"] = True
    except Exception as e:
        logger.error(f" social_router failed: {e}")
        status["social_router"] = False

    try:
        # ===== METRICS & ANALYTICS =====
        from routes.metrics_routes import metrics_router

        app.include_router(metrics_router)
        logger.info(" metrics_router registered")
        status["metrics_router"] = True
    except Exception as e:
        logger.error(f" metrics_router failed: {e}")
        status["metrics_router"] = False

    try:
        # ===== ANALYTICS - KPI Dashboard =====
        from routes.analytics_routes import analytics_router

        app.include_router(analytics_router)
        logger.info(" analytics_router registered (KPI dashboard)")
        status["analytics_router"] = True
    except Exception as e:
        logger.error(f" analytics_router failed: {e}")
        status["analytics_router"] = False

    try:
        # ===== AI AGENT MANAGEMENT =====
        from routes.agents_routes import router as agents_router

        app.include_router(agents_router)
        logger.info(" agents_router registered")
        status["agents_router"] = True
    except Exception as e:
        logger.error(f" agents_router failed: {e}")
        status["agents_router"] = False

    # ===== OPTIONAL ROUTES (Conditional on availability) =====

    try:
        # ===== WORKFLOW HISTORY (Phase 5) =====
        from services.workflow_history import WorkflowHistoryService
        from routes.workflow_history import (
            router as workflow_history_router,
            alias_router as workflow_history_alias_router,
            initialize_history_service,
        )

        if database_service and workflow_history_service:
            initialize_history_service(database_service.pool)
            app.include_router(workflow_history_router)
            app.include_router(workflow_history_alias_router)
            logger.info(
                " workflow_history_router registered (both /api/workflow/* and /api/workflows/* paths)"
            )
            status["workflow_history_router"] = True
        else:
            logger.warning(" workflow_history not available (dependencies missing)")
            status["workflow_history_router"] = False
    except ImportError as e:
        logger.warning(f" workflow_history not available: {e}")
        status["workflow_history_router"] = False
    except Exception as e:
        logger.error(f" workflow_history registration failed: {e}")
        status["workflow_history_router"] = False

    # ===== INTELLIGENT ORCHESTRATOR (DEPRECATED - replaced by UnifiedOrchestrator) =====
    # This router is no longer registered. Use orchestrator_routes instead.
    logger.info(" intelligent_orchestrator_routes SKIPPED (deprecated, use orchestrator_routes)")
    status["intelligent_orchestrator_router"] = False

    try:
        # ===== WEBSOCKET - Real-time progress tracking =====
        from routes.websocket_routes import websocket_router

        app.include_router(websocket_router)
        logger.info(" websocket_router registered (real-time progress tracking)")
        status["websocket_router"] = True
    except Exception as e:
        logger.error(f" websocket_router failed: {e}")
        status["websocket_router"] = False

    # Log registration summary
    total_routes = len(status)
    registered_routes = sum(1 for v in status.values() if v)
    logger.info(
        f"✅ Route registration complete: {registered_routes}/{total_routes} routers registered"
    )

    return status
