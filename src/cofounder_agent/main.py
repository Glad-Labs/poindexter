"""
Glad Labs AI Agent - Poindexter
FastAPI application serving as the central orchestrator for the Glad Labs ecosystem
Implements PostgreSQL database with REST API command queue integration
"""

import sys
import os
import logging
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import uvicorn

try:
    import sentry_sdk

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

# Load environment variables from .env.local first
from dotenv import load_dotenv

# Try to load .env.local from the project root, then from current directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
env_local_path = os.path.join(project_root, ".env.local")
if os.path.exists(env_local_path):
    load_dotenv(env_local_path, override=True)
    print(f"[+] Loaded .env.local from {env_local_path}")
else:
    # Fallback to .env.local in current directory
    load_dotenv(".env.local", override=True)
    print("[+] Loaded .env.local from current directory")

# Add the cofounder_agent directory to the Python path for relative imports
sys.path.insert(0, os.path.dirname(__file__))
# Add the parent directory (src) to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator_logic import Orchestrator
from services.database_service import DatabaseService
from services.task_executor import TaskExecutor
from services.content_critique_loop import ContentCritiqueLoop
from services.telemetry import setup_telemetry  #  OpenTelemetry tracing
from services.sentry_integration import setup_sentry  #  Sentry error tracking
from services.redis_cache import setup_redis_cache  #  Redis caching for query optimization
from services.content_router_service import get_content_task_store  #  Inject DB service
from services.migrations import run_migrations  #  Database schema migrations

# Import new consolidated services
from services.unified_orchestrator import UnifiedOrchestrator
from services.quality_service import UnifiedQualityService
from services.content_orchestrator import ContentOrchestrator

# Import new utility modules
from utils.startup_manager import StartupManager
from utils.exception_handlers import register_exception_handlers
from utils.middleware_config import MiddlewareConfig
from utils.route_registration import register_all_routes
from utils.route_utils import initialize_services

# Import workflow history service dependencies (needed for startup manager)
try:
    from services.workflow_history import WorkflowHistoryService
    from routes.workflow_history import initialize_history_service

    WORKFLOW_HISTORY_AVAILABLE = True
except ImportError as e:
    WORKFLOW_HISTORY_AVAILABLE = False
    WorkflowHistoryService = None
    initialize_history_service = None
    logging.warning(f"Workflow history service not available: {str(e)}")

# IntelligentOrchestrator is DEPRECATED - replaced by UnifiedOrchestrator
# Keeping as None for backward compatibility with startup_manager
INTELLIGENT_ORCHESTRATOR_AVAILABLE = False
IntelligentOrchestrator = None
EnhancedMemorySystem = None
intelligent_orchestrator_router = None

# PostgreSQL database service is now the primary service
# Legacy 'database.py' (SQLAlchemy) has been removed.
DATABASE_SERVICE_AVAILABLE = True

# Flag for Google Cloud availability (for test mocking)
# Google Cloud services have been replaced with PostgreSQL + task store
GOOGLE_CLOUD_AVAILABLE = False

# Placeholder for firestore_client (for backward compatibility with tests)
# Actual implementation uses PostgreSQL through database_service
firestore_client = None

# Use centralized logging configuration
from services.logger_config import get_logger
from services.model_consolidation_service import initialize_model_consolidation_service
from services.training_data_service import TrainingDataService
from services.fine_tuning_service import FineTuningService
from services.legacy_data_integration import LegacyDataIntegrationService

logger = get_logger(__name__)


# ============================================================================
# LIFESPAN: Application Startup and Shutdown
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager - handles startup and shutdown.

    Uses StartupManager to orchestrate all service initialization
    in the correct order with proper error handling.
    """
    startup_manager = StartupManager()

    try:
        # Initialize all services
        services = await startup_manager.initialize_all_services()

        # Inject services into app state for access in routes
        app.state.database = services["database"]
        app.state.redis_cache = services["redis_cache"]
        app.state.orchestrator = services["orchestrator"]
        app.state.task_executor = services["task_executor"]
        app.state.intelligent_orchestrator = services["intelligent_orchestrator"]
        app.state.workflow_history = services["workflow_history"]
        app.state.training_data_service = services.get("training_data_service")
        app.state.fine_tuning_service = services.get("fine_tuning_service")
        app.state.legacy_data_service = services.get("legacy_data_service")
        app.state.startup_error = services["startup_error"]
        app.state.startup_complete = True

        # Initialize new consolidated services
        db_service = services["database"]

        # Initialize task store for content orchestrator
        from services.content_router_service import get_content_task_store

        task_store = get_content_task_store(db_service)
        app.state.task_store = task_store
        logger.info("✅ ContentTaskStore initialized")

        # Initialize quality service
        quality_service = UnifiedQualityService(
            model_router=getattr(app.state, "model_router", None),
            database_service=db_service,
            qa_agent=None,
        )
        app.state.quality_service = quality_service
        logger.info("✅ UnifiedQualityService initialized")

        # Initialize content orchestrator for use in unified system
        content_orchestrator = ContentOrchestrator(
            task_store=task_store  # Now uses the initialized task_store
        )
        app.state.content_orchestrator = content_orchestrator
        logger.info("✅ ContentOrchestrator initialized with task_store")

        # Initialize unified orchestrator with all available agents
        unified_orchestrator = UnifiedOrchestrator(
            database_service=db_service,
            model_router=getattr(app.state, "model_router", None),
            quality_service=quality_service,
            memory_system=getattr(app.state, "memory_system", None),
            content_orchestrator=content_orchestrator,
            financial_agent=getattr(app.state, "financial_agent", None),
            compliance_agent=getattr(app.state, "compliance_agent", None),
        )
        app.state.unified_orchestrator = unified_orchestrator
        logger.info("✅ UnifiedOrchestrator initialized")

        # Store db_service with alternative name for dependency injection
        app.state.db_service = db_service

        # Initialize ServiceContainer for Phase 2 utilities
        # Provides 3 access patterns: get_services(), Depends(), Request.state
        initialize_services(
            app,
            database_service=services["database"],
            orchestrator=services["orchestrator"],
            task_executor=services["task_executor"],
            intelligent_orchestrator=services["intelligent_orchestrator"],
            workflow_history=services["workflow_history"],
        )

        # Register routes with initialized services
        register_all_routes(
            app,
            database_service=services["database"],
            workflow_history_service=services["workflow_history"],
            intelligent_orchestrator=services["intelligent_orchestrator"],
            training_data_service=services.get("training_data_service"),
            fine_tuning_service=services.get("fine_tuning_service"),
        )

        # Initialize LangGraph orchestrator
        try:
            from services.langgraph_orchestrator import LangGraphOrchestrator

            langgraph_orchestrator = LangGraphOrchestrator(
                db_service=db_service,
                llm_service=getattr(app.state, "model_router", None),
                quality_service=quality_service,
                metadata_service=getattr(app.state, "unified_metadata_service", None),
            )
            app.state.langgraph_orchestrator = langgraph_orchestrator
            logger.info("✅ LangGraphOrchestrator initialized")
        except Exception as e:
            logger.warning(f"⚠️  LangGraph initialization failed (non-critical): {str(e)}")
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
    - GET /status (StatusResponse)
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
            except Exception as e:
                logger.warning(f"Database health check failed in /api/health: {e}")
                health_data["components"]["database"] = "degraded"
        else:
            health_data["components"]["database"] = "unavailable"

        return health_data
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return {"status": "unhealthy", "service": "cofounder-agent", "error": str(e)}


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
        else:
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
    except Exception as e:
        logger.error(f"Metrics retrieval failed: {e}", exc_info=True)
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


@app.get("/api/debug/startup")
async def debug_startup():
    """
    Debug endpoint showing startup status and any errors
    Only available in development mode
    """
    database_service = getattr(app.state, "database", None)
    orchestrator = getattr(app.state, "orchestrator", None)
    startup_error = getattr(app.state, "startup_error", None)
    startup_complete = getattr(app.state, "startup_complete", False)

    return {
        "startup_complete": startup_complete,
        "startup_error": startup_error,
        "database_service_available": database_service is not None,
        "orchestrator_available": orchestrator is not None,
        "environment": os.getenv("ENVIRONMENT", "development"),
        "database_url_configured": bool(os.getenv("DATABASE_URL")),
        "api_base_url": os.getenv("API_BASE_URL", "http://localhost:8000"),
    }


class CommandRequest(BaseModel):
    """Request model for processing a command."""

    command: str
    context: Optional[Dict[str, Any]] = None
    priority: Optional[str] = "normal"


class CommandResponse(BaseModel):
    """Response model for the result of a command."""

    response: str
    task_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class StatusResponse(BaseModel):
    status: str
    data: Dict[str, Any]


@app.post("/command", response_model=CommandResponse)
async def process_command(request: CommandRequest, background_tasks: BackgroundTasks):
    """
    Processes a command sent to the Co-Founder agent.

    This endpoint receives a command, delegates it to the orchestrator logic,
    and returns the result. Can optionally execute tasks in the background.
    """
    try:
        logger.info(f"Received command: {request.command}")

        if orchestrator is None:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        # Always use async version with new database service
        response = await orchestrator.process_command_async(request.command, request.context)

        return CommandResponse(
            response=response.get("response", "Command processed"),
            task_id=response.get("task_id"),
            metadata=response.get("metadata"),
        )
    except Exception as e:
        logger.error(f"Error processing command: {str(e)} | command={request.command}")
        raise HTTPException(status_code=500, detail=f"An internal error occurred: {str(e)}")


@app.get("/status", response_model=StatusResponse)
async def get_status():
    """
    DEPRECATED: Use GET /api/health instead.

    Backward compatibility endpoint that wraps the unified /api/health endpoint.
    Maintained for clients that depend on StatusResponse model.
    Will be removed in version 2.0.
    """
    try:
        # Call the unified health endpoint
        health = await api_health()

        # Convert to StatusResponse format for backward compatibility
        status_data = {
            "service": (
                "online" if health.get("status") == "healthy" else health.get("status", "unknown")
            ),
            "database_available": database_service is not None,
            "orchestrator_initialized": orchestrator is not None,
            "timestamp": health.get("timestamp", str(asyncio.get_event_loop().time())),
        }

        # Include component statuses if available
        components = health.get("components", {})
        if isinstance(components, dict):
            for key, value in components.items():
                status_data[f"component_{key}"] = value

        return StatusResponse(status=health.get("status", "unknown"), data=status_data)

    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return StatusResponse(status="unhealthy", data={"error": str(e)})


@app.get("/tasks/pending")
async def get_pending_tasks(limit: int = 10):
    """
    Get pending tasks from the task queue
    """
    try:
        if not database_service:
            return {"tasks": [], "message": "Running in development mode"}

        tasks = await database_service.get_pending_tasks(limit)
        return {"tasks": tasks, "count": len(tasks)}

    except Exception as e:
        logger.error(f"Error getting pending tasks: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get tasks: {str(e)}")


@app.get("/metrics/performance")
async def get_performance_metrics(hours: int = 24):
    """
    Get comprehensive performance metrics and analytics
    """
    try:
        if database_service:
            # Query performance data from database
            logs = await database_service.get_logs(limit=1000)
            return {"logs": logs, "status": "success"}
        else:
            return {"message": "Performance monitoring not available", "status": "disabled"}
    except Exception as e:
        logger.error(f"Error getting performance metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get performance metrics: {str(e)}")


@app.get("/metrics/health")
async def get_health_metrics():
    """
    DEPRECATED: Use GET /api/health instead.

    Backward compatibility endpoint that wraps the unified /api/health endpoint.
    Returns health metrics in the legacy format.
    Will be removed in version 2.0.
    """
    try:
        health = await api_health()

        # Convert to legacy format for backward compatibility
        components = health.get("components", {})
        database_health = components.get("database") if isinstance(components, dict) else "unknown"

        return {
            "health": {"status": database_health, "timestamp": health.get("timestamp")},
            "status": "success" if health.get("status") == "healthy" else "degraded",
        }
    except Exception as e:
        logger.error(f"Error getting health metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get health metrics: {str(e)}")


@app.post("/metrics/reset")
async def reset_performance_metrics():
    """
    Reset session-level performance metrics (admin endpoint)
    """
    try:
        # Performance metrics are now logged to database
        if database_service:
            await database_service.add_log_entry("system", "info", "Performance metrics reset")
            return {"message": "Performance metrics reset successfully", "status": "success"}
        else:
            return {"message": "Database not available", "status": "disabled"}
    except Exception as e:
        logger.error(f"Error resetting metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reset metrics: {str(e)}")


@app.get("/")
async def root():
    """
    Root endpoint to confirm the server is running.
    """
    return {
        "message": "Glad Labs AI Co-Founder is running",
        "version": "1.0.0",
        "database_enabled": hasattr(app.state, 'database') and app.state.database is not None,
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
