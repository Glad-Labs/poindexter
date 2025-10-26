# 🗑️ Firestore & Pub/Sub Removal Plan

**Date:** October 25, 2025  
**Status:** Planning Phase  
**Scope:** Remove Google Cloud bloat, migrate to PostgreSQL + API-based commands

---

## 🎯 Current Stack Issues

### What You're Currently Paying For (Google Cloud)

- **Firestore:** $0.06 per 100K read/write operations (dev unused, prod scalable)
- **Pub/Sub:** $0.40 per GB of published data (event-based)
- **Storage:** $0.020 per GB (image storage, Pexels already hosted)
- **Total:** ~$20-50/month even if barely used

### What Railway Gives You (Free Tier)

- **PostgreSQL:** Fully managed, 1GB storage, included FREE
- **Compute:** Shared, 500mb RAM, FREE first project
- **Real-time:** No native Pub/Sub, but not needed (API calls are real-time)

### Cost Comparison

| Component | Google Cloud               | Railway + Vercel     | Savings            |
| --------- | -------------------------- | -------------------- | ------------------ |
| Database  | Firestore ($0.06/100K ops) | PostgreSQL (FREE)    | 100%               |
| Messaging | Pub/Sub ($0.40/GB)         | API calls (included) | 100%               |
| Storage   | Cloud Storage ($0.020/GB)  | Vercel CDN (FREE)    | 100%               |
| **Total** | **~$30/mo minimum**        | **FREE tier**        | **100% reduction** |

---

## 🏗️ New Architecture

### Before (Google Cloud Mess)

```
FastAPI → Firestore (document DB)
       → Pub/Sub (async messaging)
       ↓
Cloud Functions (intervene-trigger)
       ↓
Agents listening to Pub/Sub topics
```

### After (Clean PostgreSQL + API)

```
FastAPI → PostgreSQL (relational DB)
       → API endpoints for task dispatch
       ↓
Agents call API endpoints directly
       ↓
No Cloud Functions needed (use API routes)
```

---

## 📊 Data Structure Transformation

### Collections → PostgreSQL Tables

| Firestore Collection | PostgreSQL Table    | Purpose                                             |
| -------------------- | ------------------- | --------------------------------------------------- |
| `tasks`              | `tasks`             | Content creation & operational tasks                |
| `logs`               | `logs`              | Structured logging (info, warning, error, critical) |
| `financials`         | `financial_entries` | Expense tracking & burn rate                        |
| `agents`             | `agent_status`      | Agent health & heartbeat                            |
| `health`             | `health_checks`     | Service health history                              |

### Schema Design

```python
# Tasks (replaces Firestore tasks collection)
class Task(Base):
    __tablename__ = "tasks"
    id: UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_name: str
    agent_id: str
    status: str = "pending"  # pending, running, completed, failed
    topic: str
    primary_keyword: str
    target_audience: str
    category: str
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
    updated_at: datetime = Column(DateTime, onupdate=datetime.utcnow)
    metadata: dict = Column(JSON)  # flexible schema

# Logs (replaces Firestore logs collection)
class Log(Base):
    __tablename__ = "logs"
    id: UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    level: str  # info, warning, error, critical
    message: str
    timestamp: datetime = Column(DateTime, default=datetime.utcnow)
    metadata: dict = Column(JSON)

# Financial Entries (replaces Firestore financials)
class FinancialEntry(Base):
    __tablename__ = "financial_entries"
    id: UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    amount: float
    category: str  # model_usage, storage, compute, etc.
    timestamp: datetime = Column(DateTime, default=datetime.utcnow)
    metadata: dict = Column(JSON)

# Agent Status (replaces Firestore agents)
class AgentStatus(Base):
    __tablename__ = "agent_status"
    agent_name: str = Column(String, primary_key=True)
    status: str  # online, offline, busy, error
    last_heartbeat: datetime = Column(DateTime, default=datetime.utcnow)
    updated_at: datetime = Column(DateTime, onupdate=datetime.utcnow)
    metadata: dict = Column(JSON)
```

---

## 🔄 Command System Transformation

### Before: Pub/Sub Event-Driven

```python
# Pub/Sub topic: "agent-commands"
publish_agent_command("content", {
    "action": "process_all_pending",
    "priority": "high"
})

# Somewhere else, agent listening to topic processes message
def on_message_received(message):
    handle_command(message)
```

### After: REST API Request-Response

```python
# FastAPI endpoint
POST /api/agents/commands
{
    "agent_id": "content",
    "action": "process_all_pending",
    "priority": "high"
}

# Agent makes HTTP call to fetch tasks
GET /api/tasks/pending?agent_id=content&limit=10

# Agent completes task
PUT /api/tasks/{task_id}
{
    "status": "completed",
    "result": {...}
}
```

**Advantages:**

- ✅ No listeners needed (simpler, fewer moving parts)
- ✅ Synchronous request/response (easier debugging)
- ✅ No message queue infrastructure
- ✅ Real-time with HTTP polling or WebSocket
- ✅ Railway compatible (no special services)

---

## 📝 Implementation Roadmap

### Phase 1: Database Layer (TODAY)

**Create:**

1. `src/cofounder_agent/models.py` - SQLAlchemy ORM models
2. `src/cofounder_agent/services/database_service.py` - PostgreSQL client
3. Database migrations using Alembic (optional, can do manual creation)

**Remove:**

1. Delete `firestore_client.py` (replaced by database_service)
2. Delete `pubsub_client.py` (replaced by API endpoints)
3. Remove Google Cloud imports from `main.py`

### Phase 2: API Layer (NEXT)

**Create:**

1. `src/cofounder_agent/routes/tasks_router.py` - Task CRUD endpoints
2. `src/cofounder_agent/routes/logs_router.py` - Log endpoints
3. `src/cofounder_agent/routes/agents_router.py` - Agent status endpoints
4. `src/cofounder_agent/routes/commands_router.py` - Command dispatch

**Migrate:**

1. Update `orchestrator_logic.py` to call API endpoints instead of Pub/Sub
2. Update agents to call API endpoints instead of listening to Pub/Sub

### Phase 3: Cleanup (FINAL)

**Remove from requirements:**

- `google-cloud-firestore`
- `google-cloud-pubsub`
- `google-cloud-storage` (if not used for image hosting)
- `firebase-admin`

**Archive:**

- `cloud-functions/intervene-trigger/` (no longer needed, use API endpoint)

---

## 💰 Cost Savings Summary

### Current Monthly Spend (Google Cloud)

- Firestore: $5-10/month (at 100K-200K ops)
- Pub/Sub: $0.40-5/month (messaging)
- Storage: $0.50-2/month (images)
- **Total: $30-50/month minimum**

### New Monthly Spend (Railway + Vercel)

- PostgreSQL: $0 (free tier included)
- API calls: $0 (included in compute)
- Storage: $0 (Vercel CDN free)
- **Total: $0 (free tier) → $20/mo (if scales beyond free)**

### 12-Month Savings

**$360-600 per year** (before scaling)

---

## ⚡ Real-Time Capabilities

### Option 1: HTTP Polling (Simplest)

```python
# Agent polls for new tasks every 5 seconds
while True:
    tasks = await get_pending_tasks()
    for task in tasks:
        await process_task(task)
    await asyncio.sleep(5)
```

**Cost:** Minimal (few HTTP calls)  
**Latency:** ~5 seconds

### Option 2: Server-Sent Events (Real-time)

```python
# FastAPI endpoint streams tasks
@router.get("/tasks/stream")
async def stream_tasks():
    async def event_generator():
        while True:
            tasks = await get_pending_tasks()
            yield f"data: {json.dumps(tasks)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Cost:** WebSocket connection (PostgreSQL queries run on-demand)  
**Latency:** ~1 second

### Option 3: WebSocket (Full Real-Time)

```python
# FastAPI WebSocket endpoint
@router.websocket("/ws/tasks")
async def websocket_tasks(websocket: WebSocket):
    await websocket.accept()
    while True:
        tasks = await get_pending_tasks()
        await websocket.send_json(tasks)
        await asyncio.sleep(1)
```

**Cost:** Minimal (persistent connection, no messages)  
**Latency:** Immediate

---

## 🚀 Migration Checklist

- [ ] Phase 1: Database models created
- [ ] Phase 1: Database service replaces Firestore client
- [ ] Phase 2: API endpoints created (tasks, logs, agents, commands)
- [ ] Phase 2: Orchestrator updated to use API instead of Pub/Sub
- [ ] Phase 2: Agents updated to call API instead of listen to Pub/Sub
- [ ] Phase 3: Dependencies removed from requirements
- [ ] Phase 3: Tests updated (mock PostgreSQL instead of Firestore)
- [ ] Phase 3: Deploy to Railway (PostgreSQL included)
- [ ] Phase 3: Verify all functionality working
- [ ] Phase 3: Archive Google Cloud migration code

---

## 🎓 Key Decisions Made

1. **Why PostgreSQL?** Already in your stack, free on Railway, relational schema matches data
2. **Why API instead of Pub/Sub?** Simpler orchestration, no message queue infrastructure, easier debugging
3. **Why keep SQL models?** Type-safe, queryable, can use ORM features, auto-migration ready
4. **Why not use SQLite for dev?** PostgreSQL for both dev and prod = consistent behavior

---

## 📚 References

- SQLAlchemy Async: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Railway PostgreSQL: https://railway.app/docs/databases/postgresql
- FastAPI + Async SQL: https://fastapi.tiangolo.com/advanced/sql-databases-async/
- PostgreSQL JSON: https://www.postgresql.org/docs/current/datatype-json.html
