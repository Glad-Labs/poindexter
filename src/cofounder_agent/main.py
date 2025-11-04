"""
Glad Labs AI Agent - Poindexter
FastAPI application serving as the central orchestrator for the Glad Labs ecosystem
Implements PostgreSQL database with REST API command queue integration
Replaces Google Cloud Firestore and Pub/Sub services
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

# Import route routers
# Unified content router (consolidates content.py, content_generation.py, enhanced_content.py)
from routes.content_routes import content_router
from routes.models import models_router, models_list_router
from routes.auth import router as github_oauth_router
from routes.auth_routes import router as auth_router
from routes.settings_routes import router as settings_router
from routes.command_queue_routes import router as command_queue_router
from routes.chat_routes import router as chat_router
from routes.ollama_routes import router as ollama_router
from routes.task_routes import router as task_router
from routes.webhooks import webhook_router
from routes.social_routes import social_router
from routes.metrics_routes import metrics_router
from routes.agents_routes import router as agents_router

# Import database initialization
try:
    from database import init_db
    DATABASE_AVAILABLE = True
except ImportError:
    init_db = None
    DATABASE_AVAILABLE = False
    logging.warning("Database module not available - authentication may not work")

# PostgreSQL database service is now the primary service
DATABASE_SERVICE_AVAILABLE = True

# Flag for Google Cloud availability (for test mocking)
# Google Cloud services have been replaced with PostgreSQL + task store
GOOGLE_CLOUD_AVAILABLE = False

# Placeholder for firestore_client (for backward compatibility with tests)
# Actual implementation uses PostgreSQL through database_service
firestore_client = None

# Use centralized logging configuration
from services.logger_config import get_logger
from services.task_store_service import initialize_task_store, get_persistent_task_store
from services.model_consolidation_service import initialize_model_consolidation_service

logger = get_logger(__name__)

# Global service instances
database_service: Optional[DatabaseService] = None
orchestrator: Optional[Orchestrator] = None
startup_error: Optional[str] = None
startup_complete: bool = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - startup and shutdown with PostgreSQL initialization"""
    global database_service, orchestrator, startup_error, startup_complete
    
    try:
        logger.info("🚀 Starting Glad Labs AI Co-Founder application...")
        logger.info(f"  Environment: {os.getenv('ENVIRONMENT', 'development')}")
        logger.info(f"  Database URL: {os.getenv('DATABASE_URL', 'SQLite (local dev)')[:50]}...")
        
        # 1. Initialize PostgreSQL database service
        logger.info("  📦 Connecting to PostgreSQL...")
        try:
            database_service = DatabaseService()
            await database_service.initialize()
            logger.info("  ✅ PostgreSQL connection established")
        except Exception as e:
            startup_error = f"PostgreSQL connection failed: {str(e)}"
            logger.error(f"  ❌ {startup_error}", exc_info=True)
            logger.warning("  ⚠️ Continuing in development mode without database")
            database_service = None
        
        # 2. Initialize persistent task store
        logger.info("  📋 Initializing persistent task store...")
        try:
            database_url = os.getenv("DATABASE_URL", "sqlite:///.tmp/data.db")
            initialize_task_store(database_url)
            logger.info("  ✅ Persistent task store initialized")
        except Exception as e:
            error_msg = f"Task store initialization failed: {str(e)}"
            logger.error(f"  ⚠️ {error_msg}", exc_info=True)
            startup_error = error_msg
            # Don't re-raise - allow app to start for health checks
        
        # 3. Initialize unified model consolidation service
        logger.info("  🧠 Initializing unified model consolidation service...")
        try:
            initialize_model_consolidation_service()
            logger.info("  ✅ Model consolidation service initialized (Ollama→HF→Google→Anthropic→OpenAI)")
        except Exception as e:
            error_msg = f"Model consolidation initialization failed: {str(e)}"
            logger.error(f"  ⚠️ {error_msg}", exc_info=True)
            # Don't fail startup - models are optional
        
        # 4. Create tables if they don't exist
        if database_service:
            try:
                logger.info("  📋 Database tables initialized in previous step")
            except Exception as e:
                error_msg = f"Table creation failed: {str(e)}"
                logger.error(f"  ⚠️ {error_msg}", exc_info=True)
                startup_error = error_msg
        
        # 4. Initialize orchestrator with new database service
        api_base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
        logger.info(f"  🤖 Initializing orchestrator (API: {api_base_url})...")
        try:
            orchestrator = Orchestrator(
                database_service=database_service,
                api_base_url=api_base_url
            )
            logger.info("  ✅ Orchestrator initialized successfully")
        except Exception as e:
            error_msg = f"Orchestrator initialization failed: {str(e)}"
            logger.error(f"  ❌ {error_msg}", exc_info=True)
            startup_error = error_msg
            # Don't re-raise - allow app to start for health checks
        
        # 5. Verify connections
        if database_service:
            try:
                logger.info("  🔍 Verifying database connection...")
                health = await database_service.health_check()
                if health.get("status") == "healthy":
                    logger.info(f"  ✅ Database health check passed")
                else:
                    logger.warning(f"  ⚠️ Database health check returned: {health}")
            except Exception as e:
                logger.warning(f"  ⚠️ Database health check failed: {e}", exc_info=True)
        
        # 6. Register database service with route modules
        if database_service:
            from routes.task_routes import set_db_service
            set_db_service(database_service)
            logger.info("  ✅ Database service registered with routes")
        
        logger.info("✅ Application started successfully!")
        logger.info(f"  - Database Service: {database_service is not None}")
        logger.info(f"  - Orchestrator: {orchestrator is not None}")
        logger.info(f"  - Task Store: initialized")
        logger.info(f"  - Startup Error: {startup_error}")
        logger.info(f"  - API Base URL: {api_base_url}")
        
        startup_complete = True
        yield  # Application runs here
        
    except Exception as e:
        startup_error = f"Critical startup failure: {str(e)}"
        logger.error(f"❌ {startup_error}", exc_info=True)
        startup_complete = True  # Mark complete so /api/health works
    
    finally:
        # ===== SHUTDOWN =====
        try:
            logger.info("🛑 Shutting down Glad Labs AI Co-Founder application...")
            
            # Close task store
            try:
                logger.info("  Closing persistent task store...")
                task_store = get_persistent_task_store()
                if task_store:
                    task_store.close()
                    logger.info("  ✅ Task store connection closed")
            except Exception as e:
                logger.error(f"  ⚠️ Error closing task store: {e}", exc_info=True)
            
            if database_service:
                try:
                    logger.info("  Closing database connection...")
                    await database_service.close()
                    logger.info("  ✅ Database connection closed")
                except Exception as e:
                    logger.error(f"  ⚠️ Error closing database: {e}", exc_info=True)
            
            logger.info("✅ Application shut down successfully!")
            
        except Exception as e:
            logger.error(f"❌ Error during shutdown: {e}", exc_info=True)

app = FastAPI(
    title="Glad Labs AI Co-Founder",
    description="Central orchestrator for Glad Labs AI-driven business operations with Google Cloud integration",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],  # React apps
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include route routers
app.include_router(github_oauth_router)  # GitHub OAuth authentication
app.include_router(auth_router)  # Traditional authentication endpoints
app.include_router(task_router)  # Task management endpoints
# Register unified content router (replaces 3 legacy routers)
app.include_router(content_router)

# Register models router
app.include_router(models_router)
app.include_router(models_list_router)  # Legacy /api/models endpoint support
app.include_router(settings_router)  # Settings management
app.include_router(command_queue_router)  # Command queue (replaces Pub/Sub)
app.include_router(chat_router)  # Chat and AI model integration
app.include_router(ollama_router)  # Ollama health checks and warm-up
app.include_router(webhook_router)  # Webhook handlers for Strapi events
app.include_router(social_router)  # Social media management
app.include_router(metrics_router)  # Metrics and analytics
app.include_router(agents_router)  # AI agent management and monitoring

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

class TaskRequest(BaseModel):
    topic: str
    task_type: str
    metadata: Optional[Dict[str, Any]] = None

class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str

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

@app.post("/tasks", response_model=TaskResponse)
async def create_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """
    Create a new task for content creation or business operations
    """
    try:
        logger.info(f"Creating task: topic={request.topic} type={request.task_type}")
        
        if not database_service:
            # Development mode - simulate task creation
            task_id = f"dev-task-{hash(request.topic)}"
            return TaskResponse(
                task_id=task_id,
                status="created",
                message=f"Task created for '{request.topic}' (development mode)"
            )
        
        # Create task in PostgreSQL database
        task_data = {
            "topic": request.topic,
            "task_type": request.task_type,
            "metadata": request.metadata or {},
            "status": "pending"
        }
        
        task_id = await database_service.add_task(task_data)
        
        # Optionally trigger content agent if it's a content task
        if request.task_type == "content_creation":
            background_tasks.add_task(
                trigger_content_agent,
                task_id,
                request.topic,
                request.metadata
            )
        
        return TaskResponse(
            task_id=task_id,
            status="created",
            message=f"Task created for '{request.topic}'"
        )
        
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")

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
            await database_service.add_log_entry("info", "Performance metrics reset")
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

async def trigger_content_agent(task_id: str, topic: str, metadata: Optional[Dict[str, Any]]):
    """Background task to trigger content agent via command queue API"""
    try:
        if orchestrator:
            content_request = {
                "task_id": task_id,
                "topic": topic,
                "type": "blog_post",
                "metadata": metadata or {}
            }
            
            # Dispatch via REST API instead of Pub/Sub
            await orchestrator.run_content_pipeline_async(topic, metadata)
            logger.info(f"Content agent triggered: task_id={task_id} topic={topic}")
            
    except Exception as e:
        logger.error(f"Failed to trigger content agent: task_id={task_id} error={e}")

if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        log_level="info"
    )
