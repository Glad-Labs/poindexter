"""
Glad Labs AI Agent - Poindexter
FastAPI application serving as the central orchestrator for the Glad Labs ecosystem
Implements PostgreSQL database with REST API command queue integration
"""

import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

# Third-party imports
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
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
from services.container import service_container
from services.auth import AuthService

# Local application imports (must come after path setup)
from utils.exception_handlers import register_exception_handlers
from utils.middleware_config import MiddlewareConfig
from utils.route_registration import register_all_routes
from utils.route_utils import initialize_services
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

        # Initialize auth service
        logger.info("[LIFESPAN] Initializing authentication service. ..")
        auth_service = AuthService()
        service_container.register("auth", auth_service)
        logger.info("[LIFESPAN] ✅ Authentication service initialized")

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
        service_container.register("quality", quality_service)
        logger.info("[LIFESPAN] ✅ Quality service initialized")

        # Register services in the global DI container for dependency injection
        logger.info("[LIFESPAN] Registering services in global DI container. ..")
        initialize_services(
            app,
            database_service=services["database"],
            orchestrator=services.get("orchestrator"),
            task_executor=services["task_executor"],
            intelligent_orchestrator=services.get("intelligent_orchestrator"),
            workflow_history=services["workflow_history"]
        )
        logger.info("[LIFESPAN] ✅ Services registered in global DI container")

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
setup_sentry(app, service_name="cofounder-agent")

# ===== MIDDLEWARE CONFIGURATION =====
# Register all middleware (centralized in utils.middleware_config)
middleware_config = MiddlewareConfig()
middleware_config.register_all_middleware(app)

# ===== ROUTE REGISTRATION =====
# Register all API routes from routes/ modules
logger.info("[STARTUP] Registering all routes...")
register_all_routes(app)
logger.info("[STARTUP] ✅ All routes registered")

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
            "version": "3.0.1",
            "timestamp": datetime.utcnow().isoformat(),
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
                logger.warning("Database health check failed in /api/health: %s", str(e))
                health_data["components"]["database"] = "degraded"
        else:
            health_data["components"]["database"] = "unavailable"

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
        response = await orchestrator.process_command_async(
            command.command, command.context
        )

        return CommandResponse(
            response=response.get("response", "Command processed"),
            task_id=response.get("task_id"),
            metadata=response.get("metadata"),
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error(
            f"Error processing command: {str(e)} | command={command.command}"
        )
        raise HTTPException(
            status_code=500, detail=f"An internal error occurred: {str(e)}"
        ) from e


@app.get("/")
async def root():
    """
    Root endpoint to confirm the server is running.
    """
    return {
        "message": "Glad Labs AI Co-Founder is running",
        "version": "3.0.1",
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
