"""
POINDEXTER INTEGRATION GUIDE

How to wire Poindexter into your existing FastAPI application (main.py)
"""

# ==============================================================================
# INTEGRATION STEPS FOR main.py
# ==============================================================================

"""
Step 1: Import Poindexter components at top of main.py
"""

from src.cofounder_agent.services.poindexter_orchestrator import Poindexter
from src.cofounder_agent.services.poindexter_tools import PoindexterTools
from src.cofounder_agent.services.mcp_discovery import Poindexter_MCPIntegration
from src.cofounder_agent.routes.poindexter_routes import (
    poindexter_router,
    set_poindexter_instance,
    HealthResponse
)


"""
Step 2: In your FastAPI app startup (e.g., @app.on_event("startup"))
"""

@app.on_event("startup")
async def startup_poindexter():
    """Initialize Poindexter orchestrator on app startup."""
    
    try:
        logger.info("[POINDEXTER] Initializing orchestrator...")
        
        # 1. Initialize MCP discovery
        mcp_integration = Poindexter_MCPIntegration()
        logger.info("[POINDEXTER] MCP discovery initialized")
        
        # 2. Initialize Poindexter tools (wrap existing agents)
        poindexter_tools = PoindexterTools(
            agent_factory=app.agent_factory,  # Your existing agent factory
            model_router=app.model_router,    # Your existing model router
            constraint_config={
                "default_budget": 5.00,
                "default_quality_threshold": 0.85,
                "default_max_runtime": 600  # seconds
            }
        )
        logger.info("[POINDEXTER] Tool set initialized")
        
        # 3. Initialize Poindexter orchestrator
        poindexter = Poindexter(
            model_router=app.model_router,
            agent_factory=app.agent_factory,
            mcp_integration=mcp_integration
        )
        logger.info("[POINDEXTER] Orchestrator initialized successfully")
        
        # 4. Make Poindexter available to routes
        set_poindexter_instance(poindexter)
        
        # 5. Store in app state for access from other modules
        app.poindexter = poindexter
        app.poindexter_tools = poindexter_tools
        app.mcp_integration = mcp_integration
        
        logger.info("[POINDEXTER] ✅ Ready for orchestration")
    
    except Exception as e:
        logger.error(f"[POINDEXTER] Initialization failed: {e}", exc_info=True)
        # Don't crash app if Poindexter fails, but log it
        app.poindexter = None


"""
Step 3: Register Poindexter routes in main FastAPI app
"""

# Add this after creating your FastAPI app instance
app.include_router(poindexter_router)

# This registers:
# - POST   /api/v2/orchestrate
# - GET    /api/v2/orchestrate/{workflow_id}
# - GET    /api/v2/orchestrate-status


"""
Step 4: Ensure backward compatibility with existing endpoints
"""

# Your existing routes should continue to work:
# - POST /api/tasks              (existing task creation)
# - GET  /api/tasks
# - POST /api/content/generate-blog-post  (existing content endpoints)
# - etc.

# Poindexter is additive - it doesn't replace existing APIs, just adds v2 endpoints


"""
Step 5: Add Poindexter to your health check
"""

@app.get("/api/health")
async def health_check():
    """Check system health including Poindexter status."""
    
    health = {
        "status": "healthy",
        "services": {
            "fastapi": "running",
            "database": "connected",
            "strapi": "online",
            "poindexter": "initializing"
        }
    }
    
    # Check Poindexter
    if hasattr(app, 'poindexter') and app.poindexter is not None:
        health["services"]["poindexter"] = "ready"
    else:
        health["services"]["poindexter"] = "offline"
        health["status"] = "degraded"
    
    return health


"""
Step 6 (OPTIONAL): Add admin endpoint to view Poindexter metrics
"""

@app.get("/api/admin/poindexter-metrics")
async def get_poindexter_metrics():
    """Get Poindexter orchestration metrics."""
    
    if not hasattr(app, 'poindexter') or app.poindexter is None:
        return {"error": "Poindexter not available"}
    
    return {
        "metrics": app.poindexter.metrics,
        "status": "running"
    }


# ==============================================================================
# EXAMPLE: Using Poindexter from your app
# ==============================================================================

@app.post("/api/custom-orchestration")
async def custom_orchestration(command: str):
    """Example of using Poindexter directly in a route."""
    
    if not hasattr(app, 'poindexter') or app.poindexter is None:
        return {"error": "Poindexter not ready"}
    
    result = await app.poindexter.orchestrate(
        command=command,
        constraints={"budget": 1.00, "quality_threshold": 0.90}
    )
    
    return result


# ==============================================================================
# DEPENDENCY INJECTION PATTERN (if using FastAPI dependencies)
# ==============================================================================

from fastapi import Depends, HTTPException

def get_poindexter(request: Request):
    """Get Poindexter instance via dependency injection."""
    poindexter = request.app.poindexter
    if poindexter is None:
        raise HTTPException(status_code=503, detail="Poindexter not initialized")
    return poindexter


# Usage in route:
@app.post("/api/v3/orchestrate-advanced")
async def advanced_orchestration(
    command: str,
    poindexter: Poindexter = Depends(get_poindexter)
):
    """Advanced orchestration using dependency injection."""
    result = await poindexter.orchestrate(command=command)
    return result


# ==============================================================================
# CONFIGURATION MANAGEMENT (for different environments)
# ==============================================================================

class PoindexterConfig:
    """Configuration for Poindexter based on environment."""
    
    def __init__(self, environment: str = "development"):
        self.environment = environment
        
        if environment == "development":
            self.config = {
                "max_orchestration_steps": 10,
                "default_quality_threshold": 0.85,
                "default_budget": 5.00,
                "enable_critique": True,
                "max_critique_iterations": 3,
                "log_level": "DEBUG"
            }
        elif environment == "production":
            self.config = {
                "max_orchestration_steps": 20,
                "default_quality_threshold": 0.92,
                "default_budget": 100.00,
                "enable_critique": True,
                "max_critique_iterations": 5,
                "log_level": "INFO"
            }


# Usage in startup:
@app.on_event("startup")
async def startup_with_config():
    """Initialize Poindexter with environment-specific config."""
    
    config = PoindexterConfig(environment=os.getenv("ENVIRONMENT", "development"))
    
    # ... initialize Poindexter with config.config ...


# ==============================================================================
# SHUTDOWN HANDLER (for cleanup)
# ==============================================================================

@app.on_event("shutdown")
async def shutdown_poindexter():
    """Clean up Poindexter resources on shutdown."""
    
    if hasattr(app, 'poindexter') and app.poindexter is not None:
        logger.info("[POINDEXTER] Shutting down...")
        
        # Close any connections
        if hasattr(app.poindexter, 'mcp_integration'):
            # Add any cleanup logic here
            pass
        
        logger.info("[POINDEXTER] Shutdown complete")


# ==============================================================================
# TESTING POINDEXTER INTEGRATION
# ==============================================================================

"""
Quick test to verify integration works:

1. Start your app:
   python -m uvicorn src.cofounder_agent.main:app --reload

2. Check health:
   curl http://localhost:8000/api/v2/orchestrate-status

3. Try a simple orchestration:
   curl -X POST http://localhost:8000/api/v2/orchestrate \
     -H "Content-Type: application/json" \
     -d '{
       "command": "Create a blog post about Python",
       "constraints": [
         {"name": "budget", "value": 0.50, "unit": "USD"}
       ]
     }'

4. Check workflow status:
   curl http://localhost:8000/api/v2/orchestrate/[workflow_id]

5. View existing endpoints still work:
   curl http://localhost:8000/api/health
   curl http://localhost:8000/api/tasks
"""


# ==============================================================================
# DEBUGGING CHECKLIST
# ==============================================================================

"""
If Poindexter doesn't work, check:

1. Imports
   - All imports in main.py are correct
   - No missing dependencies
   - PYTHONPATH includes src/

2. Initialization
   - Poindexter startup runs without error
   - MCP integration initializes
   - Agent factory is available
   - Model router is available

3. Routes
   - poindexter_router is included
   - Routes are registered at /api/v2/*
   - No route conflicts

4. Dependencies
   - smolagents installed: pip install smolagents
   - All required packages installed
   - Database connection works

5. Logging
   - Enable DEBUG logging to see Poindexter startup
   - Check logs for initialization errors
   - Monitor /api/v2/orchestrate-status endpoint

Debug endpoint to test:
   GET /api/v2/orchestrate-status

Should return:
   {
       "status": "healthy",
       "poindexter_ready": true,
       "smolagents_available": true,  // After pip install
       "mcp_available": true,
       "model_router_available": true,
       "agents_available": [...]
   }
"""


# ==============================================================================
# FULL INTEGRATION EXAMPLE (main.py)
# ==============================================================================

"""
Here's a minimal main.py showing complete integration:

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

# Poindexter imports
from src.cofounder_agent.services.poindexter_orchestrator import Poindexter
from src.cofounder_agent.services.poindexter_tools import PoindexterTools
from src.cofounder_agent.services.mcp_discovery import Poindexter_MCPIntegration
from src.cofounder_agent.routes.poindexter_routes import (
    poindexter_router,
    set_poindexter_instance
)

# Existing imports
from src.cofounder_agent.multi_agent_orchestrator import AgentFactory
from src.cofounder_agent.services.model_router import ModelRouter

logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="GLAD Labs Co-Founder Agent",
    version="3.0-poindexter"
)

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize components
app.agent_factory = AgentFactory()
app.model_router = ModelRouter()

# Register existing routes
from src.cofounder_agent.routes import (
    task_routes,
    content_routes,
    model_routes
)

app.include_router(task_routes.router)
app.include_router(content_routes.router)
app.include_router(model_routes.router)

# Register Poindexter routes (NEW)
app.include_router(poindexter_router)


@app.on_event("startup")
async def startup():
    """Initialize all services."""
    
    # Initialize Poindexter
    try:
        logger.info("Initializing Poindexter orchestrator...")
        
        mcp_integration = Poindexter_MCPIntegration()
        poindexter = Poindexter(
            model_router=app.model_router,
            agent_factory=app.agent_factory,
            mcp_integration=mcp_integration
        )
        
        set_poindexter_instance(poindexter)
        app.poindexter = poindexter
        
        logger.info("✅ Poindexter ready for orchestration")
    
    except Exception as e:
        logger.error(f"Failed to initialize Poindexter: {e}")


@app.get("/api/health")
async def health():
    """Health check."""
    return {
        "status": "healthy",
        "poindexter": "ready" if hasattr(app, 'poindexter') else "offline"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
"""


# ==============================================================================
# REQUIREMENTS TO ADD
# ==============================================================================

"""
Add to requirements.txt:

# Poindexter dependencies
smolagents>=0.3.0          # HuggingFace autonomous agent framework
httpx>=0.24.0              # Async HTTP client for MCP servers

# These should already be installed:
fastapi>=0.95.0
uvicorn>=0.21.0
pydantic>=2.0
"""


# ==============================================================================
# NEXT STEPS
# ==============================================================================

"""
1. Add Poindexter imports to main.py
2. Initialize in startup handler
3. Register routes
4. Add to health check
5. Test with curl
6. Deploy to staging
7. Run full integration tests
8. Monitor metrics
9. Promote to production
"""
