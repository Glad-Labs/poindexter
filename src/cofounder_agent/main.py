"""
Glad Labs AI Agent - Poindexter
FastAPI application serving as the central orchestrator for the Glad Labs ecosystem
Implements PostgreSQL database with REST API command queue integration
"""

import sys
import os
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import uvicorn

# Load environment variables from .env.local first
from dotenv import load_dotenv

# Try to load .env.local from the project root, then from current directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
env_local_path = os.path.join(project_root, '.env.local')
if os.path.exists(env_local_path):
    load_dotenv(env_local_path, override=True)
    print(f"[+] Loaded .env.local from {env_local_path}")
else:
    # Fallback to .env.local in current directory
    load_dotenv('.env.local', override=True)
    print("[+] Loaded .env.local from current directory")

# Add the cofounder_agent directory to the Python path for relative imports
sys.path.insert(0, os.path.dirname(__file__))
# Add the parent directory (src) to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from orchestrator_logic import Orchestrator
from services.database_service import DatabaseService
from services.task_executor import TaskExecutor
from services.content_critique_loop import ContentCritiqueLoop
from services.telemetry import setup_telemetry  #  OpenTelemetry tracing
from services.content_router_service import get_content_task_store  #  Inject DB service
from services.migrations import run_migrations  #  Database schema migrations

# Import route routers
# Unified content router (consolidates content.py, content_generation.py, enhanced_content.py)
from routes.content_routes import content_router
from routes.cms_routes import router as cms_router
from routes.models import models_router, models_list_router
from routes.auth_unified import router as auth_router  #  Unified auth (OAuth-only architecture)
from routes.settings_routes import router as settings_router
from routes.command_queue_routes import router as command_queue_router
from routes.chat_routes import router as chat_router
from routes.ollama_routes import router as ollama_router
from routes.task_routes import router as task_router
from routes.subtask_routes import router as subtask_router  # Subtask independent execution
from routes.bulk_task_routes import router as bulk_task_router  # Bulk task operations
from routes.webhooks import webhook_router
from routes.social_routes import social_router
from routes.metrics_routes import metrics_router
from routes.agents_routes import router as agents_router

# Import workflow history service (Phase 5 - database persistence)
try:
    from services.workflow_history import WorkflowHistoryService
    from routes.workflow_history import router as workflow_history_router, initialize_history_service
    WORKFLOW_HISTORY_AVAILABLE = True
except ImportError as e:
    WORKFLOW_HISTORY_AVAILABLE = False
    WorkflowHistoryService = None
    initialize_history_service = None
    workflow_history_router = None
    logging.warning(f"Workflow history service not available: {str(e)}")

# Import intelligent orchestrator (NEW - separate module, no conflicts)
try:
    from services.intelligent_orchestrator import IntelligentOrchestrator
    from services.orchestrator_memory_extensions import EnhancedMemorySystem
    from routes.intelligent_orchestrator_routes import router as intelligent_orchestrator_router
    INTELLIGENT_ORCHESTRATOR_AVAILABLE = True
except ImportError as e:
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

logger = get_logger(__name__)

# Global service instances
database_service: Optional[DatabaseService] = None
orchestrator: Optional[Orchestrator] = None
task_executor: Optional[TaskExecutor] = None
intelligent_orchestrator: Optional[IntelligentOrchestrator] = None  # NEW
workflow_history_service: Optional[Any] = None  # Phase 6 - Workflow history
startup_error: Optional[str] = None
startup_complete: bool = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - PostgreSQL connection is MANDATORY"""
    global database_service, orchestrator, task_executor, workflow_history_service, startup_error, startup_complete
    
    try:
        logger.info("🚀 Starting Glad Labs AI Co-Founder application...")
        logger.info(f"  Environment: {os.getenv('ENVIRONMENT', 'production')}")
        
        # ============================================================================
        # 1. MANDATORY: Initialize PostgreSQL database connection
        # ============================================================================
        logger.info("  Connecting to PostgreSQL (REQUIRED)...")
        print("  Connecting to PostgreSQL (REQUIRED)...") # Console feedback
        db_url = os.getenv('DATABASE_URL', 'Not set')
        logger.info(f"  DATABASE_URL: {db_url[:50]}...")
        
        try:
            database_service = DatabaseService()
            await database_service.initialize()
            logger.info("   PostgreSQL connected - ready for operations")
            print("   PostgreSQL connected - ready for operations") # Console feedback
        except Exception as e:
            startup_error = f" FATAL: PostgreSQL connection failed: {str(e)}"
            logger.error(f"  {startup_error}", exc_info=True)
            print(f"  {startup_error}") # Console feedback
            logger.error("  🛑 PostgreSQL is REQUIRED - cannot continue")
            logger.error("   Set DATABASE_URL or DATABASE_USER environment variables")
            logger.error("  Example DATABASE_URL: postgresql://user:password@localhost:5432/glad_labs_dev")
            raise SystemExit(1)  #  STOP - PostgreSQL required
        
        # 2. All task operations now handled by DatabaseService (pure asyncpg)
        logger.info("  📋 Task storage ready via DatabaseService (asyncpg)")
        
        # 2a. Run database migrations (audit logging, etc.)
        logger.info("  🔄 Running database migrations...")
        try:
            migrations_ok = await run_migrations(database_service)
            if migrations_ok:
                logger.info("   ✅ Database migrations completed successfully")
            else:
                logger.warning("   ⚠️ Database migrations failed (proceeding anyway)")
        except Exception as e:
            logger.warning(f"   ⚠️ Migration error: {str(e)} (proceeding anyway)")
        
        #  Inject database service into content task store (fixes initialization error)
        get_content_task_store(database_service)

        # 3. Initialize unified model consolidation service
        logger.info("  🧠 Initializing unified model consolidation service...")
        try:
            initialize_model_consolidation_service()
            logger.info("   Model consolidation service initialized (Ollama->HF->Google->Anthropic->OpenAI)")
        except Exception as e:
            error_msg = f"Model consolidation initialization failed: {str(e)}"
            logger.error(f"   {error_msg}", exc_info=True)
            # Don't fail startup - models are optional
        
        # 4. Create tables if they don't exist
        if database_service:
            try:
                logger.info("  📋 Database tables initialized in previous step")
            except Exception as e:
                error_msg = f"Table creation failed: {str(e)}"
                logger.error(f"   {error_msg}", exc_info=True)
                startup_error = error_msg
        
        # 4. Initialize orchestrator with new database service
        api_base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
        logger.info(f"  🤖 Initializing orchestrator (API: {api_base_url})...")
        try:
            orchestrator = Orchestrator(
                database_service=database_service,
                api_base_url=api_base_url
            )
            logger.info("   Orchestrator initialized successfully")
        except Exception as e:
            error_msg = f"Orchestrator initialization failed: {str(e)}"
            logger.error(f"   {error_msg}", exc_info=True)
            startup_error = error_msg
            # Don't re-raise - allow app to start for health checks
        
        # 4a. Initialize workflow history service (Phase 6 - workflow execution persistence)
        logger.info("  📊 Initializing workflow history service...")
        try:
            if WORKFLOW_HISTORY_AVAILABLE and database_service:
                workflow_history_service = WorkflowHistoryService(database_service.pool)
                initialize_history_service(database_service.pool)
                logger.info("   Workflow history service initialized - executions will be persisted to PostgreSQL")
            else:
                logger.warning("   Workflow history service not available - executions will not be persisted")
        except Exception as e:
            error_msg = f"Workflow history service initialization failed: {str(e)}"
            logger.warning(f"   {error_msg}", exc_info=True)
            workflow_history_service = None
        
        # 4b. Initialize intelligent orchestrator (NEW - non-intrusive addition)
        logger.info("  🧠 Initializing intelligent orchestrator...")
        intelligent_orchestrator = None
        try:
            if INTELLIGENT_ORCHESTRATOR_AVAILABLE and orchestrator and database_service:
                # Create enhanced memory system wrapper
                try:
                    from memory_system import AIMemorySystem
                    base_memory = AIMemorySystem(db_pool=database_service.pool)
                except Exception:
                    base_memory = None
                    
                enhanced_memory = EnhancedMemorySystem(base_memory)
                
                # Initialize intelligent orchestrator
                intelligent_orchestrator = IntelligentOrchestrator(
                    llm_client=None,  # Will be initialized internally
                    database_service=database_service,
                    memory_system=enhanced_memory,
                    mcp_orchestrator=None  # Optional, can be injected later
                )
                logger.info("   Intelligent orchestrator initialized successfully")
            else:
                logger.warning("   Intelligent orchestrator module not available or dependencies missing")
        except Exception as e:
            error_msg = f"Intelligent orchestrator initialization failed: {str(e)}"
            logger.warning(f"   {error_msg}", exc_info=True)
            intelligent_orchestrator = None
        
        # 5. Initialize content critique loop
        logger.info("  🔍 Initializing content critique loop...")
        try:
            critique_loop = ContentCritiqueLoop()
            logger.info("   Content critique loop initialized")
        except Exception as e:
            logger.warning(f"   Content critique loop initialization failed: {e}")
            critique_loop = None
        
        # 6. Initialize background task executor
        logger.info("  ⏳ Starting background task executor...")
        try:
            # Prefer IntelligentOrchestrator if available
            active_orchestrator = intelligent_orchestrator if intelligent_orchestrator else orchestrator
            
            task_executor = TaskExecutor(
                database_service=database_service,
                orchestrator=active_orchestrator,
                critique_loop=critique_loop,
                poll_interval=5  # Poll every 5 seconds
            )
            await task_executor.start()
            logger.info("   Background task executor started successfully")
            logger.info(f"     🔗 Pipeline: Orchestrator->Critique->Publishing")
        except Exception as e:
            error_msg = f"Task executor startup failed: {str(e)}"
            logger.error(f"   {error_msg}", exc_info=True)
            # Don't fail startup - task processing is optional
            task_executor = None
        
        # 6. Verify connections
        if database_service:
            try:
                logger.info("  🔍 Verifying database connection...")
                health = await database_service.health_check()
                if health.get("status") == "healthy":
                    logger.info(f"   Database health check passed")
                else:
                    logger.warning(f"   Database health check returned: {health}")
            except Exception as e:
                logger.warning(f"   Database health check failed: {e}", exc_info=True)
        
        # 6. Register database service with route modules
        if database_service:
            from routes.task_routes import set_db_service
            from routes.subtask_routes import set_db_service as set_subtask_db_service
            set_db_service(database_service)
            set_subtask_db_service(database_service)
            logger.info("   Database service registered with routes")
        
        logger.info(" Application started successfully!")
        logger.info(f"  - Database Service: {database_service is not None}")
        logger.info(f"  - Orchestrator: {orchestrator is not None}")
        logger.info(f"  - Task Executor: {task_executor is not None and task_executor.running}")
        logger.info(f"  - Task Store: initialized")
        logger.info(f"  - Startup Error: {startup_error}")
        logger.info(f"  - API Base URL: {api_base_url}")
        
        startup_complete = True
        yield  # Application runs here
        
    except Exception as e:
        startup_error = f"Critical startup failure: {str(e)}"
        logger.error(f" {startup_error}", exc_info=True)
        startup_complete = True  # Mark complete so /api/health works
    
    finally:
        # ===== SHUTDOWN =====
        try:
            logger.info("🛑 Shutting down Glad Labs AI Co-Founder application...")
            
            # Stop background task executor
            try:
                if task_executor and task_executor.running:
                    logger.info("  Stopping background task executor...")
                    await task_executor.stop()
                    logger.info("   Task executor stopped")
                    stats = task_executor.get_stats()
                    logger.info(f"     Tasks processed: {stats['total_processed']}, Success: {stats['successful']}, Failed: {stats['failed']}")
            except Exception as e:
                logger.error(f"   Error stopping task executor: {e}", exc_info=True)
            
            # Task store is now handled by database_service - no separate close needed
            logger.info("  Task store cleanup handled by database_service")
            
            if database_service:
                try:
                    logger.info("  Closing database connection...")
                    await database_service.close()
                    logger.info("   Database connection closed")
                except Exception as e:
                    logger.error(f"   Error closing database: {e}", exc_info=True)
            
            logger.info(" Application shut down successfully!")
            
        except Exception as e:
            logger.error(f" Error during shutdown: {e}", exc_info=True)

app = FastAPI(
    title="Glad Labs AI Co-Founder",
    description="Central orchestrator for Glad Labs AI-driven business operations with Google Cloud integration",
    version="1.0.0",
    lifespan=lifespan
)

# Initialize OpenTelemetry tracing
setup_telemetry(app)

# ===== SECURITY: INPUT VALIDATION MIDDLEWARE =====
# Validates and sanitizes all incoming requests
# Prevents SQL injection, XSS, oversized payloads, and other attacks
try:
    from middleware.input_validation import InputValidationMiddleware, PayloadInspectionMiddleware
    
    app.add_middleware(PayloadInspectionMiddleware)
    app.add_middleware(InputValidationMiddleware)
    
    logger.info("✅ Input validation middleware initialized")
except ImportError as e:
    logger.warning(f"⚠️  Input validation middleware not available: {e}")

# CORS middleware for frontend integration (SECURITY: environment-based configuration)
allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:3001"  # Dev default
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],  # SECURITY: Restricted from ["*"]
    allow_headers=["Authorization", "Content-Type"],  # SECURITY: Restricted from ["*"]
)

# ===== SECURITY: ADD RATE LIMITING MIDDLEWARE =====
# Protects against DDoS, API abuse, and brute force attacks
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    from fastapi.responses import JSONResponse
    
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request, exc):
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Too many requests."},
        )
    
    logger.info("✅ Rate limiting middleware initialized (slowapi)")
except ImportError:
    logger.warning("⚠️  slowapi not installed - rate limiting disabled. Install with: pip install slowapi")
    limiter = None

# Include route routers
app.include_router(auth_router)  #  Unified authentication (JWT, OAuth, GitHub)
app.include_router(task_router)  # Task management endpoints
# Register unified content router (replaces 3 legacy routers)
app.include_router(content_router)
app.include_router(cms_router)  # Simple CMS API (replaces Strapi)

# Register models router
app.include_router(models_router)
app.include_router(models_list_router)  # Legacy /api/models endpoint support
app.include_router(settings_router)  # Settings management
app.include_router(command_queue_router)  # Command queue (replaces Pub/Sub)
app.include_router(chat_router)  # Chat and AI model integration
app.include_router(ollama_router)  # Ollama health checks and warm-up
app.include_router(subtask_router)  # Subtask independent execution (Phase 2 - unified orchestration)
app.include_router(bulk_task_router)  # Bulk task operations (multiple tasks at once)
app.include_router(webhook_router)  # Webhook event handlers
app.include_router(social_router)  # Social media management
app.include_router(metrics_router)  # Metrics and analytics
app.include_router(agents_router)  # AI agent management and monitoring

# Register workflow history routes (Phase 5 - database persistence)
if WORKFLOW_HISTORY_AVAILABLE and workflow_history_router:
    app.include_router(workflow_history_router)  # Workflow history tracking
    logger.info(" Workflow history routes registered")
else:
    logger.warning(" Workflow history routes not registered (module not available)")

# Register intelligent orchestrator routes (NEW - conditional on availability)
if INTELLIGENT_ORCHESTRATOR_AVAILABLE and intelligent_orchestrator_router:
    app.include_router(intelligent_orchestrator_router)
    logger.info(" Intelligent orchestrator routes registered")
else:
    logger.warning(" Intelligent orchestrator routes not registered (module not available)")

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
    global startup_error, startup_complete
    
    try:
        # Build comprehensive health response
        health_data = {
            "status": "healthy",
            "service": "cofounder-agent",
            "version": "1.0.0",
            "timestamp": datetime.utcnow().isoformat(),
            "components": {}
        }
        
        # Check startup status
        if startup_error:
            health_data["status"] = "degraded"
            health_data["startup_error"] = startup_error
            health_data["startup_complete"] = startup_complete
            logger.warning(f"Health check returning degraded status: {startup_error}")
        elif not startup_complete:
            health_data["status"] = "starting"
            health_data["startup_complete"] = False
        
        # Include database status if available
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
        return {
            "status": "unhealthy",
            "service": "cofounder-agent",
            "error": str(e)
        }

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
                "total_cost": 0.0
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
            "error": str(e)
        }

@app.get("/api/debug/startup")
async def debug_startup():
    """
    Debug endpoint showing startup status and any errors
    Only available in development mode
    """
    global startup_error, startup_complete
    
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
            metadata=response.get("metadata")
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
            "service": "online" if health.get("status") == "healthy" else health.get("status", "unknown"),
            "database_available": database_service is not None,
            "orchestrator_initialized": orchestrator is not None,
            "timestamp": health.get("timestamp", str(asyncio.get_event_loop().time()))
        }
        
        # Include component statuses if available
        components = health.get("components", {})
        if isinstance(components, dict):
            for key, value in components.items():
                status_data[f"component_{key}"] = value
        
        return StatusResponse(status=health.get("status", "unknown"), data=status_data)
        
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return StatusResponse(
            status="unhealthy", 
            data={"error": str(e)}
        )

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
            "status": "success" if health.get("status") == "healthy" else "degraded"
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
        "database_enabled": database_service is not None
    }

if __name__ == "__main__":
    # Watch the entire src directory for changes to support agent development
    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    print(f"INFO:     Configured watch directory: {src_dir}")
    
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        reload_dirs=[src_dir],
        log_level="info"
    )
