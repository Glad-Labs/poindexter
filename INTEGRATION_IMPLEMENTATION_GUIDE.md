# 🔗 Integration Implementation Guide - Post-SQLite Removal

**Date:** November 8, 2025  
**Status:** Ready for Integration Phase  
**Database:** PostgreSQL Only (SQLite completely removed)

---

## 🎯 What We've Done

1. ✅ **Removed ALL SQLite** - PostgreSQL mandatory
2. ✅ **Fail-fast startup** - Clear errors if not configured
3. ✅ **Updated configuration** - .env, docker-compose.yml, requirements.txt
4. ✅ **Cleaned dependencies** - Removed aiosqlite

---

## 🚀 Next: The 5 Critical Integrations

Now that PostgreSQL is mandatory, we can implement proper data persistence:

### Integration 1: Chat History Persistence

**File:** `src/cofounder_agent/routes/chat_routes.py`

**What to do:**

- Replace in-memory `conversations` dict with database queries
- Save every chat message to `chat_messages` table
- Load conversation history on request

**Database Schema:**

```sql
CREATE TABLE chat_conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    title VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES chat_conversations(id),
    task_id UUID,
    role VARCHAR(50) NOT NULL,  -- 'user', 'assistant'
    content TEXT NOT NULL,
    model VARCHAR(100),
    tokens_used INT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

### Integration 2: Task-to-Chat Linking

**File:** `src/cofounder_agent/routes/chat_routes.py`

**What to do:**

- Add `task_id` to ChatRequest model
- Save task_id with chat message
- Return task_id in response

**Code Changes:**

```python
class ChatRequest(BaseModel):
    message: str
    model: str
    conversationId: str
    task_id: Optional[str] = None  # NEW
    command_type: Optional[str] = None  # NEW

@router.post("")
async def chat(request: ChatRequest) -> ChatResponse:
    # Create task if command_type provided
    if request.command_type and not request.task_id:
        task_id = await db_service.add_task({...})
        request.task_id = task_id

    # Save chat with task reference
    await save_message(request.task_id, ...)
```

---

### Integration 3: API Metrics Recording

**File:** `src/cofounder_agent/routes/chat_routes.py` & `metrics_routes.py`

**What to do:**

- Record metrics on every API call
- Store tokens, cost, response time
- Update metrics endpoint to query database

**Database Schema:**

```sql
CREATE TABLE api_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model VARCHAR(100) NOT NULL,
    provider VARCHAR(100) NOT NULL,
    task_id UUID REFERENCES tasks(id),
    tokens_used INT,
    cost_usd DECIMAL(10, 4),
    response_time_ms INT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**Implementation:**

```python
async def chat(request: ChatRequest):
    start = time.time()
    response = await model.query(...)
    duration_ms = int((time.time() - start) * 1000)

    # Record metric
    await db_service.record_metric({
        'model': request.model,
        'tokens': len(response.split()),
        'cost_usd': 0.0,  # Ollama = free
        'response_time_ms': duration_ms,
        'task_id': request.task_id
    })
```

---

### Integration 4: Result Storage

**File:** `src/cofounder_agent/routes/chat_routes.py`

**What to do:**

- Save response to `tasks` table result field
- Update task status to 'completed'
- Store execution metadata

**Database Changes:**

```sql
ALTER TABLE tasks ADD COLUMN (
    result TEXT,
    result_metadata JSONB,
    completed_at TIMESTAMP
);
```

**Implementation:**

```python
async def chat(request: ChatRequest):
    response = await model.query(...)

    if request.task_id:
        await db_service.update_task(request.task_id, {
            'status': 'completed',
            'result': response,
            'result_metadata': {
                'model': request.model,
                'tokens': len(response.split()),
                'completed_at': datetime.utcnow().isoformat()
            }
        })
```

---

### Integration 5: Frontend Result Saving

**File:** `web/oversight-hub/src/components/common/CommandPane.jsx`

**What to do:**

- Use correct `/api/chat` endpoint
- Send task_id with command
- Save results back to backend
- Handle errors gracefully

**Changes:**

```jsx
// OLD: Wrong endpoint
const response = await fetch('http://localhost:8000/command', {...})

// NEW: Correct endpoint
const response = await fetch('http://localhost:8000/api/chat', {
    method: 'POST',
    body: JSON.stringify({
        message: commandMessage.description,
        model: selectedModel,
        conversationId: selectedTask?.id,
        task_id: selectedTask?.id,  // NEW
        command_type: commandMessage.type  // NEW
    })
})

// NEW: Save result to backend
const saveResultToBackend = async (taskId, result) => {
    await fetch(`http://localhost:8000/api/tasks/${taskId}`, {
        method: 'PATCH',
        body: JSON.stringify({
            status: 'approved',
            result: result
        })
    })
}
```

---

## 📋 Implementation Checklist

### Phase 1: Database Schema (Today - 30 min)

- [ ] Create `chat_conversations` table
- [ ] Create `chat_messages` table
- [ ] Create `api_metrics` table
- [ ] Add `result` columns to `tasks` table
- [ ] Test schema with psql

### Phase 2: Backend Chat Routes (1-2 days)

- [ ] Update ChatRequest model (add task_id, command_type)
- [ ] Replace in-memory conversations dict
- [ ] Save chat messages to database
- [ ] Implement message retrieval
- [ ] Add task linking logic
- [ ] Test with curl

### Phase 3: Backend Metrics (1 day)

- [ ] Create metrics recording function
- [ ] Add metrics recording to chat route
- [ ] Update metrics endpoint to query database
- [ ] Test with queries

### Phase 4: Backend Result Storage (1 day)

- [ ] Add result columns to tasks table
- [ ] Update task after chat completion
- [ ] Store execution metadata
- [ ] Test with database queries

### Phase 5: Frontend Integration (1 day)

- [ ] Fix endpoint URL to `/api/chat`
- [ ] Add task_id to request
- [ ] Add error handling
- [ ] Implement result saving
- [ ] Test end-to-end

### Phase 6: Testing & Validation (1 day)

- [ ] Unit tests for each component
- [ ] Integration tests for full flow
- [ ] End-to-end tests
- [ ] Performance validation
- [ ] Error scenario testing

---

## 🧪 Testing the Current State

### Test 1: Backend Starts Only With PostgreSQL

```bash
cd src/cofounder_agent

# Test 1a: Without DATABASE_URL
unset DATABASE_URL
python main.py
# Expected: FATAL error, exits with code 1
```

```bash
# Test 1b: With valid DATABASE_URL
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
python main.py
# Expected: ✅ PostgreSQL connected
```

### Test 2: No SQLite References

```bash
# Should find no results
grep -r "sqlite" src/cofounder_agent
grep -r "aiosqlite" src/cofounder_agent
grep -r "\.db" src/cofounder_agent/main.py
```

### Test 3: Docker Compose

```bash
# Check PostgreSQL configuration
docker-compose config | grep -A 5 "DATABASE"
```

---

## 📖 Reference Documents

1. **COMPREHENSIVE_CODE_REVIEW.md** - Critical issues identified
2. **SQLITE_REMOVAL_COMPLETE.md** - What was removed
3. **SQLITE_REMOVAL_PHASE_COMPLETE.md** - Phase completion details
4. **This document** - Integration guide

---

## 🚀 Quick Start: Run the Backend

```powershell
# 1. Make sure PostgreSQL is running
# 2. Make sure .env has correct DATABASE_URL
cat src/cofounder_agent/.env | grep DATABASE_URL

# 3. Start the backend
cd src/cofounder_agent
python main.py

# Expected output:
# ✅ PostgreSQL connected - ready for operations
# 🚀 Starting Glad Labs AI Co-Founder application...
# INFO: Uvicorn running on http://127.0.0.1:8000
```

---

## 🎯 Success Criteria

When integration is complete:

1. ✅ Chat message received by API
2. ✅ Message saved to `chat_messages` table
3. ✅ Ollama called with message
4. ✅ Metrics recorded in `api_metrics` table
5. ✅ Response sent to frontend
6. ✅ Frontend displays result
7. ✅ User approves result
8. ✅ Approval saved to `tasks` table
9. ✅ Task marked as 'approved'
10. ✅ Conversation history persists across restarts

---

## 📊 Impact When Complete

| Metric           | Before             | After                      |
| ---------------- | ------------------ | -------------------------- |
| Data persistence | ❌ Lost on restart | ✅ Persisted to PostgreSQL |
| Chat history     | ❌ In-memory       | ✅ Database stored         |
| Metrics tracking | ❌ Hardcoded       | ✅ Real data               |
| Task tracking    | ❌ No linking      | ✅ Linked to chats         |
| Error recovery   | ❌ No audit trail  | ✅ Full audit log          |

---

## 🎉 Conclusion

All SQLite has been removed. PostgreSQL is mandatory. The backend is ready for integration work.

**Next Step:** Implement the 5 critical integrations (2-4 days)

---

**Status:** ✅ READY FOR INTEGRATION PHASE  
**Created:** November 8, 2025
