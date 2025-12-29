"""
Startup Migration Guide - Converting to StartupManager Pattern

This guide provides step-by-step instructions for integrating StartupManager
into your FastAPI application and migrating away from ad-hoc startup code.
"""

# ============================================================================

# MIGRATION OVERVIEW

# ============================================================================

"""
This guide walks through converting a FastAPI application to use the
StartupManager utility for centralized startup/shutdown orchestration.

BEFORE (Ad-hoc startup):

- Initialization code scattered across main.py
- Manual service instantiation with error handling
- No consistent shutdown sequence
- Difficult to test startup logic
- Hard to understand initialization order

AFTER (StartupManager pattern):

- Single initialization entry point
- Centralized configuration
- Consistent error handling
- Testable startup logic
- Clear initialization order
  """

# ============================================================================

# STEP 1: Update your main.py (Replace startup code)

# ============================================================================

"""
Replace your current startup/shutdown code with this pattern:

# OLD CODE TO REMOVE:

# Old initialization scattered throughout main.py

@app.on_event("startup")
async def startup(): # Database initialization
db_pool = await create_pool(...)

    # Orchestrator setup
    orchestrator = Orchestrator(...)

    # Task executor
    executor = TaskExecutor(...)

    # And many other services...
    app.state.db_pool = db_pool
    # ... store all services manually

# NEW CODE TO ADD:

"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from utils.startup_manager import StartupManager

@asynccontextmanager
async def lifespan(app: FastAPI): # Startup
startup_manager = StartupManager()
services = await startup_manager.initialize_all_services()

    # Inject into app state
    app.state.database = services['database']
    app.state.orchestrator = services['orchestrator']
    app.state.task_executor = services['task_executor']
    app.state.intelligent_orchestrator = services['intelligent_orchestrator']
    app.state.workflow_history = services['workflow_history']
    app.state.startup_manager = startup_manager
    app.state.startup_error = services['startup_error']

    yield  # App runs here

    # Shutdown
    await startup_manager.shutdown()

app = FastAPI(lifespan=lifespan)

"""
This removes all the scattered initialization code and replaces it with
a single, coordinated startup/shutdown sequence.
"""

# ============================================================================

# STEP 2: Update route files to use injected services

# ============================================================================

"""
BEFORE: Manual database access in routes
=========================================

# In routes/task_routes.py

from services.database_service import DatabaseService

db_service = None # Global - error-prone

def set_db_service(db):
global db_service
db_service = db

@app.get("/tasks")
async def list_tasks():
if db_service is None:
raise RuntimeError("Database not initialized")
tasks = await db_service.query(...)
return tasks

# AFTER: Access through app state

# In routes/task_routes.py

@app.get("/tasks")
async def list_tasks(request: Request):
db = request.state.database
tasks = await db.pool.fetch("SELECT \* FROM tasks")
return tasks

OR using dependency injection:

from fastapi import Depends

def get_database(request: Request):
if request.app.state.database is None:
raise RuntimeError("Database not initialized")
return request.app.state.database

@app.get("/tasks")
async def list_tasks(db = Depends(get_database)):
tasks = await db.pool.fetch("SELECT \* FROM tasks")
return tasks
"""

# ============================================================================

# STEP 3: Handle startup errors appropriately

# ============================================================================

"""
The StartupManager provides error information in app.state.startup_error

Use this to determine application readiness:
"""

@app.get("/health")
async def health_check():
"""
Health check that accounts for startup errors
"""
startup_error = getattr(app.state, 'startup_error', None)

    if startup_error:
        # Startup had critical errors
        return {
            "status": "error",
            "message": startup_error,
            "code": 503
        }, 503
    else:
        # All services initialized
        return {
            "status": "healthy",
            "services": {
                "database": app.state.database is not None,
                "orchestrator": app.state.orchestrator is not None,
                # ... other services
            }
        }

# ============================================================================

# STEP 4: Environment configuration

# ============================================================================

"""
Ensure these environment variables are set before startup:

## REQUIRED:

DATABASE_URL=postgresql://user:password@localhost:5432/glad_labs_dev
OR
DATABASE_USER=user
DATABASE_PASSWORD=password
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev

## OPTIONAL:

ENVIRONMENT=development|production (default: production)
API_BASE_URL=http://localhost:8000 (default: http://localhost:8000)
REDIS_URL=redis://localhost:6379 (default: not used if not set)
OLLAMA_URL=http://localhost:11434 (for local LLM)
"""

# ============================================================================

# STEP 5: Testing the startup

# ============================================================================

"""
To test the startup sequence without running the full server:
"""

import asyncio
from utils.startup_manager import StartupManager

async def test_startup():
"""Test startup without running web server"""
manager = StartupManager()

    try:
        services = await manager.initialize_all_services()
        print("Startup test PASSED")
        print(f"Database: {services['database'] is not None}")
        print(f"Orchestrator: {services['orchestrator'] is not None}")
        # ... check other services

        # Cleanup
        await manager.shutdown()
    except Exception as e:
        print(f"Startup test FAILED: {e}")

# Run with: asyncio.run(test_startup())

# ============================================================================

# STEP 6: Graceful shutdown handling

# ============================================================================

"""
The StartupManager handles graceful shutdown:

When the application terminates:

1. Background task executor is stopped gracefully
2. Statistics are logged (tasks processed, success rate, etc.)
3. Database connection is closed
4. All resources are cleaned up

Shutdown log output looks like:
[STOP] Shutting down Glad Labs AI Co-Founder application...
Stopping background task executor...
Task executor stopped
Tasks processed: 1234, Success: 1200, Failed: 34
Closing database connection...
Database connection closed
Application shut down successfully!
"""

# ============================================================================

# STEP 7: Debugging startup issues

# ============================================================================

"""
If startup fails, the logs will show:

For PostgreSQL connection failure:
ERROR: FATAL: PostgreSQL connection failed: ...
ERROR: 🛑 PostgreSQL is REQUIRED - cannot continue
ERROR: Set DATABASE_URL or DATABASE_USER environment variables
ERROR: Example DATABASE_URL: postgresql://user:...

For other service failures:
WARNING: Service X initialization failed: ... (continuing anyway)

Check:

1. Environment variables are set correctly
2. PostgreSQL server is running
3. Database user has proper permissions
4. Network connectivity to all services

The health check endpoint (/health) shows which services failed:
curl http://localhost:8000/health
{
"status": "degraded",
"services": {
"database": true,
"orchestrator": false, # This service failed
...
},
"startup_error": "Orchestrator initialization failed: ..."
}
"""

# ============================================================================

# STEP 8: Advanced configuration (optional)

# ============================================================================

"""
For more control over startup, you can customize StartupManager:
"""

class CustomStartupManager(StartupManager):
"""Extended startup manager with custom behavior"""

    async def initialize_all_services(self):
        """Override to customize initialization order"""
        # Call parent class initialization
        return await super().initialize_all_services()

    async def _initialize_database(self):
        """Override to customize database setup"""
        # Custom database initialization logic
        await super()._initialize_database()

        # Custom post-initialization
        logger.info("Running custom database setup...")
        # ... your custom code

"""
Then use it in main.py:

@asynccontextmanager
async def lifespan(app: FastAPI):
startup_manager = CustomStartupManager() # Use custom manager
services = await startup_manager.initialize_all_services() # ... rest of startup
yield
await startup_manager.shutdown()
"""

# ============================================================================

# STEP 9: Migration checklist

# ============================================================================

"""
Use this checklist to ensure complete migration:

[ ] Copy StartupManager to utils/startup_manager.py
[ ] Update main.py with lifespan context manager
[ ] Remove old startup event handlers
[ ] Remove old shutdown event handlers
[ ] Update all routes to use request.state or Depends()
[ ] Remove global service variables
[ ] Set required environment variables
[ ] Test startup with /health endpoint
[ ] Test graceful shutdown (Ctrl+C)
[ ] Verify all services appear in logs
[ ] Check task executor starts and processes tasks
[ ] Test database connectivity
[ ] Verify orchestrator initializes
[ ] Check workflow history service
[ ] Monitor startup error field
[ ] Test with Redis cache (if using)
[ ] Document any custom initialization steps
"""

# ============================================================================

# STEP 10: Rollback plan

# ============================================================================

"""
If you need to rollback to the previous setup:

1. Keep a backup of the old main.py startup code
2. Save the old service initialization code
3. If problems arise, restore from backup
4. Verify all services still work

However, the StartupManager pattern is more maintainable and should
be worth the migration effort.
"""

# ============================================================================

# SUMMARY

# ============================================================================

"""
Benefits of using StartupManager:

✅ Centralized initialization logic
✅ Consistent error handling
✅ Clear service dependencies
✅ Graceful shutdown with cleanup
✅ Health check integration
✅ Testable startup sequence
✅ Better logging and debugging
✅ Easier to maintain and extend
✅ Proper async/await patterns
✅ Works with all FastAPI versions

Migration effort: 1-2 hours for typical application
Payoff: Significantly improved code maintainability and reliability
"""
