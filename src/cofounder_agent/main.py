"""
GLAD Labs AI Co-Founder Agent
FastAPI application serving as the central orchestrator for the GLAD Labs ecosystem
Implements Google-native stack with Firestore and Pub/Sub integration
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

# Try to import Google Cloud services (may not be available in dev)
try:
    from services.firestore_client import FirestoreClient
    from services.pubsub_client import PubSubClient
    from services.performance_monitor import PerformanceMonitor
    GOOGLE_CLOUD_AVAILABLE = True
except ImportError:
    FirestoreClient = None
    PubSubClient = None
    PerformanceMonitor = None
    GOOGLE_CLOUD_AVAILABLE = False
    logging.warning("Google Cloud services not available - running in development mode")

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
firestore_client: Optional[object] = None
pubsub_client: Optional[object] = None
orchestrator: Optional[Orchestrator] = None
performance_monitor: Optional[object] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for service initialization and cleanup"""
    global firestore_client, pubsub_client, orchestrator, performance_monitor
    
    try:
        # Initialize services
        logger.info("Initializing GLAD Labs AI Co-Founder services")
        
        if GOOGLE_CLOUD_AVAILABLE:
            # Initialize Firestore client
            firestore_client = FirestoreClient()  # type: ignore[call-arg]
            
            # Initialize performance monitor with Firestore
            performance_monitor = PerformanceMonitor(firestore_client=firestore_client)  # type: ignore[call-arg]
            
            # Initialize Pub/Sub client
            pubsub_client = PubSubClient()  # type: ignore[call-arg]
            await pubsub_client.ensure_topics_exist()
            
            # Update agent status
            await firestore_client.update_agent_status("cofounder", {  # type: ignore[union-attr]
                "status": "online",
                "service_version": "1.0.0",
                "capabilities": ["command_processing", "agent_orchestration", "task_management", "performance_monitoring"]
            })
        else:
            # Development mode - initialize performance monitor without Firestore
            performance_monitor = PerformanceMonitor() if PerformanceMonitor else None
        
        # Initialize orchestrator with services
        orchestrator = Orchestrator(
            firestore_client=firestore_client,
            pubsub_client=pubsub_client
        )
        
        logger.info("GLAD Labs AI Co-Founder services initialized successfully")
        
        yield
        
    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        raise
    finally:
        # Cleanup services
        logger.info("Shutting down GLAD Labs AI Co-Founder services")
        
        if GOOGLE_CLOUD_AVAILABLE and firestore_client:
            try:
                await firestore_client.update_agent_status("cofounder", {  # type: ignore[union-attr]
                    "status": "offline"
                })
            except Exception as e:
                logger.error(f"Error updating offline status: {e}")
        
        if GOOGLE_CLOUD_AVAILABLE and pubsub_client:
            try:
                await pubsub_client.close()  # type: ignore[union-attr]
            except Exception as e:
                logger.error(f"Error closing pub/sub: {e}")

app = FastAPI(
    title="GLAD Labs AI Co-Founder",
    description="Central orchestrator for GLAD Labs AI-driven business operations with Google Cloud integration",
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
        
        # Use async version if Google Cloud services are available
        if GOOGLE_CLOUD_AVAILABLE and orchestrator.firestore_client and orchestrator.pubsub_client:
            response = await orchestrator.process_command_async(request.command, request.context)
        else:
            # Fall back to synchronous version for development
            response = orchestrator.process_command(request.command, request.context)
        
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
        
        if not GOOGLE_CLOUD_AVAILABLE or not firestore_client:
            # Development mode - simulate task creation
            task_id = f"dev-task-{hash(request.topic)}"
            return TaskResponse(
                task_id=task_id,
                status="created",
                message=f"Task created for '{request.topic}' (development mode)"
            )
        
        # Create task in Firestore
        task_data = {
            "topic": request.topic,
            "task_type": request.task_type,
            "metadata": request.metadata or {},
            "status": "pending"
        }
        
        task_id = await firestore_client.add_task(task_data)  # type: ignore[union-attr]
        
        # Optionally trigger content agent if it's a content task
        if request.task_type == "content_creation" and pubsub_client:
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
            "google_cloud_available": GOOGLE_CLOUD_AVAILABLE,
            "orchestrator_initialized": orchestrator is not None,
            "timestamp": str(asyncio.get_event_loop().time())
        }
        
        if GOOGLE_CLOUD_AVAILABLE:
            # Add Google Cloud service health checks
            if firestore_client:
                firestore_health = await firestore_client.health_check()  # type: ignore[union-attr]
                status_data["firestore"] = firestore_health
            
            if pubsub_client:
                pubsub_health = await pubsub_client.health_check()  # type: ignore[union-attr]
                status_data["pubsub"] = pubsub_health
        
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
        if not GOOGLE_CLOUD_AVAILABLE or not firestore_client:
            return {"tasks": [], "message": "Running in development mode"}
        
        tasks = await firestore_client.get_pending_tasks(limit)  # type: ignore[union-attr]
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
        if performance_monitor:
            metrics = await performance_monitor.get_performance_summary(hours=hours)  # type: ignore[union-attr]
            return {"metrics": metrics, "status": "success"}
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
        if performance_monitor:
            health = await performance_monitor.get_health_metrics()  # type: ignore[union-attr]
            return {"health": health, "status": "success"}
        else:
            return {
                "health": {
                    "overall_status": "unknown",
                    "message": "Performance monitoring not available"
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
        if performance_monitor:
            performance_monitor.reset_session_metrics()  # type: ignore[union-attr]
            return {"message": "Performance metrics reset successfully", "status": "success"}
        else:
            return {"message": "Performance monitoring not available", "status": "disabled"}
    except Exception as e:
        logger.error(f"Error resetting metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reset metrics: {str(e)}")

@app.get("/")
async def root():
    """
    Root endpoint to confirm the server is running.
    """
    return {
        "message": "GLAD Labs AI Co-Founder is running",
        "version": "1.0.0",
        "google_cloud_enabled": GOOGLE_CLOUD_AVAILABLE
    }

async def trigger_content_agent(task_id: str, topic: str, metadata: Optional[Dict[str, Any]]):
    """Background task to trigger content agent via Pub/Sub"""
    try:
        if pubsub_client:
            content_request = {
                "task_id": task_id,
                "topic": topic,
                "type": "blog_post",
                "metadata": metadata or {}
            }
            
            message_id = await pubsub_client.publish_content_request(content_request)  # type: ignore[union-attr]
            logger.info(f"Content agent triggered: task_id={task_id} topic={topic} message_id={message_id}")
            
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
