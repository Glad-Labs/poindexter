"""
Glad Labs AI Agent - Poindexter
FastAPI application serving as the central orchestrator for the Glad Labs ecosystem
Implements PostgreSQL database with REST API command queue integration
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Third-party imports
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel, validator

# Import configuration
from config import get_config

# Load configuration
config = get_config()

from services.container import service_container

# Import services
from services.logger_config import get_logger
from services.quality_service import UnifiedQualityService
from services.sentry_integration import setup_sentry
from services.telemetry import setup_telemetry

# Local application imports (must come after path setup)
from utils.exception_handlers import register_exception_handlers
from utils.middleware_config import MiddlewareConfig
from utils.route_registration import register_all_routes
from utils.route_utils import initialize_services
from utils.connection_health import ConnectionPoolHealth
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
    scheduled_publisher_task = None
    pool_health_task = None

    try:
        logger.info("=" * 80)
        logger.info("[LIFESPAN] Starting application startup sequence. ..")
        logger.info("=" * 80)

        # Initialize all services
        logger.info("[LIFESPAN] Calling startup_manager.initialize_all_services(). ..")
        services = await startup_manager.initialize_all_services()
        logger.info("[LIFESPAN] ✅ All services initialized by startup_manager")
        logger.debug(f"[LIFESPAN] Services dict keys: {services.keys()}")

        # Inject services into app state for access in routes
        logger.info("[LIFESPAN] Injecting services into app.state. ..")
        app.state.database = services["database"]
        app.state.redis_cache = services["redis_cache"]
        # app.state.orchestrator will be set to UnifiedOrchestrator below
        # (removed legacy Orchestrator)
        app.state.task_executor = services["task_executor"]
        app.state.workflow_history = services["workflow_history"]
        app.state.training_data_service = services.get("training_data_service")
        app.state.fine_tuning_service = services.get("fine_tuning_service")
        app.state.custom_workflows_service = services.get("custom_workflows_service")
        app.state.legacy_data_service = services.get("legacy_data_service")
        app.state.startup_error = services["startup_error"]
        app.state.startup_complete = True
        logger.debug("[LIFESPAN] ✅ All services injected into app.state")

        # Initialize capability system
        logger.info("[LIFESPAN] Initializing capability system. ..")
        try:
            from services.capability_examples import register_example_capabilities

            register_example_capabilities()
            logger.info("[LIFESPAN] ✅ Capability system initialized with example capabilities")
        except Exception as e:
            logger.warning(f"[LIFESPAN] ⚠️ Failed to initialize capabilities: {e}", exc_info=True)

        # Initialize quality service
        logger.info("[LIFESPAN] Initializing quality service. ..")
        quality_service = UnifiedQualityService()
        service_container.register("quality", quality_service)
        logger.info("[LIFESPAN] ✅ Quality service initialized")

        # Initialize template execution service for blog_post workflow
        logger.info("[LIFESPAN] Initializing template execution service...")
        try:
            from services.template_execution_service import TemplateExecutionService

            custom_workflows_svc = services.get("custom_workflows_service")
            template_execution_service = TemplateExecutionService(
                custom_workflows_service=custom_workflows_svc,
            )
            logger.info("[LIFESPAN] ✅ Template execution service initialized")
        except Exception as e:
            template_execution_service = None
            logger.warning(f"[LIFESPAN] ⚠️ Template execution service failed: {e}", exc_info=True)

        # Register services in the global DI container for dependency injection
        logger.info("[LIFESPAN] Registering services in global DI container. ..")
        initialize_services(
            app,
            database_service=services["database"],
            orchestrator=services.get("orchestrator"),
            task_executor=services["task_executor"],
            intelligent_orchestrator=services.get("intelligent_orchestrator"),
            workflow_history=services["workflow_history"],
            custom_workflows_service=services.get("custom_workflows_service"),
            template_execution_service=template_execution_service,
        )
        logger.info("[LIFESPAN] ✅ Services registered in global DI container")

        # Branch startup behaviour based on deployment mode
        deployment_mode = os.getenv("DEPLOYMENT_MODE", "coordinator")
        logger.info(f"[LIFESPAN] Deployment mode: {deployment_mode}")

        if deployment_mode == "worker":
            # Worker mode: register worker, start heartbeat, start task executor
            try:
                from services.worker_service import WorkerService

                worker_service = WorkerService(services["database"].pool)
                await worker_service.register()
                await worker_service.start_heartbeat()
                app.state.worker_service = worker_service
                logger.info("[LIFESPAN] Worker: registered and heartbeat started")
            except ImportError:
                logger.warning("[LIFESPAN] Worker: worker_service module not yet available, skipping")
            except Exception as e:
                logger.error(f"[LIFESPAN] Worker: failed to start worker service: {e}", exc_info=True)

            # Start task executor (claims tasks from queue)
            task_executor = services.get("task_executor")
            if task_executor:
                await task_executor.start()
                logger.info("[LIFESPAN] Worker: task executor started")
        else:
            # Coordinator mode: start webhook delivery, scheduled publisher
            # Do NOT start task executor (workers handle that)
            try:
                from services.webhook_delivery_service import WebhookDeliveryService

                webhook_service = WebhookDeliveryService(services["database"].pool)
                await webhook_service.start()
                app.state.webhook_service = webhook_service
                logger.info("[LIFESPAN] Coordinator: webhook delivery started")
            except ImportError:
                logger.warning("[LIFESPAN] Coordinator: webhook_delivery_service not yet available, skipping")
            except Exception as e:
                logger.error(f"[LIFESPAN] Coordinator: failed to start webhook delivery: {e}", exc_info=True)

            # Start the scheduled post publisher (publishes posts at their scheduled time)
            from services.scheduled_publisher import run_scheduled_publisher

            db_pool = services["database"].pool

            async def _get_pool():
                return db_pool

            scheduled_publisher_task = asyncio.create_task(run_scheduled_publisher(_get_pool))
            logger.info("[LIFESPAN] Coordinator: scheduled post publisher started")

        # Start connection pool health monitor (#819)
        db_service = services.get("database")
        if db_service and getattr(db_service, "pool", None):
            pool_health = ConnectionPoolHealth(db_service.pool)
            pool_health_task = asyncio.create_task(pool_health.auto_health_check())
            app.state.pool_health = pool_health
            logger.info("[LIFESPAN] Connection pool health monitor started")

        # Initialize global model router singleton and seed spend counter from
        # cost_logs so budget enforcement survives restarts (issue #1385).
        try:
            from services.model_router import get_model_router, initialize_model_router

            _router = get_model_router()
            if _router is None:
                _router = initialize_model_router()
            if _router and getattr(db_service, "pool", None):
                await _router.seed_spend_from_db(db_service.pool)
                logger.info("[LIFESPAN] Model router spend seeded from cost_logs")
        except Exception as e:
            logger.warning(f"[LIFESPAN] Failed to seed model router spend: {e}", exc_info=True)

        logger.info("[OK] Lifespan: Yielding control to FastAPI application. ..")
        try:
            logger.info("[OK] Application is now running")
        except UnicodeEncodeError:
            logger.info("[OK] Application is now running")

        yield  # Application runs here

    except Exception as e:
        logger.error(f"Critical startup failure: {str(e)}", exc_info=True)
        try:
            logger.error(f"[ERROR] EXCEPTION IN LIFESPAN: {str(e)}", exc_info=True)
        except UnicodeEncodeError:
            logger.error(f"[ERROR] EXCEPTION IN LIFESPAN: {str(e)}", exc_info=True)
        app.state.startup_error = str(e)
        app.state.startup_complete = True
        raise

    finally:
        try:
            logger.info("[STOP] Shutting down application")
        except UnicodeEncodeError:
            logger.info("[STOP] Shutting down application")
        if scheduled_publisher_task is not None:
            scheduled_publisher_task.cancel()
            try:
                await scheduled_publisher_task
            except asyncio.CancelledError:
                pass
        if pool_health_task is not None:
            pool_health_task.cancel()
            try:
                await pool_health_task
            except asyncio.CancelledError:
                pass
        # Stop worker service if running in worker mode
        if hasattr(app.state, "worker_service"):
            try:
                await app.state.worker_service.stop()
                logger.info("[STOP] Worker service stopped")
            except Exception as e:
                logger.error(f"[STOP] Error stopping worker service: {e}", exc_info=True)
        # Stop webhook delivery service if running in coordinator mode
        if hasattr(app.state, "webhook_service"):
            try:
                await app.state.webhook_service.stop()
                logger.info("[STOP] Webhook delivery service stopped")
            except Exception as e:
                logger.error(f"[STOP] Error stopping webhook delivery: {e}", exc_info=True)
        await startup_manager.shutdown()


_deployment_mode = os.getenv("DEPLOYMENT_MODE", "coordinator")

app = FastAPI(
    title=f"Glad Labs AI Co-Founder ({_deployment_mode})",
    description=f"""
## Comprehensive AI-powered business co-founder system

**Deployment mode: `{_deployment_mode}`** — {"always-on lightweight coordinator (Railway)" if _deployment_mode == "coordinator" else "heavy-compute worker (local PC)"}

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
API endpoints require `Authorization: Bearer <API_TOKEN>` header.
Admin panel at `/admin`.
""",
    version=config.app_version,
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

# Initialize SQLAdmin panel at /admin
try:
    from admin import setup_admin

    setup_admin(app)
    logger.info("[ADMIN] SQLAdmin panel mounted at /admin")
except Exception as e:
    logger.warning(f"[ADMIN] SQLAdmin not available: {e}")

# Initialize OpenTelemetry tracing
setup_telemetry(app)

# ===== EXCEPTION HANDLERS =====
# Register all exception handlers (centralized in utils.exception_handlers)
register_exception_handlers(app)

# ===== ERROR TRACKING: SENTRY INTEGRATION =====
# Captures exceptions, performance metrics, and error tracking
setup_sentry(app, service_name="cofounder-agent")

# ===== MIDDLEWARE CONFIGURATION =====
# Register all middleware (centralized in utils.middleware_config)
middleware_config = MiddlewareConfig()
middleware_config.register_all_middleware(app)

# ===== ROUTE REGISTRATION =====
# Register API routes based on deployment mode (coordinator or worker)
deployment_mode = os.getenv("DEPLOYMENT_MODE", "coordinator")
logger.info("[STARTUP] Registering routes for deployment mode: %s", deployment_mode)
register_all_routes(app, deployment_mode=deployment_mode)
logger.info("[STARTUP] ✅ Routes registered (mode=%s)", deployment_mode)

# ===== UNIFIED HEALTH CHECK ENDPOINT =====
# Consolidated from: /api/health, /status, /metrics/health, and route-specific health endpoints


@app.get("/api/health")
async def api_health():
    """
    Unified health check endpoint for Railway deployment and load balancers.

    Returns comprehensive status of all critical services:
    - Startup status (starting/degraded/healthy)
    - Database connectivity and health
    - Orchestrator initialization and status
    - LLM providers availability
    - Timestamp for monitoring systems

    Used by: Railway load balancers, monitoring systems, external health checks
    Authentication: Not required (critical for load balancers)
    """
    try:
        # Build comprehensive health response
        health_data = {
            "status": "healthy",
            "service": "cofounder-agent",
            "version": config.app_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
        }

        # Check startup status
        startup_error = getattr(app.state, "startup_error", None)
        startup_complete = getattr(app.state, "startup_complete", False)

        if startup_error:
            health_data["status"] = "degraded"
            health_data["startup_error"] = startup_error
            health_data["startup_complete"] = startup_complete
            logger.warning(f"Health check returning degraded status: {startup_error}")
        elif not startup_complete:
            health_data["status"] = "starting"
            health_data["startup_complete"] = False

        # Include database status if available
        database_service = getattr(app.state, "database", None)
        if database_service:
            try:
                db_health = await database_service.health_check()
                health_data["components"]["database"] = db_health.get("status", "unknown")
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    "Database health check failed in /api/health: %s", str(e), exc_info=True
                )
                health_data["components"]["database"] = "degraded"
        else:
            health_data["components"]["database"] = "unavailable"

        # Include task executor liveness and queue depth (#580)
        task_executor = getattr(app.state, "task_executor", None)
        if task_executor is not None:
            try:
                executor_stats = task_executor.get_stats()
                # Fetch pending/in-progress counts from DB for queue-depth monitoring
                pending_count = 0
                in_progress_count = 0
                if database_service:
                    try:
                        task_counts = await database_service.tasks.get_task_counts()
                        pending_count = getattr(task_counts, "pending", 0)
                        in_progress_count = getattr(task_counts, "in_progress", 0)
                    except Exception:  # pylint: disable=broad-except
                        pass  # Non-critical — executor stats still returned
                health_data["components"]["task_executor"] = {
                    "running": executor_stats.get("running", False),
                    "pending_task_count": pending_count,
                    "in_progress_count": in_progress_count,
                    "total_processed": executor_stats.get("task_count", 0),
                    "success_count": executor_stats.get("success_count", 0),
                    "error_count": executor_stats.get("error_count", 0),
                }
                # Degrade overall status if executor is not running
                if not executor_stats.get("running", False) and health_data["status"] == "healthy":
                    health_data["status"] = "degraded"
                    health_data["components"]["task_executor"][
                        "degraded_reason"
                    ] = "executor_not_running"
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("Task executor health check failed: %s", str(e), exc_info=True)
                health_data["components"]["task_executor"] = "unavailable"
        else:
            health_data["components"]["task_executor"] = "unavailable"

        return health_data
    except Exception as e:  # pylint: disable=broad-except
        logger.error("Health check failed: %s", str(e), exc_info=True)
        return {"status": "unhealthy", "service": "cofounder-agent", "error": str(e)}


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
async def get_metrics_endpoint():
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
    """
    try:
        database_service = getattr(app.state, "database", None)
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
    request: Request,
    command: CommandRequest,
    background_tasks: BackgroundTasks,
):  # pylint: disable=unused-argument
    """
    Processes a command sent to the Co-Founder agent.

    This endpoint receives a command, delegates it to the orchestrator logic,
    and returns the result. Can optionally execute tasks in the background.
    """
    try:
        logger.info(f"Received command: {command.command}")

        orchestrator = getattr(request.app.state, "orchestrator", None)
        if orchestrator is None:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        # Execute the command asynchronously
        response = await orchestrator.process_command_async(command.command, command.context)

        return CommandResponse(
            response=response.get("response", "Command processed"),
            task_id=response.get("task_id"),
            metadata=response.get("metadata"),
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error(
            f"Error processing command: {str(e)} | command={command.command}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"An internal error occurred: {str(e)}") from e


@app.get("/")
async def root():
    """
    Root endpoint to confirm the server is running.
    """
    return {
        "message": "Glad Labs AI Co-Founder is running",
        "version": config.app_version,
        "database_enabled": hasattr(app.state, "database") and app.state.database is not None,
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
