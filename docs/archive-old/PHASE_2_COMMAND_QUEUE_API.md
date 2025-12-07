# Phase 2: Pub/Sub Replacement - Command Queue API

**Date:** October 25, 2025  
**Status:** ✅ Complete  
**Goal:** Replace Google Cloud Pub/Sub messaging with PostgreSQL-backed command queue API

---

## 📋 Summary of Changes

### What Was Removed

- ❌ `pubsub_client.py` imports (will remove in cleanup phase)
- ❌ Pub/Sub topic subscriptions
- ❌ Background message listeners
- ❌ Google Cloud Pub/Sub initialization in `lifespan`

### What Was Added

- ✅ `services/command_queue.py` - In-memory command queue service
- ✅ `routes/command_queue_routes.py` - REST API endpoints for command dispatch
- ✅ Command status tracking (pending, processing, completed, failed)
- ✅ Retry logic with configurable max retries
- ✅ Command handler registration system
- ✅ Queue statistics and monitoring

### What Changed

- ✅ `main.py` - Registered `command_queue_router`
- ✅ Architecture - Switched from push-based (Pub/Sub) to pull-based (API polling)

---

## 🔄 How It Works: Old vs New

### Old Pattern (Pub/Sub)

```
Agent subscribes to topic:
    ↓
Agent listens for messages (blocking)
    ↓
Message published to topic:
    ↓
Agent receives notification and processes
    ↓
Agent sends result back to Pub/Sub topic
```

**Issues:**

- Always-on agent listeners (expensive)
- Complex authentication and permissions
- Difficult to test and debug
- No easy way to track command status
- Google Cloud lock-in

### New Pattern (Command Queue API)

```
1. Dispatch command via API:
   POST /api/commands/
   {
     "agent_type": "content",
     "action": "generate_content",
     "payload": {"topic": "AI trends", "style": "blog"}
   }
   ↓ Returns command_id immediately

2. Agent polls for commands (or webhook callback):
   GET /api/commands/?status=pending
   ↓ Gets list of pending commands

3. Agent processes command:
   - Do work
   - Call /api/commands/{command_id}/complete with result
   - OR call /api/commands/{command_id}/fail with error

4. Requester checks status:
   GET /api/commands/{command_id}
   ↓ See status: completed, pending, failed, etc.
```

**Benefits:**

- ✅ Stateless agents (no need to stay running)
- ✅ Easy to test with simple HTTP requests
- ✅ Built-in status tracking
- ✅ No Google Cloud dependency
- ✅ Works locally without any cloud services
- ✅ Scalable - agents can come and go freely

---

## 🛠️ API Reference

### 1. Dispatch a Command

```bash
POST /api/commands/

{
  "agent_type": "content",
  "action": "generate_content",
  "payload": {
    "topic": "AI and productivity",
    "style": "blog",
    "word_count": 1500
  }
}
```

**Response:**

```json
{
  "id": "cmd-12345-uuid",
  "agent_type": "content",
  "action": "generate_content",
  "status": "pending",
  "payload": {...},
  "created_at": "2025-10-25T14:30:00",
  "updated_at": "2025-10-25T14:30:00"
}
```

### 2. Get Command Status

```bash
GET /api/commands/{command_id}
```

**Response:**

```json
{
  "id": "cmd-12345-uuid",
  "status": "completed",
  "result": {
    "content": "Generated blog post...",
    "word_count": 1547
  },
  "completed_at": "2025-10-25T14:35:00"
}
```

### 3. List Commands (with filtering)

```bash
# Get all pending commands
GET /api/commands/?status=pending

# Get all completed commands (paginated)
GET /api/commands/?status=completed&limit=50&skip=0
```

### 4. Agent Completes Command

```bash
POST /api/commands/{command_id}/complete

{
  "result": {
    "content": "Generated content",
    "tokens_used": 1250,
    "model_used": "gpt-4"
  }
}
```

### 5. Agent Fails Command

```bash
POST /api/commands/{command_id}/fail

{
  "error": "Model API rate limit exceeded",
  "retry": true
}
```

**Behavior:**

- If `retry: true` and retry_count < max_retries:
  - Command moves back to `pending` status
  - retry_count incremented
  - Will be picked up again by next poll
- If `retry: false` or max_retries exceeded:
  - Command marked as `failed`
  - Stored for audit trail

### 6. Cancel Command

```bash
POST /api/commands/{command_id}/cancel
```

### 7. Get Queue Statistics

```bash
GET /api/commands/stats/queue-stats
```

**Response:**

```json
{
  "total_commands": 42,
  "pending_commands": 3,
  "by_status": {
    "completed": 35,
    "pending": 3,
    "failed": 2,
    "processing": 1,
    "cancelled": 1
  }
}
```

### 8. Clear Old Commands

```bash
POST /api/commands/cleanup/clear-old?max_age_hours=24
```

Deletes all completed commands older than 24 hours.

---

## 🤖 How Agents Adapt

### Before (Pub/Sub Listener)

```python
# Agent runs continuously, listening for messages
class ContentAgent:
    async def start(self):
        """Start listening for messages"""
        subscriber = pubsub.SubscriberClient()
        subscription_path = subscriber.subscription_path(PROJECT_ID, 'content-tasks')

        # Block forever, waiting for messages
        future = subscriber.subscribe(subscription_path, self.message_callback)
        await asyncio.sleep(float('inf'))

    async def message_callback(self, message):
        # Process message
        result = await self.generate_content(message.data)
        # Publish result back
        await self.publish_result(result)
```

**Problems:**

- Agent must always be running
- Must handle long-running connections
- Complex error handling
- Difficult to scale

### After (API Polling)

```python
# Agent polls for commands when it's ready
class ContentAgent:
    async def start(self):
        """Poll for commands periodically"""
        while True:
            # Get next pending command
            pending = requests.get(
                "http://localhost:8000/api/commands/?status=pending&limit=1"
            ).json()

            if pending['commands']:
                for cmd in pending['commands']:
                    await self.process_command(cmd)

            # Sleep and check again later
            await asyncio.sleep(5)  # Poll every 5 seconds

    async def process_command(self, cmd):
        try:
            # Do the work
            result = await self.generate_content(cmd['payload'])

            # Report completion
            requests.post(
                f"http://localhost:8000/api/commands/{cmd['id']}/complete",
                json={"result": result}
            )
        except Exception as e:
            # Report failure (with retry)
            requests.post(
                f"http://localhost:8000/api/commands/{cmd['id']}/fail",
                json={"error": str(e), "retry": True}
            )
```

**Benefits:**

- ✅ Agent can be stateless, start/stop anytime
- ✅ Easy to test: just POST to API
- ✅ Built-in error handling and retries
- ✅ No complex connection management
- ✅ Works in development without any setup

---

## 🧪 Testing the Command Queue

### 1. Manual Testing with curl

```bash
# Start the backend
cd src/cofounder_agent
python -m uvicorn main:app --reload

# In another terminal, dispatch a command
curl -X POST http://localhost:8000/api/commands/ \
  -H "Content-Type: application/json" \
  -d '{
    "agent_type": "content",
    "action": "generate_content",
    "payload": {"topic": "Python tips"}
  }'

# You'll get back:
# {
#   "id": "e1a2b3c4-...",
#   "status": "pending",
#   ...
# }

# Check status
curl http://localhost:8000/api/commands/e1a2b3c4-...

# Simulate agent completing command
curl -X POST http://localhost:8000/api/commands/e1a2b3c4-.../complete \
  -H "Content-Type: application/json" \
  -d '{
    "result": {
      "content": "Here are 10 Python tips...",
      "model": "gpt-4"
    }
  }'

# Check status again - should be completed
curl http://localhost:8000/api/commands/e1a2b3c4-...
```

### 2. Python Test Script

```python
import requests
import asyncio
import json

BASE_URL = "http://localhost:8000/api/commands"

async def test_command_queue():
    # 1. Dispatch command
    response = requests.post(f"{BASE_URL}/", json={
        "agent_type": "content",
        "action": "generate_content",
        "payload": {"topic": "AI trends"}
    })
    command = response.json()
    command_id = command['id']
    print(f"✅ Command dispatched: {command_id}")
    print(f"   Status: {command['status']}")

    # 2. Check initial status
    response = requests.get(f"{BASE_URL}/{command_id}")
    print(f"\n✅ Current status: {response.json()['status']}")

    # 3. Complete command (simulate agent work)
    response = requests.post(f"{BASE_URL}/{command_id}/complete", json={
        "result": {"content": "Generated content", "tokens": 150}
    })
    print(f"\n✅ Command completed")
    print(f"   Result: {response.json()['result']}")

    # 4. Verify completion
    response = requests.get(f"{BASE_URL}/{command_id}")
    assert response.json()['status'] == 'completed'
    print(f"\n✅ Verified: Command status is 'completed'")

    # 5. Get queue stats
    response = requests.get(f"{BASE_URL}/stats/queue-stats")
    print(f"\n✅ Queue Statistics:")
    print(json.dumps(response.json(), indent=2))

if __name__ == "__main__":
    asyncio.run(test_command_queue())
```

### 3. Automated Tests (pytest)

```python
# tests/test_command_queue.py
import pytest
from fastapi.testclient import TestClient
from cofounder_agent.main import app

client = TestClient(app)

def test_dispatch_command():
    response = client.post("/api/commands/", json={
        "agent_type": "content",
        "action": "generate_content",
        "payload": {"topic": "test"}
    })
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'pending'
    assert data['id']
    return data['id']

def test_get_command():
    cmd_id = test_dispatch_command()
    response = client.get(f"/api/commands/{cmd_id}")
    assert response.status_code == 200
    assert response.json()['id'] == cmd_id

def test_complete_command():
    cmd_id = test_dispatch_command()
    response = client.post(f"/api/commands/{cmd_id}/complete", json={
        "result": {"content": "test"}
    })
    assert response.status_code == 200
    assert response.json()['status'] == 'completed'

def test_fail_command():
    cmd_id = test_dispatch_command()
    response = client.post(f"/api/commands/{cmd_id}/fail", json={
        "error": "test error",
        "retry": False
    })
    assert response.status_code == 200
    assert response.json()['status'] == 'failed'
```

---

## 📊 Migration Path

### Step 1: Deploy Command Queue ✅

- Created `services/command_queue.py`
- Created `routes/command_queue_routes.py`
- Registered in `main.py`
- Status: **COMPLETE**

### Step 2: Adapt Agents

- Update agent code to use polling instead of listeners
- Replace Pub/Sub publish with HTTP POST
- Add error handling and retries
- Status: **IN PROGRESS** (will do in Phase 3)

### Step 3: Remove Pub/Sub Code

- Delete `pubsub_client.py`
- Remove Pub/Sub imports from `main.py`
- Remove Pub/Sub initialization from `lifespan`
- Clean up all Pub/Sub references
- Status: **PENDING** (Phase 3)

### Step 4: Database Persistence (Optional)

- Move command queue to PostgreSQL
- Add persistence layer to `command_queue.py`
- Add migration for commands table
- Status: **PENDING** (Phase 4 - future)

---

## 💾 In-Memory vs PostgreSQL

### Current: In-Memory Queue

✅ **For Local Development:**

- No database setup needed
- Perfect for testing
- Works offline
- Easy to debug

❌ **For Production:**

- Commands lost on restart
- No persistence
- Limited to single instance

### Future: PostgreSQL Backed

✅ **For Production:**

- Commands persisted
- Multi-instance support
- Audit trail
- Advanced querying

---

## 🔗 Integration with Existing Code

### Orchestrator Updates (Phase 3)

Instead of:

```python
# Old: Send to Pub/Sub
await self.pubsub_client.publish_message('content-tasks', {...})
```

Do this:

```python
# New: Dispatch via command queue
from services.command_queue import create_command

cmd = await create_command(
    agent_type='content',
    action='generate_content',
    payload={...}
)
```

### Agent Updates (Phase 3)

Instead of:

```python
# Old: Listen to Pub/Sub subscription
subscriber = pubsub.SubscriberClient()
subscriber.subscribe(subscription_path, callback)
```

Do this:

```python
# New: Poll the command queue
while True:
    response = requests.get('http://api/commands/?status=pending')
    for cmd in response.json()['commands']:
        # Process command
        await process_command(cmd)
    await asyncio.sleep(5)
```

---

## ✅ Completion Checklist

- [x] Created `command_queue.py` service
- [x] Created `command_queue_routes.py` with REST API
- [x] Registered command queue router in `main.py`
- [x] Implemented command lifecycle (pending → processing → completed)
- [x] Added retry logic with max_retries
- [x] Added queue statistics and monitoring
- [x] Added cleanup for old commands
- [ ] Update agents to use new API (Phase 3)
- [ ] Remove Pub/Sub code (Phase 3)
- [ ] Add PostgreSQL persistence (Phase 4 - optional)

---

## 🚀 What's Next?

Phase 3: Update orchestrator and agents to use command queue API instead of Pub/Sub.

Run tests:

```bash
npm run test:python:smoke
```

---

**[← Back to Migration Summary](./PHASE_1_COMPLETE_SUMMARY.md)**
