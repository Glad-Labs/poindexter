"""
Glad Labs AI Agent - Poindexter
FastAPI application serving as the central orchestrator for the Glad Labs ecosystem
Implements PostgreSQL database with REST API command queue integration
"""

# ============================================================================
# CRITICAL: Fix sys.path for namespace packages at module import time
# Poetry sometimes breaks namespace package resolution (e.g., google.generativeai)
# This MUST be done before any package imports
# ============================================================================
import sys
from pathlib import Path as _PathType


def _fix_sys_path():
    """Fix sys.path to prioritize venv site-packages."""
    try:
        venv_site_packages = _PathType(sys.prefix) / "Lib" / "site-packages"
        if venv_site_packages.exists():
            venv_site_packages_str = str(venv_site_packages)
            # Ensure venv's site-packages is first in the path
            sys.path = [venv_site_packages_str] + [
                p for p in sys.path if p != venv_site_packages_str
            ]
            # Clear import caches to force fresh imports
            import importlib  # pylint: disable=import-outside-toplevel

            importlib.invalidate_caches()
    except (OSError, AttributeError, ValueError) as e:
        print(f"[WARNING] Failed to fix sys.path: {e}")


_fix_sys_path()
del _fix_sys_path, _PathType

# Standard library imports (at the top as required by pylint)
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

# Third-party imports
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel, validator

# CRITICAL: Environment loading must happen before service imports
# to ensure .env.local is available for all services
# Load environment variables from .env.local first
# Try to load .env.local from the project root, then from current directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
env_local_path = os.path.join(project_root, ".env.local")
if os.path.exists(env_local_path):
    load_dotenv(env_local_path, override=True)
else:
    # Fallback to .env.local in current directory
    load_dotenv(".env.local", override=True)

# Add the current directory (cofounder_agent) to Python path FIRST
# This allows absolute imports like "from services.x import y" to work
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from services.content_router_service import get_content_task_store
from services.logger_config import get_logger
from services.quality_service import UnifiedQualityService
from services.sentry_integration import setup_sentry
from services.telemetry import setup_telemetry
from services.unified_orchestrator import UnifiedOrchestrator

# Local application imports (must come after path setup)
# pylint: disable=wrong-import-position,import-error
from utils.exception_handlers import register_exception_handlers
from utils.middleware_config import MiddlewareConfig
from utils.route_registration import register_all_routes
from utils.route_utils import initialize_services
from utils.startup_manager import StartupManager

# pylint: enable=import-error

try:
    import sentry_sdk  # pylint: disable=unused-import
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

# Import workflow history service dependencies (needed for startup manager)
try:
    WORKFLOW_HISTORY_AVAILABLE = True
except ImportError as e:
    WORKFLOW_HISTORY_AVAILABLE = False
    logging.warning("Workflow history service not available: %s", str(e))

# PostgreSQL database service is now the primary service
# Legacy 'database.py' (SQLAlchemy) has been removed.
DATABASE_SERVICE_AVAILABLE = True

# Flag for Google Cloud availability (for test mocking)
# Google Cloud services have been replaced with PostgreSQL + task store
GOOGLE_CLOUD_AVAILABLE = False

# Placeholder for firestore_client (for backward compatibility with tests)
# Actual implementation uses PostgreSQL through database_service
FIRESTORE_CLIENT = None  # noqa: invalid-name

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
        logger.info("[LIFESPAN] Starting application startup sequence...")
        logger.info("=" * 80)

        # Initialize all services
        logger.info("[LIFESPAN] Calling startup_manager.initialize_all_services()...")
        services = await startup_manager.initialize_all_services()
        logger.info("[LIFESPAN] ✅ All services initialized by startup_manager")
        logger.debug(f"[LIFESPAN] Services dict keys: {services.keys()}")

        # Inject services into app state for access in routes
        logger.info("[LIFESPAN] Injecting services into app.state...")
        app.state.database = services["database"]
        app.state.redis_cache = services["redis_cache"]
        # app.state.orchestrator will be set to UnifiedOrchestrator below
        # (removed legacy Orchestrator)
        app.state.task_executor = services["task_executor"]
        app.state.workflow_history = services["workflow_history"]
        app.state.training_data_service = services.get("training_data_service")
        app.state.fine_tuning_service = services.get("fine_tuning_service")
        app.state.legacy_data_service = services.get("legacy_data_service")
        app.state.startup_error = services["startup_error"]
        app.state.startup_complete = True
        logger.debug("[LIFESPAN] ✅ All services injected into app.state")

        # Initialize new consolidated services
        db_service = services["database"]
        logger.info(f"[LIFESPAN] db_service = {db_service}")
        logger.debug(f"[LIFESPAN] db_service.pool = {db_service.pool}")
        logger.debug(f"[LIFESPAN] db_service.tasks = {db_service.tasks}")

        # Initialize task store for content orchestrator
        logger.info("[LIFESPAN] Initializing ContentTaskStore...")
        task_store = get_content_task_store(db_service)
        app.state.task_store = task_store
        logger.info("✅ ContentTaskStore initialized")

        # Initialize quality service
        logger.info("[LIFESPAN] Initializing UnifiedQualityService...")
        quality_service = UnifiedQualityService(
            model_router=getattr(app.state, "model_router", None),
            database_service=db_service,
            qa_agent=None,
        )
        app.state.quality_service = quality_service
        logger.info("✅ UnifiedQualityService initialized")

        # The UnifiedOrchestrator is created here with all dependencies properly initialized
        # This ensures TaskExecutor can use it for content generation
        logger.info("[LIFESPAN] Creating UnifiedOrchestrator with all dependencies...")
        logger.debug("[LIFESPAN] UnifiedOrchestrator dependencies:")
        logger.debug("  - database_service: %s", db_service)
        logger.debug("  - quality_service: %s", quality_service)

        # Initialize unified orchestrator with all available agents
        unified_orchestrator = UnifiedOrchestrator(
            database_service=db_service,
            model_router=getattr(app.state, "model_router", None),
            quality_service=quality_service,
            memory_system=getattr(app.state, "memory_system", None),
            # No longer needed - pipeline is built into UnifiedOrchestrator
            content_orchestrator=None,
            financial_agent=getattr(app.state, "financial_agent", None),
            compliance_agent=getattr(app.state, "compliance_agent", None),
        )
        app.state.unified_orchestrator = unified_orchestrator
        # CRITICAL: Set as primary orchestrator for TaskExecutor to use
        app.state.orchestrator = unified_orchestrator
        logger.info("✅ UnifiedOrchestrator initialized and set as primary orchestrator")
        logger.debug(f"[LIFESPAN] app.state.orchestrator = {app.state.orchestrator}")

        # CRITICAL: Inject app.state into task_executor NOW (before it processes tasks)
        # This ensures it has access to the properly-initialized UnifiedOrchestrator
        logger.info("[LIFESPAN] Injecting app.state into TaskExecutor...")
        task_executor = services.get("task_executor")
        if task_executor:
            logger.debug(
                "[LIFESPAN] TaskExecutor before injection: "
                f"app_state={getattr(task_executor, 'app_state', None)}"
            )
            task_executor.app_state = app.state
            logger.debug(
                "[LIFESPAN] TaskExecutor after injection: " f"app_state={task_executor.app_state}"
            )
            logger.debug(
                "[LIFESPAN] TaskExecutor.orchestrator property check: "
                f"{task_executor.orchestrator is not None}"
            )
            logger.info("✅ TaskExecutor app.state reference updated with UnifiedOrchestrator")
            logger.info(
                f"   TaskExecutor can now access orchestrator: "
                f"{task_executor.orchestrator is not None}"
            )

            # NOW start the task executor (after UnifiedOrchestrator is initialized)
            logger.info("[LIFESPAN] Starting TaskExecutor background processing loop...")
            await task_executor.start()
            logger.info("✅ TaskExecutor started successfully with UnifiedOrchestrator")
        else:
            logger.warning("⚠️ TaskExecutor not found in services")

        # Store db_service with alternative name for dependency injection
        app.state.db_service = db_service
        logger.debug(f"[LIFESPAN] app.state.db_service = {app.state.db_service}")

        # Initialize ServiceContainer for Phase 2 utilities
        # Provides 3 access patterns: get_services(), Depends(), Request.state
        # Note: orchestrator will be UnifiedOrchestrator, set below before TaskExecutor starts
        logger.info("[LIFESPAN] Initializing ServiceContainer...")
        initialize_services(
            app,
            database_service=services["database"],
            orchestrator=None,  # Will be set to UnifiedOrchestrator below
            task_executor=services["task_executor"],
            workflow_history=services["workflow_history"],
        )
        logger.debug("[LIFESPAN] ✅ ServiceContainer initialized")

        # Register routes with initialized services
        logger.info("[LIFESPAN] Registering routes...")
        register_all_routes(
            app,
            database_service=services["database"],
            workflow_history_service=services["workflow_history"],
            training_data_service=services.get("training_data_service"),
            fine_tuning_service=services.get("fine_tuning_service"),
        )

        # Initialize LangGraph orchestrator
        try:
            from services.langgraph_orchestrator import (  # pylint: disable=import-outside-toplevel
                LangGraphOrchestrator,
            )

            langgraph_orchestrator = LangGraphOrchestrator(
                db_service=db_service,
                llm_service=getattr(app.state, "model_router", None),
                quality_service=quality_service,
                metadata_service=getattr(app.state, "unified_metadata_service", None),
            )
            app.state.langgraph_orchestrator = langgraph_orchestrator
            logger.info("✅ LangGraphOrchestrator initialized")
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("⚠️  LangGraph initialization failed (non-critical): %s", str(e))
            app.state.langgraph_orchestrator = None

        logger.info("[OK] Lifespan: Yielding control to FastAPI application...")
        try:
            print("[OK] Application is now running")
        except UnicodeEncodeError:
            print("[OK] Application is now running")

        yield  # Application runs here

    except Exception as e:
        logger.error(f"Critical startup failure: {str(e)}", exc_info=True)
        try:
            print(f"[ERROR] EXCEPTION IN LIFESPAN: {str(e)}")
        except UnicodeEncodeError:
            print(f"[ERROR] EXCEPTION IN LIFESPAN: {str(e)}")
        app.state.startup_error = str(e)
        app.state.startup_complete = True
        raise

    finally:
        try:
            print("[STOP] Shutting down application")
        except UnicodeEncodeError:
            print("[STOP] Shutting down application")
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

    This endpoint consolidates previous endpoints:
    - GET /status (removed)
    - GET /metrics/health (database health)
    - GET /settings/health (removed duplicate)
    - GET /tasks/health/status (removed duplicate)
    - GET /models/providers/status (removed duplicate)

    Used by: Railway load balancers, monitoring systems, external health checks
    Authentication: Not required (critical for load balancers)
    """
    try:
        # Build comprehensive health response
        health_data = {
            "status": "healthy",
            "service": "cofounder-agent",
            "version": "1.0.0",
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


@app.get("/api/metrics")
async def get_metrics():
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
    def _command_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("command must be a non-empty string")
        return v


class CommandResponse(BaseModel):
    """Response model for the result of a command.

    Attributes
    ----------
    response: Human‑readable response from the orchestrator.
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


# The legacy /status endpoint has been removed. Clients should use /api/health.


@app.get("/")
async def root():
    """
    Root endpoint to confirm the server is running.
    """
    return {
        "message": "Glad Labs AI Co-Founder is running",
        "version": "1.0.0",
        "database_enabled": hasattr(app.state, "database") and app.state.database is not None,
    }


if __name__ == "__main__":
    # Watch the entire src directory for changes to support agent development
    # NOTE: Use 'python -m uvicorn main:app --reload' instead of 'python main.py'
    # This file is imported by uvicorn when using the module syntax, so running
    # uvicorn.run() here creates nested server conflicts.
    print("ERROR: Do not run 'python main.py' directly.")
    print("Instead, use:")
    print("  python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000")
    sys.exit(1)
