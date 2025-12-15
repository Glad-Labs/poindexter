"""
StartupManager - Complete Reference Documentation

Comprehensive guide to using the StartupManager utility for startup/shutdown orchestration.
"""

# ============================================================================

# TABLE OF CONTENTS

# ============================================================================

"""

1. Overview
2. Architecture
3. Initialization Sequence
4. Service Dependencies
5. Error Handling Strategy
6. Configuration
7. Integration Patterns
8. Testing
9. Troubleshooting
10. API Reference
    """

# ============================================================================

# 1. OVERVIEW

# ============================================================================

"""
StartupManager is a centralized utility for orchestrating application startup
and shutdown in the Glad Labs AI Co-Founder system.

Key responsibilities:

- Initialize all services in correct order
- Handle errors with appropriate fallback behavior
- Manage service dependencies
- Provide graceful shutdown
- Log startup state for debugging

Location: src/cofounder_agent/utils/startup_manager.py
Used in: src/cofounder_agent/main.py (FastAPI lifespan)
"""

# ============================================================================

# 2. ARCHITECTURE

# ============================================================================

"""
STARTUP FLOW:

    FastAPI Startup Event
            |
            v
    StartupManager.initialize_all_services()
            |
            +---> _initialize_database (PostgreSQL) [MANDATORY]
            |
            +---> _run_migrations
            |
            +---> _setup_redis_cache [OPTIONAL]
            |
            +---> _initialize_model_consolidation [OPTIONAL]
            |
            +---> _initialize_orchestrator [OPTIONAL]
            |
            +---> _initialize_workflow_history [OPTIONAL]
            |
            +---> _initialize_intelligent_orchestrator [OPTIONAL]
            |
            +---> _initialize_content_critique [OPTIONAL]
            |
            +---> _initialize_task_executor [BACKGROUND]
            |
            +---> _verify_connections [HEALTH CHECK]
            |
            +---> _register_route_services [CONFIG]
            |
            v
    Return services dict

    Services available to routes via app.state

SHUTDOWN FLOW:

    FastAPI Shutdown Event
            |
            v
    StartupManager.shutdown()
            |
            +---> Stop task executor gracefully
            |     - Wait for in-flight tasks
            |     - Log final statistics
            |
            +---> Close database connection
            |     - Drain connection pool
            |     - Close asyncpg pool
            |
            v
    All resources cleaned up

DEPENDENCY GRAPH:

    database -----> migrations, orchestrator, workflow_history, task_executor

    orchestrator --> task_executor

    intelligent_orchestrator -----> task_executor

    redis_cache -----> (enhances query performance)

    model_consolidation --> (optional AI backends)

    content_critique --> (enhances task_executor)

"""

# ============================================================================

# 3. INITIALIZATION SEQUENCE

# ============================================================================

"""
The 11-step initialization sequence ensures proper startup:

Step 1: Initialize Database (PostgreSQL with asyncpg)

- Establishes connection pool
- Mandatory - fails startup if not available
- Creates asyncpg.Pool for high-performance queries
- Status: "PostgreSQL connected - ready for operations"

Step 2: Run Migrations

- Applies schema changes if needed
- Configures content task store
- Status: "Database migrations completed successfully"
- Failure mode: Warning, continues anyway

Step 3: Setup Redis Cache

- Initializes query caching layer
- Improves repeated query performance
- Status: "Redis cache initialized" or "not available"
- Failure mode: Warning, continues without caching

Step 4: Initialize Model Consolidation

- Registers AI model backends (Ollama -> HF -> Google -> Anthropic -> OpenAI)
- Provides unified model interface
- Status: "Model consolidation service initialized"
- Failure mode: Warning, models not available

Step 5: Initialize Orchestrator

- Core workflow orchestration engine
- Uses database and HTTP API
- Status: "Orchestrator initialized successfully"
- Failure mode: Stored in startup_error, continues

Step 6: Initialize Workflow History Service

- Persists execution history to PostgreSQL
- Enables audit trails and analytics
- Status: "Workflow history service initialized"
- Failure mode: Service disabled, continues

Step 7: Initialize Intelligent Orchestrator

- Advanced orchestrator with memory system
- Builds on base orchestrator
- Status: "Intelligent orchestrator initialized successfully"
- Failure mode: Falls back to base orchestrator

Step 8: Initialize Content Critique Loop

- Quality assurance for generated content
- Evaluates and improves outputs
- Status: "Content critique loop initialized"
- Failure mode: Critique disabled, continues

Step 9: Initialize Background Task Executor

- Processes tasks asynchronously
- Orchestrates content generation pipeline
- Status: "Background task executor started successfully"
- Failure mode: Tasks not processed, continues

Step 10: Verify Connections

- Health checks on all services
- Logs verification results
- Status: "Database health check passed"
- Failure mode: Logged as warning

Step 11: Register Route Services

- Injects database service into route handlers
- Enables routes to access database
- Status: "Database service registered with routes"
- Failure mode: Routes may fail, continues
  """

# ============================================================================

# 4. SERVICE DEPENDENCIES

# ============================================================================

"""
Dependency Tree:

database_service (MANDATORY)
├── Required by: orchestrator, workflow_history, task_executor
├── Provides: PostgreSQL connection pool
└── Failure impact: Application cannot start

orchestrator (OPTIONAL)
├── Depends on: database_service
├── Provides: Workflow execution engine
└── Failure impact: Orchestration not available

intelligent_orchestrator (OPTIONAL)
├── Depends on: orchestrator, database_service
├── Provides: Advanced orchestration with memory
└── Failure impact: Advanced features not available

task_executor (OPTIONAL BUT RECOMMENDED)
├── Depends on: database_service, orchestrator or intelligent_orchestrator
├── Provides: Background task processing
└── Failure impact: Tasks not processed automatically

workflow_history_service (OPTIONAL)
├── Depends on: database_service
├── Provides: Execution history persistence
└── Failure impact: Execution history not available

redis_cache (OPTIONAL)
├── Depends on: none (standalone)
├── Provides: Query result caching
└── Failure impact: Queries slower but still work

model_consolidation (OPTIONAL)
├── Depends on: none (standalone)
├── Provides: Unified AI model interface
└── Failure impact: AI models not available

MANDATORY vs OPTIONAL:

MANDATORY (Startup fails without):

- PostgreSQL database

OPTIONAL (Startup continues without):

- Redis cache
- Model consolidation
- Orchestrator
- Workflow history
- Intelligent orchestrator
- Content critique
- Task executor
  """

# ============================================================================

# 5. ERROR HANDLING STRATEGY

# ============================================================================

"""
Different error handling for different service types:

DATABASE (MANDATORY):
If connection fails: 1. Log detailed error message 2. Print instructions to user 3. Raise SystemExit(1) to stop application

Example error:
FATAL: PostgreSQL connection failed: connection refused
🛑 PostgreSQL is REQUIRED - cannot continue
Set DATABASE_URL or DATABASE_USER environment variables

ORCHESTRATOR/INTELLIGENT ORCHESTRATOR (OPTIONAL):
If initialization fails: 1. Store error in startup_error 2. Log warning message 3. Continue with degraded functionality

Services still work but without orchestration

BACKGROUND SERVICES (OPTIONAL):
If initialization fails: 1. Log warning message 2. Set service to None 3. Continue with remaining services

Application works but with reduced features

HEALTH CHECK:
If connection verification fails: 1. Log warning 2. Continue startup 3. Services may fail at runtime if unhealthy

FALLBACK PATTERN:

# Check service availability

if app.state.startup_error: # Use degraded mode
return {"status": "degraded", "error": app.state.startup_error}
else: # Normal operation
return {"status": "healthy"}
"""

# ============================================================================

# 6. CONFIGURATION

# ============================================================================

"""
Environment Variables Required:

DATABASE CONFIGURATION:
Option A - Single connection string:
DATABASE_URL=postgresql://user:password@host:5432/dbname

Option B - Individual components:
DATABASE_USER=your_user
DATABASE_PASSWORD=your_password
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev

OPTIONAL CONFIGURATION:
ENVIRONMENT=production|development
Default: production
Controls logging verbosity

API_BASE_URL=http://localhost:8000
Default: http://localhost:8000
Used for internal API calls

REDIS_URL=redis://localhost:6379
Optional, used for query caching

OLLAMA_URL=http://localhost:11434
Optional, used for local LLM backend

EXAMPLE .env file:

# Required

DATABASE_USER=glad_user
DATABASE_PASSWORD=secure_password
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_dev

# Optional

ENVIRONMENT=development
API_BASE_URL=http://localhost:8000
REDIS_URL=redis://localhost:6379

LOADING CONFIGURATION:
Python reads environment variables automatically
Use python-dotenv for .env file support:
from dotenv import load_dotenv
load_dotenv()
"""

# ============================================================================

# 7. INTEGRATION PATTERNS

# ============================================================================

"""
PATTERN 1: Basic FastAPI Integration (Recommended)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from utils.startup_manager import StartupManager

@asynccontextmanager
async def lifespan(app: FastAPI): # Startup
startup_manager = StartupManager()
services = await startup_manager.initialize_all_services()
app.state.database = services['database']
app.state.orchestrator = services['orchestrator'] # ... other services

    yield  # App runs here

    # Shutdown
    await startup_manager.shutdown()

app = FastAPI(lifespan=lifespan)

PATTERN 2: Using Dependency Injection

from fastapi import Depends

def get_database(request: Request):
if not hasattr(request.app.state, 'database'):
raise RuntimeError("Database not initialized")
return request.app.state.database

@app.get("/tasks")
async def list_tasks(db = Depends(get_database)):
tasks = await db.pool.fetch("SELECT \* FROM tasks")
return tasks

PATTERN 3: Direct Request State Access

@app.get("/tasks")
async def list_tasks(request: Request):
db = request.state.database
tasks = await db.pool.fetch("SELECT \* FROM tasks")
return tasks

PATTERN 4: Testing

async def test_startup():
manager = StartupManager()
services = await manager.initialize_all_services()
try: # Use services for testing
assert services['database'] is not None
finally:
await manager.shutdown()

PATTERN 5: Custom Initialization

class CustomStartupManager(StartupManager):
async def \_initialize_orchestrator(self): # Custom orchestrator setup
await super().\_initialize_orchestrator() # Custom code here
"""

# ============================================================================

# 8. TESTING

# ============================================================================

"""
Running Tests:

# Run all tests

pytest tests/test_startup_manager.py -v

# Run only unit tests (no database required)

pytest tests/test_startup_manager.py -v -m "not integration"

# Run only integration tests (requires TEST_DATABASE_URL)

pytest tests/test_startup_manager.py -v -m "integration"

# Run specific test

pytest tests/test_startup_manager.py::TestStartupManager::test_initialization_creates_empty_state -v

# Run with coverage

pytest tests/test_startup_manager.py --cov=utils.startup_manager --cov-report=html

Test Categories:

Unit Tests (no external dependencies):

- test_initialization_creates_empty_state
- test_initialize_all_services_structure
- test_database_initialization_required
- test_shutdown_stops_task_executor
- test_shutdown_closes_database
- test_graceful_degradation_on_redis_failure
- test_state_preserved_after_initialization

Integration Tests (requires TEST_DATABASE_URL):

- test_full_startup_sequence
- test_database_health_check

Error Handling Tests:

- test_missing_database_url_causes_exit
- test_error_tracking_on_orchestrator_failure
- test_shutdown_handles_executor_stop_error

Setting up Test Database:

# Create test database

createdb glad_labs_test

# Set environment variable

export TEST_DATABASE_URL=postgresql://user:password@localhost:5432/glad_labs_test

# Run integration tests

pytest tests/test_startup_manager.py -v -m "integration"
"""

# ============================================================================

# 9. TROUBLESHOOTING

# ============================================================================

"""
PROBLEM: "PostgreSQL connection failed"

Possible causes:

1. DATABASE_URL not set
2. PostgreSQL server not running
3. Wrong host/port
4. Wrong username/password
5. Database doesn't exist

Solutions:

# Check PostgreSQL is running

psql -l

# Set DATABASE_URL correctly

export DATABASE_URL=postgresql://user:password@localhost:5432/dbname

# Create database if needed

createdb glad_labs_dev

# Test connection

python -c "import asyncpg; asyncio.run(asyncpg.connect(...))"

PROBLEM: "Service X initialization failed"

This is a warning, not a fatal error. The service is optional.

Solutions:

1. Check service-specific logs for details
2. Verify dependencies are installed
3. Check service configuration
4. Check that external services are running

Example:
"Redis cache error: connection refused"
-> Redis is not running, but that's optional
-> Application will continue without caching

PROBLEM: "Startup stuck / taking too long"

Possible causes:

1. Database migration running
2. Model consolidation downloading models
3. Task executor processing old tasks
4. Network connectivity issues

Solutions:

1. Check logs for progress messages
2. Increase timeout if needed
3. Check network connectivity
4. Ensure external services are running

PROBLEM: "Memory usage high during startup"

Possible causes:

1. Large number of tasks in queue
2. Models being downloaded
3. Database connection pool too large

Solutions:

1. Clear task queue before startup
2. Use smaller model or different backend
3. Reduce connection pool size in config

PROBLEM: "Application crashes on shutdown"

Possible causes:

1. Task executor timeout on shutdown
2. Database connection pool leak
3. Unflushed data

Solutions:

1. Increase shutdown timeout
2. Check for proper connection cleanup
3. Ensure all operations complete before shutdown

DEBUGGING:

Enable debug logging:
import logging
logging.basicConfig(level=logging.DEBUG)

Check health endpoint:
curl http://localhost:8000/health

This shows which services initialized successfully and which failed.

Check application logs:
tail -f application.log

Look for [STARTUP] and [STOP] markers to track startup/shutdown.
"""

# ============================================================================

# 10. API REFERENCE

# ============================================================================

"""
StartupManager Class

Methods:

async initialize_all_services() -> Dict[str, Any]
Initialize all services in proper sequence

    Returns:
      {
        'database': DatabaseService | None,
        'orchestrator': Orchestrator | None,
        'task_executor': TaskExecutor | None,
        'intelligent_orchestrator': IntelligentOrchestrator | None,
        'workflow_history': WorkflowHistoryService | None,
        'startup_error': str | None
      }

    Raises:
      SystemExit: If database connection fails
      Exception: If unexpected error occurs

async shutdown() -> None
Gracefully shutdown all services

    - Stops task executor
    - Closes database connections
    - Cleans up resources

    Raises:
      Exception: If shutdown fails (logs but doesn't raise)

Properties:

database_service: DatabaseService | None
PostgreSQL database connection service

orchestrator: Orchestrator | None
Main orchestrator for workflow execution

task_executor: TaskExecutor | None
Background task processing executor

intelligent_orchestrator: IntelligentOrchestrator | None
Advanced orchestrator with memory system

workflow_history_service: WorkflowHistoryService | None
Service for persisting execution history

startup_error: str | None
Error message if startup had issues (but continued)

Private Methods (for subclassing):

async \_initialize_database() -> None
async \_run_migrations() -> None
async \_setup_redis_cache() -> None
async \_initialize_model_consolidation() -> None
async \_initialize_orchestrator() -> None
async \_initialize_workflow_history() -> None
async \_initialize_intelligent_orchestrator() -> None
async \_initialize_content_critique() -> None
async \_initialize_task_executor() -> None
async \_verify_connections() -> None
async \_register_route_services() -> None
def \_log_startup_summary() -> None

Usage Example:

from utils.startup_manager import StartupManager

async def main():
manager = StartupManager()
services = await manager.initialize_all_services()

      try:
          # Use services
          db = services['database']
          orchestrator = services['orchestrator']

          # Do work
          tasks = await db.pool.fetch("SELECT * FROM tasks")

      finally:
          await manager.shutdown()

# Run with:

asyncio.run(main())
"""

# ============================================================================

# SUMMARY

# ============================================================================

"""
StartupManager provides centralized, orchestrated startup/shutdown for the
Glad Labs AI Co-Founder application.

Key benefits:
✅ Centralized initialization logic
✅ Consistent error handling
✅ Clear service dependencies
✅ Graceful shutdown
✅ Health checking
✅ Testable
✅ Extensible

Location: src/cofounder_agent/utils/startup_manager.py
Use in: FastAPI lifespan or startup/shutdown events
Test with: pytest tests/test_startup_manager.py

For questions or issues, check STARTUP_MIGRATION_GUIDE.md or troubleshooting above.
"""
