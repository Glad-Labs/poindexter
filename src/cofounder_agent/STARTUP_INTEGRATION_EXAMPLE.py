"""
FastAPI Integration Example - Using StartupManager in main.py

This demonstrates the recommended way to integrate the StartupManager
into your FastAPI application for proper initialization and cleanup.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# OPTION 1: Using FastAPI lifespan context manager (Recommended - FastAPI 0.93+)
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager for startup and shutdown.
    
    This is the recommended approach for FastAPI 0.93+ as it:
    - Provides clear separation of startup/shutdown logic
    - Automatically handles cleanup on application termination
    - Integrates well with async/await patterns
    - Works correctly with graceful shutdown signals
    """
    # Startup phase
    logger.info("=" * 60)
    logger.info("STARTUP PHASE")
    logger.info("=" * 60)
    
    from utils.startup_manager import StartupManager
    
    startup_manager = StartupManager()
    services = await startup_manager.initialize_all_services()
    
    # Store services in app state for access in routes
    app.state.database = services['database']
    app.state.orchestrator = services['orchestrator']
    app.state.task_executor = services['task_executor']
    app.state.intelligent_orchestrator = services['intelligent_orchestrator']
    app.state.workflow_history = services['workflow_history']
    app.state.startup_manager = startup_manager
    app.state.startup_error = services['startup_error']
    
    yield  # Application runs here
    
    # Shutdown phase
    logger.info("=" * 60)
    logger.info("SHUTDOWN PHASE")
    logger.info("=" * 60)
    await startup_manager.shutdown()


# Create FastAPI app with lifespan
app = FastAPI(
    title="Glad Labs AI Co-Founder",
    description="Comprehensive AI co-founder and advisor system",
    version="1.0.0",
    lifespan=lifespan
)


# ============================================================================
# OPTION 2: Using FastAPI event handlers (For FastAPI < 0.93)
# ============================================================================
"""
If using FastAPI older than 0.93, use this approach instead:

startup_manager = None

@app.on_event("startup")
async def startup_event():
    global startup_manager
    from utils.startup_manager import StartupManager
    
    startup_manager = StartupManager()
    services = await startup_manager.initialize_all_services()
    
    # Store services in app state
    app.state.database = services['database']
    app.state.orchestrator = services['orchestrator']
    app.state.task_executor = services['task_executor']
    app.state.intelligent_orchestrator = services['intelligent_orchestrator']
    app.state.workflow_history = services['workflow_history']
    app.state.startup_error = services['startup_error']

@app.on_event("shutdown")
async def shutdown_event():
    global startup_manager
    if startup_manager:
        await startup_manager.shutdown()
"""


# ============================================================================
# Helper functions for accessing services in routes
# ============================================================================

def get_database_service():
    """Get database service from app state"""
    if not hasattr(app.state, 'database') or app.state.database is None:
        raise RuntimeError("Database service not initialized")
    return app.state.database


def get_orchestrator():
    """Get orchestrator from app state"""
    if not hasattr(app.state, 'orchestrator') or app.state.orchestrator is None:
        raise RuntimeError("Orchestrator not initialized")
    return app.state.orchestrator


def get_intelligent_orchestrator():
    """Get intelligent orchestrator from app state"""
    if not hasattr(app.state, 'intelligent_orchestrator'):
        return None
    return app.state.intelligent_orchestrator


def get_task_executor():
    """Get task executor from app state"""
    if not hasattr(app.state, 'task_executor'):
        return None
    return app.state.task_executor


def get_workflow_history():
    """Get workflow history service from app state"""
    if not hasattr(app.state, 'workflow_history'):
        return None
    return app.state.workflow_history


# ============================================================================
# Health check endpoint to verify startup status
# ============================================================================

@app.get("/health")
async def health_check():
    """
    Health check endpoint that verifies all services are initialized.
    
    Returns detailed information about each component's status.
    """
    return {
        "status": "healthy" if not app.state.startup_error else "degraded",
        "services": {
            "database": app.state.database is not None,
            "orchestrator": app.state.orchestrator is not None,
            "intelligent_orchestrator": app.state.intelligent_orchestrator is not None,
            "task_executor": (
                app.state.task_executor is not None 
                and app.state.task_executor.running
            ),
            "workflow_history": app.state.workflow_history is not None,
        },
        "startup_error": app.state.startup_error
    }


# ============================================================================
# Example routes using the initialized services
# ============================================================================

@app.get("/api/v1/tasks")
async def list_tasks(skip: int = 0, limit: int = 10):
    """
    Example endpoint that uses the database service.
    
    This demonstrates how to access initialized services in routes.
    """
    db = get_database_service()
    
    try:
        # Query tasks from PostgreSQL
        query = """
            SELECT id, title, description, status 
            FROM tasks 
            ORDER BY created_at DESC 
            LIMIT $1 OFFSET $2
        """
        tasks = await db.pool.fetch(query, limit, skip)
        
        return {
            "status": "success",
            "data": [dict(task) for task in tasks],
            "total": len(tasks)
        }
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e)
        }


@app.post("/api/v1/orchestrator/execute")
async def execute_with_orchestrator(prompt: str):
    """
    Example endpoint that uses the orchestrator service.
    
    Demonstrates how to execute workflows through the orchestrator.
    """
    orchestrator = get_orchestrator()
    
    try:
        # Execute orchestrator workflow
        result = await orchestrator.execute(prompt)
        
        return {
            "status": "success",
            "result": result
        }
    except Exception as e:
        logger.error(f"Orchestrator execution failed: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e)
        }


# ============================================================================
# Middleware to inject services into request state (optional)
# ============================================================================

@app.middleware("http")
async def inject_services_middleware(request, call_next):
    """
    Optional middleware to inject services into request state.
    
    This allows routes to access services via request.state instead of
    directly accessing app.state.
    """
    request.state.database = app.state.database
    request.state.orchestrator = app.state.orchestrator
    request.state.intelligent_orchestrator = app.state.intelligent_orchestrator
    request.state.task_executor = app.state.task_executor
    request.state.workflow_history = app.state.workflow_history
    
    response = await call_next(request)
    return response


# ============================================================================
# Root endpoint
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint with API documentation"""
    return {
        "name": "Glad Labs AI Co-Founder",
        "version": "1.0.0",
        "status": "running" if not app.state.startup_error else "degraded",
        "docs": "/docs",
        "health": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    
    # Run the application
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # Disable reload in production
        log_level="info"
    )
