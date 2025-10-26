# Phase 4 Action Plan: Initialize PostgreSQL in main.py

**Objective:** Update `src/cofounder_agent/main.py` to initialize PostgreSQL on application startup  
**Status:** Ready to Execute  
**Estimated Duration:** 15-20 minutes

---

## 📋 Checklist

- [ ] Read current main.py lifespan implementation
- [ ] Identify Firestore/Pub/Sub initialization code
- [ ] Replace with PostgreSQL initialization
- [ ] Update Orchestrator instantiation with new parameters
- [ ] Add database health check
- [ ] Verify zero Pylance errors
- [ ] Test application startup

---

## 🎯 What to Change

### Current State (Before)

```python
# main.py - OLD
@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    global firestore_client, pubsub_client, orchestrator

    # Google Cloud initialization
    firestore_client = firestore.Client()
    pubsub_client = pubsub_v1.PublisherClient()

    # Create orchestrator with old clients
    orchestrator = Orchestrator(
        firestore_client=firestore_client,
        pubsub_client=pubsub_client
    )
```

### Desired State (After)

```python
# main.py - NEW
@app.lifespan("startup")
async def lifespan():
    """Initialize PostgreSQL on startup"""
    global database_service, orchestrator

    try:
        # Initialize PostgreSQL
        database_service = await DatabaseService.connect()

        # Create tables if they don't exist
        await database_service.create_tables()

        # Initialize orchestrator with new services
        orchestrator = Orchestrator(
            database_service=database_service,
            api_base_url=os.getenv("API_BASE_URL", "http://localhost:8000")
        )

        # Log startup success
        logger.info("✅ Application initialized successfully")
        logger.info(f"  - Database: {database_service is not None}")
        logger.info(f"  - API Base URL: {os.getenv('API_BASE_URL')}")

        yield  # Application runs here

    finally:
        # Cleanup on shutdown
        if database_service:
            await database_service.close()
            logger.info("✅ Database connection closed")
```

---

## 🔧 Specific Changes Required

### 1. Remove Old Imports

**Location:** Top of main.py

Remove:

```python
from google.cloud import firestore
from google.cloud import pubsub_v1
```

Keep:

```python
import asyncio
import logging
from fastapi import FastAPI
# ... other existing imports
```

### 2. Add New Imports

**Location:** Top of main.py

Add after existing imports:

```python
from src.cofounder_agent.services.database_service import DatabaseService
from contextlib import asynccontextmanager
```

### 3. Update Global Variables

**Location:** After imports, before app initialization

Replace:

```python
# OLD
firestore_client = None
pubsub_client = None
orchestrator = None
```

With:

```python
# NEW
database_service = None
orchestrator = None
```

### 4. Update Lifespan Handler

**Location:** Main startup/shutdown event handlers

Replace entire startup/shutdown blocks with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager - startup and shutdown"""

    # ===== STARTUP =====
    global database_service, orchestrator

    try:
        logger.info("🚀 Starting application...")

        # 1. Initialize database service
        logger.info("  Connecting to PostgreSQL...")
        database_service = await DatabaseService.connect()

        # 2. Create tables if they don't exist
        logger.info("  Creating database tables...")
        await database_service.create_tables()

        # 3. Run migrations (if needed)
        logger.info("  Running migrations...")
        # await database_service.run_migrations()

        # 4. Initialize orchestrator
        api_base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
        logger.info(f"  Initializing orchestrator (API: {api_base_url})...")
        orchestrator = Orchestrator(
            database_service=database_service,
            api_base_url=api_base_url
        )

        # 5. Verify connections
        logger.info("  Verifying connections...")
        health = await database_service.health_check()
        logger.info(f"  Database health: {health}")

        logger.info("✅ Application started successfully!")

    except Exception as e:
        logger.error(f"❌ Failed to start application: {e}")
        raise

    yield  # Application runs here

    # ===== SHUTDOWN =====
    try:
        logger.info("🛑 Shutting down application...")

        if database_service:
            logger.info("  Closing database connection...")
            await database_service.close()
            logger.info("✅ Database connection closed")

        logger.info("✅ Application shut down successfully!")

    except Exception as e:
        logger.error(f"❌ Error during shutdown: {e}")
        raise


# Update app initialization to use lifespan
app = FastAPI(
    title="GLAD Labs AI Co-Founder",
    version="1.0.0",
    lifespan=lifespan
)
```

### 5. Update Any Route Handlers That Reference Old Clients

**Location:** Search for `firestore_client` or `pubsub_client` usage

Change from:

```python
@app.get("/api/status")
async def status():
    """Old way - using firestore_client directly"""
    return {"firestore": firestore_client is not None}
```

Change to:

```python
@app.get("/api/status")
async def status():
    """New way - using orchestrator's services"""
    status = await orchestrator._get_system_status_async()
    return status
```

---

## ✅ Verification Steps

After making changes, verify:

1. **Type Checking**

   ```bash
   cd src/cofounder_agent
   python -m pylint main.py  # or use Pylance in VS Code
   # Expected: No errors
   ```

2. **Import Validation**

   ```bash
   python -c "from src.cofounder_agent.main import app; print('✅ Imports OK')"
   ```

3. **Startup Test**

   ```bash
   cd src/cofounder_agent
   python -m uvicorn main:app --reload
   # Expected output:
   # 🚀 Starting application...
   #   Connecting to PostgreSQL...
   #   Creating database tables...
   #   Initializing orchestrator...
   #   Verifying connections...
   # ✅ Application started successfully!
   # INFO:     Uvicorn running on http://127.0.0.1:8000
   ```

4. **Health Check**
   ```bash
   curl http://localhost:8000/api/health
   # Expected:
   # {
   #   "status": "healthy",
   #   "database": {"postgresql": true},
   #   "api": {"command_queue": true}
   # }
   ```

---

## 🎬 Implementation Steps (In Order)

1. **Open file:** `src/cofounder_agent/main.py`
2. **Find:** Imports section at top
   - Remove Google Cloud imports
   - Add DatabaseService and asynccontextmanager imports
3. **Find:** Global variable declarations
   - Replace firestore_client/pubsub_client with database_service
4. **Find:** Startup/shutdown event handlers
   - Replace with lifespan context manager
5. **Find:** Any routes using firestore_client/pubsub_client
   - Update to use orchestrator's services
6. **Find:** App initialization line
   - Add lifespan parameter
7. **Test:** Run `npm run dev:cofounder` and verify logs
8. **Verify:** Run `curl http://localhost:8000/api/health` and check response

---

## 📊 Expected Changes Summary

| Item               | Before                                  | After                                                |
| ------------------ | --------------------------------------- | ---------------------------------------------------- |
| Startup handler    | `@app.on_event("startup")`              | `@asynccontextmanager` in lifespan                   |
| Shutdown handler   | `@app.on_event("shutdown")`             | Cleanup in lifespan finally block                    |
| Database init      | `firestore_client = firestore.Client()` | `database_service = await DatabaseService.connect()` |
| Orchestrator init  | `Orchestrator(firestore_client=...)`    | `Orchestrator(database_service=...)`                 |
| Error handling     | None                                    | Try/except with logging                              |
| App initialization | `app = FastAPI()`                       | `app = FastAPI(lifespan=lifespan)`                   |

---

## 🚀 Success Criteria

✅ Application starts without errors  
✅ PostgreSQL connection established  
✅ Database tables created  
✅ Orchestrator initialized with database_service  
✅ Health check endpoint returns database: true  
✅ All Pylance errors resolved (target: 0)  
✅ No Firestore/Pub/Sub imports remain

---

## 💡 Tips

- **If PostgreSQL not running:** Start it with `docker run -d -p 5432:5432 postgres:15`
- **If imports fail:** Verify `src/cofounder_agent/services/database_service.py` exists
- **If startup hangs:** Check database connection string in `.env`
- **If tables not created:** Verify DatabaseService.create_tables() method exists
- **Debug mode:** Add `DEBUG=True` to `.env` for more logging

---

## ⏭️ After Phase 4

Once Phase 4 is complete, you'll be ready for:

**Phase 5: Final Cleanup**

- [ ] Remove remaining Firestore imports from agents/
- [ ] Update tests to mock PostgreSQL
- [ ] Delete old Google Cloud configuration
- [ ] Update deployment scripts
- [ ] Full integration testing

---

**Status:** Ready to begin  
**Next:** Execute the changes above and verify with the verification steps  
**Estimated Time:** 15-20 minutes

Good luck! 🚀
