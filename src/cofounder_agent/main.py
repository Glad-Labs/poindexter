"""
Glad Labs AI Agent - Poindexter
FastAPI application serving as the central orchestrator for the Glad Labs ecosystem
Implements PostgreSQL database with REST API command queue integration
"""

import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Third-party imports
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, validator

# Import configuration
from config import get_config

# Load configuration
config = get_config()

# Import services
from services.logger_config import get_logger
from services.quality_service import UnifiedQualityService
from services.sentry_integration import setup_sentry
from services.telemetry import setup_telemetry
from services.unified_orchestrator import UnifiedOrchestrator

# Local application imports (must come after path setup)
from utils.exception_handlers import register_exception_handlers
from utils.middleware_config import MiddlewareConfig
from utils.route_registration import register_all_routes, register_workflow_history_routes
from utils.route_utils import (
    get_database_dependency,
    get_orchestrator_dependency,
    get_redis_cache_optional,
    initialize_services,
)
from utils.startup_manager import StartupManager

try:
    import sentry_sdk  # pylint: disable=unused-import

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

# PostgreSQL database service is now the primary service
DATABASE_SERVICE_AVAILABLE = True

# Flag for Google Cloud availability (for test mocking)
# Google Cloud services have been replaced with PostgreSQL + task store
GOOGLE_CLOUD_AVAILABLE = False

logger = get_logger(__name__)


# ============================================================================
# LIFESPAN: Application Startup and Shutdown
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):  # pylint: disable=redefined-outer-name
    """
    Application lifespan manager - handles startup and shutdown.

    Uses StartupManager to orchestrate all service initialization
    in the correct order with proper error handling.
    """
    startup_manager = StartupManager()

    try:
        logger.info("=" * 80)
        logger.info("[LIFESPAN] Starting application startup sequence. ..")
        logger.info("=" * 80)

        # Initialize all services
        logger.info("[LIFESPAN] Calling startup_manager.initialize_all_services(). ..")
        services = await startup_manager.initialize_all_services()
        logger.info("[LIFESPAN] ✅ All services initialized by startup_manager")
        logger.debug(f"[LIFESPAN] Services dict keys: {services.keys()}")

        # Framework-level app state flags (exception handling and startup status)
        logger.info("[LIFESPAN] Initializing framework-level app state flags. ..")
        app.state.startup_error = services["startup_error"]
        app.state.startup_complete = True
        logger.debug("[LIFESPAN] ✅ Framework-level flags initialized")
        logger.info(
            "[LIFESPAN] NOTE: Application-level services are now injected via ServiceContainer + Depends()"
        )

        # Auth is handled by routes/auth_unified.py (GitHub OAuth + JWT)
        # No stub AuthService needed — see services/github_oauth.py

        # Initialize capability system
        logger.info("[LIFESPAN] Initializing capability system. ..")
        try:
            from services.capability_examples import register_example_capabilities

            register_example_capabilities()
            logger.info("[LIFESPAN] ✅ Capability system initialized with example capabilities")
        except Exception as e:
            logger.warning(f"[LIFESPAN] ⚠️ Failed to initialize capabilities: {e}")

        # Initialize quality service
        logger.info("[LIFESPAN] Initializing quality service. ..")
        quality_service = UnifiedQualityService()
        logger.info("[LIFESPAN] ✅ Quality service initialized")

        # Initialize UnifiedOrchestrator and inject into task executor
        logger.info("[LIFESPAN] Initializing UnifiedOrchestrator. ..")
        orchestrator = None
        try:
            orchestrator = UnifiedOrchestrator()
            logger.info("[LIFESPAN] ✅ UnifiedOrchestrator initialized")
        except Exception as e:
            logger.error(
                f"[LIFESPAN] ❌ Failed to initialize UnifiedOrchestrator: {e}", exc_info=True
            )
            logger.warning(
                "[LIFESPAN] ⚠️ Orchestrator initialization failed - system will use fallback template-based generation"
            )

        # Inject orchestrator into task executor via Depends()-compatible setter
        task_executor = services.get("task_executor")
        if task_executor and orchestrator:
            task_executor.inject_orchestrator(orchestrator)
            logger.info("[LIFESPAN] ✅ Orchestrator injected into TaskExecutor")

        # Register services in the global DI container for dependency injection
        logger.info("[LIFESPAN] Registering services in global DI container. ..")
        initialize_services(
            app,
            database_service=services["database"],
            orchestrator=services.get("orchestrator"),
            task_executor=services["task_executor"],
            intelligent_orchestrator=services.get("intelligent_orchestrator"),
            workflow_history=services["workflow_history"],
            redis_cache=services.get("redis_cache"),
            custom_workflows_service=services.get("custom_workflows_service"),
            template_execution_service=services.get("template_execution_service"),
        )
        logger.info("[LIFESPAN] ✅ Services registered in global DI container")

        # Register workflow history routes now that services are available
        logger.info("[LIFESPAN] Registering workflow history routes...")
        wh_registered = register_workflow_history_routes(
            app,
            database_service=services["database"],
            workflow_history_service=services["workflow_history"],
        )
        if wh_registered:
            logger.info("[LIFESPAN] ✅ Workflow history routes registered")
        else:
            logger.warning("[LIFESPAN] ⚠️ Workflow history routes not available")

        # Start the background task executor (get from services dict - fixed)
        logger.info("[LIFESPAN] Starting background task executor...")
        task_executor = services.get("task_executor")
        if task_executor:
            await task_executor.start()
            logger.info("[LIFESPAN] ✅ Background task executor started")
        else:
            logger.warning("[LIFESPAN] ⚠️ Task executor not available in services dict")

        logger.info("[OK] Lifespan: Yielding control to FastAPI application. ..")
        try:
            logger.info("[OK] Application is now running")
        except UnicodeEncodeError:
            logger.info("[OK] Application is now running")

        yield  # Application runs here

    except Exception as e:
        logger.error(f"Critical startup failure: {str(e)}", exc_info=True)
        try:
            logger.error(f"[ERROR] EXCEPTION IN LIFESPAN: {str(e)}")
        except UnicodeEncodeError:
            logger.error(f"[ERROR] EXCEPTION IN LIFESPAN: {str(e)}")
        app.state.startup_error = str(e)
        app.state.startup_complete = True
        raise

    finally:
        try:
            logger.info("[STOP] Shutting down application")
        except UnicodeEncodeError:
            logger.info("[STOP] Shutting down application")
        await startup_manager.shutdown()


app = FastAPI(
    title="Glad Labs AI Co-Founder",
    description="""
## Comprehensive AI-powered business co-founder system

The Glad Labs AI Co-Founder provides autonomous agents and intelligent orchestration 
for complete business operations including:
- **Task Planning & Execution**: Intelligent task decomposition and multi-agent execution
- **Content Generation**: AI-powered content creation with quality evaluation and multi-channel publishing
- **Business Intelligence**: Market analysis, trend detection, and strategic recommendations
- **CMS & Media Management**: Content management, featured image generation, and media organization
- **Social Media Integration**: Multi-platform content distribution and engagement tracking
- **Workflow Orchestration**: Complex business process automation with persistence and monitoring
- **Model Management**: Unified LLM access across Ollama, HuggingFace, OpenAI, Anthropic, and Google

### Quick Links
- **Documentation**: [View Full Docs](./docs/00-README.md)
- **Architecture**: [System Design](./docs/02-ARCHITECTURE_AND_DESIGN.md)
- **API Base URL**: http://localhost:8000

### Authentication
Most endpoints require JWT authentication via the `Authorization: Bearer <token>` header.
Use the `/api/auth/logout` or GitHub OAuth endpoints to obtain tokens.
""",
    version="3.0.1",
    lifespan=lifespan,
    contact={
        "name": "Glad Labs Support",
        "email": "support@gladlabs.io",
        "url": "https://gladlabs.io",
    },
    license_info={"name": "AGPL-3.0", "url": "https://www.gnu.org/licenses/agpl-3.0.html"},
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    swagger_ui_parameters={"defaultModelsExpandDepth": 1},
)

# Initialize OpenTelemetry tracing
setup_telemetry(app)

# ===== EXCEPTION HANDLERS =====
# Register all exception handlers (centralized in utils.exception_handlers)
register_exception_handlers(app)

# ===== ERROR TRACKING: SENTRY INTEGRATION =====
# Captures exceptions, performance metrics, and error tracking
if SENTRY_AVAILABLE:
    setup_sentry(app, service_name="cofounder-agent")

# ===== MIDDLEWARE CONFIGURATION =====
# Register all middleware (centralized in utils.middleware_config)
middleware_config = MiddlewareConfig()
middleware_config.register_all_middleware(app)


# ===== PUBLIC ENDPOINTS =====
@app.get("/api/tasks-public", response_model=dict)
async def list_tasks_public():
    """Public version of list tasks - no auth required"""
    return {"success": True, "data": [], "pagination": {"limit": 0, "offset": 0, "total": 0}}


# ===== ROUTE REGISTRATION =====
# Register all API routes from routes/ modules
logger.info("[STARTUP] Registering all routes...")
register_all_routes(app)
logger.info("[STARTUP] ✅ All routes registered")


# ===== DEVELOPMENT-ONLY PUBLIC ENDPOINTS (DEFINED AFTER ROUTE REGISTRATION) =====
# These override/supplement the normal authenticated endpoints for development
@app.get("/api/tasks/list-public")
async def list_tasks_pub_dev(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    database_service: Any = Depends(get_database_dependency),
):
    """Public endpoint for listing tasks - NO AUTHENTICATION REQUIRED (Development Only)

    DI-5: Now uses Depends() injection for database service
    """
    try:
        if not database_service:
            return {
                "success": True,
                "tasks": [],
                "total": 0,
                "offset": offset,
                "limit": limit,
            }

        tasks, total = await database_service.get_tasks_paginated(
            offset=offset, limit=limit, status=status, category=category, user_id=None
        )

        return {
            "success": True,
            "tasks": tasks,
            "total": total,
            "offset": offset,
            "limit": limit,
        }
    except Exception as e:
        logger.error(f"Error listing public tasks: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "tasks": [],
            "total": 0,
            "offset": offset,
            "limit": limit,
        }


# ===== UNIFIED HEALTH CHECK ENDPOINT =====
# Consolidated from: /api/health, /status, /metrics/health, and route-specific health endpoints
# DI-5: Health check endpoints refactored to use Depends() for database and cache injection


@app.get("/api/health")
async def api_health(
    database_service: Any = Depends(get_database_dependency),
    redis_cache: Any = Depends(get_redis_cache_optional),
):
    """
    Unified health check endpoint for Railway deployment and load balancers.

    Returns comprehensive status of all critical services:
    - Startup status (starting/degraded/healthy)
    - Database connectivity and health
    - Task executor liveness and queue depth
    - LLM provider availability
    - Redis connectivity

    Used by: Railway load balancers, monitoring systems, external health checks
    Authentication: Not required (critical for load balancers)
    """
    try:
        # Build comprehensive health response
        health_data = {
            "status": "healthy",
            "service": "cofounder-agent",
            "version": "3.0.1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
        }

        # Check startup status (framework-level, still via app.state)
        startup_error = getattr(app.state, "startup_error", None)
        startup_complete = getattr(app.state, "startup_complete", False)

        if startup_error:
            health_data["status"] = "degraded"
            health_data["startup_error"] = startup_error
            health_data["startup_complete"] = startup_complete
            logger.warning("Health check returning degraded status: %s", startup_error)
        elif not startup_complete:
            health_data["status"] = "starting"
            health_data["startup_complete"] = False

        # Database
        if database_service:
            try:
                db_health = await database_service.health_check()
                db_status = db_health.get("status", "unknown")
                db_info: dict = {"status": db_status}
                if "pool" in db_health:
                    db_info["pool"] = db_health["pool"]
                    if db_health["pool"].get("utilization", 0) > 0.8:
                        db_info["alert"] = "pool_near_exhaustion"
                        health_data["status"] = "degraded"
                health_data["components"]["database"] = db_info
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("Database health check failed in /api/health: %s", str(e))
                health_data["components"]["database"] = "degraded"
                health_data["status"] = "degraded"
        else:
            health_data["components"]["database"] = "unavailable"
            health_data["status"] = "degraded"

        # Task executor liveness + queue depth
        try:
            from utils.route_utils import _services
            executor = _services.get_task_executor()
            if executor is not None:
                import time as _time
                last_poll_age_s = None
                if executor.last_poll_at is not None:
                    last_poll_age_s = round(_time.monotonic() - executor.last_poll_at, 1)
                executor_info: dict = {
                    "running": executor.running,
                    "task_count": executor.task_count,
                    "error_count": executor.error_count,
                    "last_poll_age_s": last_poll_age_s,
                }
                health_data["components"]["task_executor"] = executor_info
                if not executor.running and startup_complete:
                    health_data["status"] = "degraded"
                    executor_info["alert"] = "executor_not_running"
                elif last_poll_age_s is not None and last_poll_age_s > executor.poll_interval * 2:
                    # Loop is running but hasn't polled recently — possible stall
                    health_data["status"] = "degraded"
                    executor_info["alert"] = "executor_stall_suspected"
            else:
                health_data["components"]["task_executor"] = "unavailable"

            # Queue depth (pending task count)
            if database_service:
                try:
                    pending = await database_service.get_pending_tasks(limit=1000)
                    health_data["components"]["task_queue"] = {"pending_count": len(pending)}
                    if len(pending) > 100:
                        health_data["components"]["task_queue"]["alert"] = "queue_depth_high"
                except Exception:  # pylint: disable=broad-except
                    health_data["components"]["task_queue"] = "unavailable"
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Task executor health check failed: %s", str(e))
            health_data["components"]["task_executor"] = "unavailable"

        # LLM provider availability (fast check — no actual LLM call)
        try:
            from services.model_router import get_model_router
            model_router = get_model_router()
            providers_available = []
            if getattr(model_router, "_anthropic_client", None):
                providers_available.append("anthropic")
            if getattr(model_router, "_openai_client", None):
                providers_available.append("openai")
            if getattr(model_router, "_ollama_client", None):
                providers_available.append("ollama")
            if getattr(model_router, "_gemini_client", None):
                providers_available.append("gemini")
            provider_health = {}
            if hasattr(model_router, "get_provider_health"):
                provider_health = model_router.get_provider_health()
            llm_info: dict = {
                "available": providers_available,
                "count": len(providers_available),
                "runtime_failures": provider_health,
            }
            health_data["components"]["llm_providers"] = llm_info
            if not providers_available:
                health_data["status"] = "degraded"
                llm_info["alert"] = "no_providers_available"
            elif any(
                v.get("consecutive_failures", 0) >= 5
                for v in provider_health.values()
            ):
                llm_info["alert"] = "provider_consecutive_failures"
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("LLM provider health check failed: %s", str(e))
            health_data["components"]["llm_providers"] = "unavailable"

        # Redis
        if redis_cache is not None:
            health_data["components"]["redis"] = "healthy"
        else:
            health_data["components"]["redis"] = "unavailable"

        return health_data
    except Exception as e:  # pylint: disable=broad-except
        logger.error("Health check failed: %s", str(e), exc_info=True)
        return {"status": "unhealthy", "service": "cofounder-agent", "error": "internal error"}


@app.get("/health")
async def health():
    """
    Quick health check endpoint (no dependencies) - for load balancers and monitoring.

    Returns: 200 OK if app is running
    Usage: External load balancers, uptime monitors, basic connectivity checks
    Performance: Instant response (doesn't check database)
    """
    return {"status": "ok", "service": "cofounder-agent"}


# ===== METRICS ENDPOINT =====
# Consolidated from: /api/metrics, /metrics, /tasks/metrics, etc.


@app.get("/api/metrics")
async def get_metrics_endpoint(
    database_service: Any = Depends(get_database_dependency),
):
    """
    Aggregated task and system metrics endpoint.

    Returns comprehensive metrics for the oversight dashboard:
    - Task statistics (total, completed, failed, pending)
    - Success rate percentage
    - Average execution time
    - Estimated costs

    **Returns:**
    - total_tasks: Total number of tasks created
    - completed_tasks: Successfully completed tasks
    - failed_tasks: Failed tasks
    - pending_tasks: Queued or in-progress tasks
    - success_rate: Success percentage (0-100)
    - avg_execution_time: Average task duration in seconds
    - total_cost: Estimated total cost in USD

    DI-5: Now uses Depends() injection for database service
    """
    try:
        if database_service:
            metrics = await database_service.get_metrics()
            return metrics

        # Return mock metrics if database unavailable
        return {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "pending_tasks": 0,
            "success_rate": 0.0,
            "avg_execution_time": 0.0,
            "total_cost": 0.0,
        }
    except Exception as e:  # pylint: disable=broad-except
        logger.error("Metrics retrieval failed: %s", str(e), exc_info=True)
        return {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "pending_tasks": 0,
            "success_rate": 0.0,
            "avg_execution_time": 0.0,
            "total_cost": 0.0,
            "error": str(e),
        }


# ===== PUBLIC DEVELOPMENT ENDPOINTS (NO AUTH REQUIRED) =====
# These endpoints are for development/testing only and may expose data without authentication


class CommandRequest(BaseModel):
    """Request model for processing a command.

    Attributes
    ----------
    command: The command string to be processed by the orchestrator.
    context: Optional context dictionary that can influence command execution.
    """

    command: str
    context: Optional[Dict[str, Any]] = None

    @validator("command")
    def _command_must_not_be_empty(cls, v: str) -> str:  # pylint: disable=no-self-argument
        if not v or not v.strip():
            raise ValueError("command must be a non-empty string")
        return v


class CommandResponse(BaseModel):
    """Response model for the result of a command.

    Attributes
    ----------
    response: Human-readable response from the orchestrator.
    task_id: Optional identifier of a background task created by the command.
    metadata: Optional dictionary containing additional data returned by the orchestrator.
    """

    response: str
    task_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@app.post("/command", response_model=CommandResponse)
async def process_command(
    command: CommandRequest,
    background_tasks: BackgroundTasks,
    orchestrator=Depends(get_orchestrator_dependency),
):  # pylint: disable=unused-argument
    """
    Processes a command sent to the Co-Founder agent.

    This endpoint receives a command, delegates it to the orchestrator logic,
    and returns the result. Can optionally execute tasks in the background.
    """
    try:
        logger.info(f"Received command: {command.command}")

        # orchestrator is injected via Depends(get_orchestrator_dependency)

        # Execute the command asynchronously
        response = await orchestrator.process_command_async(command.command, command.context)

        return CommandResponse(
            response=response.get("response", "Command processed"),
            task_id=response.get("task_id"),
            metadata=response.get("metadata"),
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f"Error processing command: {str(e)} | command={command.command}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred") from e


@app.get("/")
async def root(database_service: Any = Depends(get_database_dependency)):
    """
    Root endpoint to confirm the server is running.

    DI-5: Now uses Depends() injection for database availability check
    """
    return {
        "message": "Glad Labs AI Co-Founder is running",
        "version": "3.0.1",
        "database_enabled": database_service is not None,
    }


if __name__ == "__main__":
    # Watch the entire src directory for changes to support agent development
    # NOTE: Use 'python -m uvicorn main:app --reload' instead of 'python main.py'
    # This file is imported by uvicorn when using the module syntax, so running
    # uvicorn.run() here creates nested server conflicts.
    logger.error("ERROR: Do not run 'python main.py' directly.")
    logger.error("Instead, use:")
    logger.error("  python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000")
    sys.exit(1)
