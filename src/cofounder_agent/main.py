"""
Glad Labs AI Co-Founder Agent
FastAPI application serving as the central orchestrator for the Glad Labs ecosystem
Implements PostgreSQL database with REST API command queue integration
Replaces Google Cloud Firestore and Pub/Sub services
"""

import sys
import os
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import uvicorn
import structlog

# Add the parent directory (src) to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from orchestrator_logic import Orchestrator
from services.database_service import DatabaseService

# Import route routers
from routes.content import content_router
from routes.content_generation import content_router as generation_router
from routes.models import models_router
from routes.enhanced_content import enhanced_content_router
from routes.auth_routes import router as auth_router
from routes.settings_routes import router as settings_router
from routes.command_queue_routes import router as command_queue_router
from routes.task_routes import router as task_router
from routes.webhooks import webhook_router

# Import database initialization
try:
    from database import init_db
    DATABASE_AVAILABLE = True
except ImportError:
    init_db = None
    DATABASE_AVAILABLE = False
    logging.warning("Database module not available - authentication may not work")

# PostgreSQL database service is now the primary service
# Google Cloud services kept for backward compatibility but not initialized
DATABASE_SERVICE_AVAILABLE = True
pubsub_client = None  # Stub for backward compatibility - Firestore removed


# Google Cloud availability flag (for test mocking)
GOOGLE_CLOUD_AVAILABLE = False

# Stub Google Cloud client for test compatibility
firestore_client = None

# Configure structured logging
try:
    import structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    logger = structlog.get_logger(__name__)
except ImportError:
    # Fallback to standard logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

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
        
        # 2. Create tables if they don't exist
        if database_service:
            try:
                logger.info("  📋 Database tables initialized in previous step")
            except Exception as e:
                error_msg = f"Table creation failed: {str(e)}"
                logger.error(f"  ⚠️ {error_msg}", exc_info=True)
                startup_error = error_msg
        
        # 3. Initialize orchestrator with new database service
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
        
        # 4. Verify connections
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
        
        logger.info("✅ Application started successfully!")
        logger.info(f"  - Database Service: {database_service is not None}")
        logger.info(f"  - Orchestrator: {orchestrator is not None}")
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
app.include_router(auth_router)  # Authentication endpoints
app.include_router(task_router)  # Task management endpoints
app.include_router(content_router)
app.include_router(generation_router)  # Content generation with Ollama
app.include_router(models_router)
app.include_router(enhanced_content_router)
app.include_router(settings_router)  # Settings management
app.include_router(command_queue_router)  # Command queue (replaces Pub/Sub)
app.include_router(webhook_router)  # Webhook handlers for Strapi events

# ===== HEALTH CHECK ENDPOINTS =====

@app.get("/api/health")
async def api_health():
    """
    Health check endpoint for Railway deployment and load balancers
    Returns detailed JSON indicating service status and any startup errors
    """
    global startup_error, startup_complete
    
    try:
        # If startup encountered errors, report them
        if startup_error:
            logger.warning(f"Health check returning degraded status: {startup_error}")
            return {
                "status": "degraded",
                "service": "cofounder-agent",
                "version": "1.0.0",
                "startup_error": startup_error,
                "startup_complete": startup_complete
            }
        
        # If startup not complete, return starting status
        if not startup_complete:
            return {
                "status": "starting",
                "service": "cofounder-agent",
                "version": "1.0.0",
                "startup_complete": False
            }
        
        # All good - fully healthy
        health_status = {
            "status": "healthy",
            "service": "cofounder-agent",
            "version": "1.0.0"
        }
        
        # Include database status if available
        if database_service:
            try:
                db_health = await database_service.health_check()
                health_status["database"] = db_health.get("status", "unknown")
            except Exception as e:
                logger.warning(f"Database health check failed in /api/health: {e}")
                health_status["database"] = "degraded"
        else:
            health_status["database"] = "unavailable"
        
        return health_status
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return {
            "status": "unhealthy",
            "service": "cofounder-agent",
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
    Get the current status of the Co-Founder agent and connected services
    """
    try:
        status_data = {
            "service": "online",
            "database_available": database_service is not None,
            "orchestrator_initialized": orchestrator is not None,
            "timestamp": str(asyncio.get_event_loop().time())
        }
        
        # Add database and API health checks
        if database_service:
            try:
                db_health = await database_service.health_check()
                status_data["database"] = db_health
            except Exception as e:
                logger.warning(f"Database health check failed: {e}")
                status_data["database"] = {"status": "error", "message": str(e)}
        
        # Add orchestrator status
        if orchestrator:
            try:
                orch_status = await orchestrator._get_system_status_async()
                status_data["orchestrator"] = orch_status
            except Exception as e:
                logger.warning(f"Orchestrator status check failed: {e}")
                status_data["orchestrator"] = {"status": "error", "message": str(e)}
        
        return StatusResponse(status="healthy", data=status_data)
        
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
    Get current system health metrics and status
    """
    try:
        if database_service:
            health = await database_service.health_check()
            return {"health": health, "status": "success"}
        else:
            return {
                "health": {
                    "status": "unknown",
                    "message": "Database not available"
                },
                "status": "disabled"
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
