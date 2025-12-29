# 🚀 Firestore → PostgreSQL Quick Reference

**Date:** October 25, 2025  
**Status:** Phase 1 Complete ✅  
**Savings:** $30-50/month → free tier

---

## What Changed?

### Google Cloud → Railway + PostgreSQL

| Old                       | New                        | Benefit              |
| ------------------------- | -------------------------- | -------------------- |
| Firestore (document DB)   | PostgreSQL (relational DB) | Free tier included   |
| Pub/Sub (event messaging) | API endpoints (REST)       | Simpler, built-in    |
| Cloud Storage             | Vercel CDN                 | Already have it      |
| Total: $50/mo             | Total: $0/mo               | ✅ $600/year savings |

---

## Code Changes So Far

### ✅ Phase 1: Database Layer (DONE)

1. **ORM Models** (`models.py`)
   - Added: `Task`, `Log`, `FinancialEntry`, `AgentStatus`, `HealthCheck`
   - Type-safe, async-ready, with proper indexes

2. **Database Service** (`services/database_service.py`)
   - Replaces Firestore client
   - Same interface, async methods
   - Returns plain dicts (easy JSON)

3. **Dependencies** (`requirements.txt`)
   - Removed: `google-cloud-firestore`, `google-cloud-pubsub`, `firebase-admin`
   - Added: `asyncpg` (async PostgreSQL driver)
   - Keeps: `sqlalchemy`, `psycopg2-binary`

### ⏳ Phases 2-3: API & Cleanup (TODO)

- Create API endpoints (replace Pub/Sub topics)
- Update orchestrator to call API instead of publish
- Delete old `firestore_client.py` and `pubsub_client.py`
- Update tests

---

## Example: Adding a Task

### Before (Firestore)

```python
from services.firestore_client import FirestoreClient

firestore = FirestoreClient()
task_id = await firestore.add_task({
    "topic": "SEO Tips",
    "status": "queued"
})
```

### After (PostgreSQL)

```python
from services.database_service import DatabaseService

db = DatabaseService()
task_id = await db.add_task({
    "topic": "SEO Tips",
    "status": "queued"
})
```

✅ Same code, different backend!

---

## Pub/Sub Replacement

### Before (Topic-based)

```python
# Publish to topic
await pubsub_client.publish_agent_command("content", {
    "action": "create_content"
})

# Agent listening elsewhere
async def listen():
    await subscriber.receive_messages(callback)
```

### After (API Polling)

```python
# HTTP POST to queue task
POST /api/agents/commands
{
    "agent_id": "content",
    "action": "create_content"
}

# Agent polls for work
async def poll():
    tasks = await api_client.get("/tasks/pending")
    for task in tasks:
        await process(task)
```

✅ Simpler! No listeners needed.

---

## Database Connection

### Setup

```bash
# Railway gives you DATABASE_URL environment variable
export DATABASE_URL="postgresql+asyncpg://user:pw@host:5432/railway"
```

### Code

```python
from services.database_service import DatabaseService

db = DatabaseService()  # Reads DATABASE_URL automatically
await db.initialize()  # Creates tables if needed
```

### Local Development

```python
# Use SQLite for local testing
db = DatabaseService("sqlite+aiosqlite:///:memory:")
```

---

## Next Steps (Priority Order)

### 🔴 High Priority

1. Create API endpoints in `routes/tasks_router.py`
2. Update `orchestrator_logic.py` to use new API
3. Update `main.py` lifespan (initialize DB instead of Firestore)

### 🟡 Medium Priority

4. Delete `firestore_client.py`
5. Delete `pubsub_client.py`
6. Archive `cloud-functions/intervene-trigger/`

### 🟢 Low Priority

7. Update tests (mock PostgreSQL instead of Firestore)
8. Update imports everywhere
9. Deploy to Railway & verify

---

## Files to Review

### Created

- ✅ `src/cofounder_agent/services/database_service.py` - New DB service
- ✅ `docs/FIRESTORE_REMOVAL_PLAN.md` - Detailed plan
- ✅ `docs/POSTGRESQL_MIGRATION_STATUS.md` - Full migration guide

### Modified

- ✅ `src/cofounder_agent/models.py` - Added 5 new ORM models
- ✅ `src/cofounder_agent/requirements.txt` - Removed GCP, added asyncpg

### Still Need Updates

- ⏳ `src/cofounder_agent/main.py` (lifespan)
- ⏳ `src/cofounder_agent/orchestrator_logic.py` (replace Pub/Sub)
- ⏳ `src/cofounder_agent/services/firestore_client.py` (can delete)
- ⏳ `src/cofounder_agent/services/pubsub_client.py` (can delete)

---

## Why This Is Better

✅ **Cheaper:** $0 → $600/year savings  
✅ **Simpler:** REST API instead of event listeners  
✅ **Type-safe:** SQLAlchemy ORM with type hints  
✅ **Relational:** Can JOIN between tables  
✅ **Auditable:** SQL is transparent & queryable  
✅ **Integrated:** Strapi + Co-Founder both on PostgreSQL

---

## Questions?

**Q: Do I need to migrate existing Firestore data?**  
A: No, this is a new codebase. Just start fresh in PostgreSQL.

**Q: Can I use SQLite locally?**  
A: Yes! Use `sqlite+aiosqlite:///:memory:` for tests, PostgreSQL for prod.

**Q: How fast is API polling vs Pub/Sub?**  
A: API polling every 5 seconds = ~1 task/second latency. Good enough for most workflows.

**Q: What if I need real-time?**  
A: Use WebSockets or Server-Sent Events (SSE). FastAPI supports both.

---

## Cost Calculator

### Monthly Costs (Free Tier)

```
PostgreSQL:    $0  (1GB included)
API compute:   $0  (in Railway free tier)
LLM models:    $10 (OpenAI/Claude/Gemini)
─────────────────────
Total:         $10/month

(Compare to: $50/month on Google Cloud)
```

### Scaling Costs (if needed)

```
PostgreSQL overages: $19/month per additional GB
Railway compute:     $5-20/month for extra containers
─────────────────────
Scaled cost:         ~$30-50/month
(Still cheaper than Google Cloud!)
```

---

**Next:** Implement Phase 2 (API endpoints) → `docs/POSTGRESQL_MIGRATION_STATUS.md`
