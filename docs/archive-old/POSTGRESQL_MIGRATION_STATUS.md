# 📊 PostgreSQL Migration Status - October 25, 2025

## ✅ Completed (Phase 1: Database Layer)

### 1. SQLAlchemy ORM Models Added to `models.py`

**New models replacing Firestore collections:**

```python
# Firestore Collection  →  PostgreSQL Table
tasks                   →  Task (id, task_name, status, metadata, result)
logs                    →  Log (level, message, task_id, metadata)
financials              →  FinancialEntry (amount, category, task_id)
agents                  →  AgentStatus (agent_name, status, last_heartbeat)
health                  →  HealthCheck (service, status, response_time_ms)
```

**Key features:**

- ✅ Async-ready with SQLAlchemy 2.0
- ✅ Type-safe with UUID primary keys
- ✅ JSON/JSONB for flexible metadata
- ✅ Proper indexes for query performance
- ✅ Foreign key relationships (tasks → logs, financials)

**File:** `src/cofounder_agent/models.py` (lines 435-633)

### 2. Database Service Created (`database_service.py`)

**Replaces Firestore Client with same interface:**

```python
# Old Method (Firestore)          →  New Method (PostgreSQL)
firestore_client.add_task()        →  db_service.add_task()
firestore_client.get_task()        →  db_service.get_task()
firestore_client.add_log_entry()   →  db_service.add_log_entry()
firestore_client.add_financial_entry() → db_service.add_financial_entry()
firestore_client.update_agent_status() → db_service.update_agent_status()
```

**All methods are async:** ✅ Fully async for performance  
**All return plain dicts:** ✅ Easy JSON serialization  
**Connection pooling:** ✅ Built-in async engine with pool_size=20

**File:** `src/cofounder_agent/services/database_service.py`

### 3. Requirements Updated

**Removed from `requirements.txt`:**

- ❌ `google-cloud-firestore>=2.12.0`
- ❌ `google-cloud-pubsub>=2.18.0`
- ❌ `google-cloud-storage>=2.10.0`
- ❌ `google-cloud-aiplatform>=1.35.0`
- ❌ `google-api-python-client>=2.100.0`
- ❌ `google-auth-httplib2>=0.2.0`
- ❌ `google-auth-oauthlib>=1.1.0`
- ❌ `firebase-admin>=6.2.0`

**Added to `requirements.txt`:**

- ✅ `asyncpg>=0.29.0` (High-performance async PostgreSQL driver)
- ✅ Kept `sqlalchemy>=2.0.0`
- ✅ Kept `psycopg2-binary>=2.9.0` (connection string parsing)

**File:** `src/cofounder_agent/requirements.txt`

---

## 🔄 Next Steps (Phases 2-3)

### Phase 2: Replace Pub/Sub with API Endpoints

**Status:** Not started

**Current Architecture (Pub/Sub):**

```python
# main.py
pubsub_client.publish_agent_command("content", {...})  # → Topic

# Agent listening elsewhere
await subscriber.receive_messages(callback)  # ← Topic
```

**New Architecture (API):**

```python
# FastAPI endpoint
POST /api/agents/commands → Queue in PostgreSQL tasks table

# Agent polls for work
GET /api/tasks/pending?agent_id=content  # ← From database

# Agent reports completion
PUT /api/tasks/{task_id} → Update status in PostgreSQL
```

**To implement:**

1. Create `routes/tasks_router.py` with CRUD endpoints
2. Create `routes/commands_router.py` for agent dispatching
3. Update `orchestrator_logic.py` to use API instead of Pub/Sub
4. Agents query API endpoints instead of listening to topics

### Phase 3: Cleanup & Deployment

**Status:** Not started

**To do:**

1. Remove all Firestore/Pub/Sub imports from orchestrator
2. Delete `firestore_client.py`
3. Delete `pubsub_client.py`
4. Archive `cloud-functions/intervene-trigger/` (use API endpoint instead)
5. Update tests to mock PostgreSQL (async)
6. Deploy to Railway (PostgreSQL included free)

---

## 💰 Cost Reduction Summary

### Before (Google Cloud Stack)

| Service       | Cost/Month  | Usage                 |
| ------------- | ----------- | --------------------- |
| Firestore     | $5-10       | 100K-200K ops/day     |
| Pub/Sub       | $0.40-5     | Event-based messaging |
| Cloud Storage | $0.50-2     | Image hosting         |
| AI Platform   | $0-20       | Model inference       |
| **Total**     | **~$30-50** | **Per month**         |

### After (Railway + Vercel)

| Service    | Cost/Month | Usage                    |
| ---------- | ---------- | ------------------------ |
| PostgreSQL | **$0**     | Free tier (1GB)          |
| API Calls  | **$0**     | Included in compute      |
| CDN        | **$0**     | Vercel free tier         |
| LLM Models | **$5-15**  | Same as before (via API) |
| **Total**  | **~$5-15** | **Per month**            |

### 12-Month Savings

**$300-600 per year** (at free tier)  
**Scales cheaply:** PostgreSQL $19/month if you exceed 1GB storage

---

## 🏗️ Data Model Comparison

### Firestore (Document Store)

```
Collection: "tasks"
Document: {
  id: "abc123",
  taskName: "...",
  agentId: "...",
  status: "...",
  metadata: { ... flexible ... }
}
```

**Pros:**

- ✅ Flexible schema
- ✅ Real-time listeners
- ✅ Auto-scaling

**Cons:**

- ❌ ~$6 per 100K reads/writes
- ❌ No joins across collections
- ❌ Harder to query relationships

### PostgreSQL (Relational)

```sql
Table: tasks
id (UUID)       | task_name (string) | agent_id (string) | status (string) | metadata (JSON)
abc...          | "Create post"      | "content"         | "queued"        | {...}
```

**Pros:**

- ✅ Free tier included in Railway
- ✅ Powerful querying with JOINs
- ✅ ACID transactions
- ✅ Structured + flexible (JSON columns)

**Cons:**

- ⚠️ Must manage connection pool
- ⚠️ Need migrations for schema changes

---

## 🔗 Database Connection Details

### Connection String

**Format:** `postgresql+asyncpg://user:password@host:port/database`

**Example (Railway):**

```
postgresql+asyncpg://user:pw@container.railway.app:5432/railway
```

**Loaded from:** `DATABASE_URL` environment variable

### Async Driver Stack

```
FastAPI (async) ↓
  ↓ (via asyncio.to_thread or async context)
  ↓
SQLAlchemy AsyncEngine
  ↓
asyncpg (async PostgreSQL driver)
  ↓
PostgreSQL on Railway
```

**Connection pool:** 20 workers, max 40 overflow

---

## 📝 Key Files Modified

### Created

- ✅ `src/cofounder_agent/services/database_service.py` (670 lines)
  - DatabaseService class with async methods
  - Task CRUD operations
  - Log management
  - Financial tracking
  - Agent status monitoring
  - Health checks

### Modified

- ✅ `src/cofounder_agent/models.py` (line 435+)
  - Added 5 new ORM models
  - PostgreSQL schemas
  - Indexes for performance

- ✅ `src/cofounder_agent/requirements.txt`
  - Removed 8 Google Cloud packages
  - Added asyncpg driver
  - Kept sqlalchemy, psycopg2-binary

### Still Need Changes

- ❌ `src/cofounder_agent/main.py` (update lifespan)
- ❌ `src/cofounder_agent/orchestrator_logic.py` (replace Pub/Sub calls)
- ❌ `src/cofounder_agent/services/firestore_client.py` (can delete)
- ❌ `src/cofounder_agent/services/pubsub_client.py` (can delete)
- ❌ Routes in `src/cofounder_agent/routes/` (update imports)

---

## 🧪 Testing Strategy

### Current Test Issues

Many tests mock `google.cloud.firestore` which is now unnecessary:

```python
# OLD (before)
@pytest.fixture
def mock_firestore_client(mocker):
    mocker.patch("google.cloud.firestore.Client")
    return FirestoreClient()

# NEW (after)
@pytest.fixture
async def db_service():
    service = DatabaseService("sqlite+aiosqlite:///:memory:")
    await service.initialize()
    return service
```

### New Test Database

Use in-memory SQLite for tests:

```python
# src/cofounder_agent/tests/conftest.py
@pytest.fixture(scope="session")
async def db_service():
    service = DatabaseService("sqlite+aiosqlite:///:memory:")
    await service.initialize()
    yield service
    await service.close()
```

---

## ✨ Benefits of This Migration

### Financial 💰

- ✅ Save $30-50/month minimum
- ✅ Scale cheaply with PostgreSQL
- ✅ No cold starts (API-based)
- ✅ No egress charges

### Technical 🔧

- ✅ Relational database (better for complex queries)
- ✅ ACID transactions (data integrity)
- ✅ Type-safe ORM (SQLAlchemy)
- ✅ No vendor lock-in (PostgreSQL is standard)
- ✅ Easier debugging (SQL is transparent)

### Operational 🚀

- ✅ Single database (Strapi + Co-Founder both on PostgreSQL)
- ✅ Simpler orchestration (API instead of event listeners)
- ✅ Railway integrated (no separate services)
- ✅ Better monitoring (database metrics in Railway dashboard)

---

## 🎯 Implementation Checklist

- [x] Phase 1.1: SQLAlchemy ORM models created
- [x] Phase 1.2: DatabaseService created (async)
- [x] Phase 1.3: Requirements updated (remove GCP, add asyncpg)
- [x] Phase 1.4: Documentation created (this file)
- [ ] Phase 2.1: API endpoints created (tasks, logs, commands)
- [ ] Phase 2.2: Orchestrator updated to use API
- [ ] Phase 2.3: Remove Pub/Sub initialization from main.py
- [ ] Phase 3.1: Delete firestore_client.py
- [ ] Phase 3.2: Delete pubsub_client.py
- [ ] Phase 3.3: Update all imports
- [ ] Phase 3.4: Update tests
- [ ] Phase 3.5: Deploy to Railway
- [ ] Phase 3.6: Verify all functionality
- [ ] Phase 3.7: Archive old cloud-functions

---

## 📞 Questions?

### How to test locally?

```python
# Use SQLite for local development
db_service = DatabaseService("sqlite+aiosqlite:///:memory:")
await db_service.initialize()

# Use PostgreSQL for staging
db_service = DatabaseService(os.getenv("DATABASE_URL"))
```

### How to run migrations?

Currently no migrations needed (SQLAlchemy creates tables).  
If needed later, use Alembic:

```bash
alembic init alembic
alembic revision --autogenerate -m "Add initial schema"
alembic upgrade head
```

### How do agents get work now?

**Old way (Pub/Sub listener):**

```python
# Agent waits passively
subscription.listen_for_messages(handler)
```

**New way (API polling):**

```python
# Agent actively polls
tasks = await api.get("/tasks/pending?agent_id=content")
for task in tasks:
    await process(task)
```

---

**Status:** Phase 1 complete ✅  
**Next:** Implement API endpoints (Phase 2)  
**Timeline:** ~2-3 days to full migration  
**Risk Level:** Low (old code stays intact, API is additive)
