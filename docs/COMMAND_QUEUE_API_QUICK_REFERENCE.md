# Command Queue API - Quick Reference

**Status:** ✅ Live and Ready  
**Base URL:** `http://localhost:8000/api/commands`  
**Date:** October 25, 2025

---

## 🚀 One-Minute Quick Start

### Dispatch a Command

```bash
curl -X POST http://localhost:8000/api/commands/ \
  -H "Content-Type: application/json" \
  -d '{
    "agent_type": "content",
    "action": "generate_content",
    "payload": {"topic": "AI trends"}
  }'
```

**Response:**

```json
{
  "id": "cmd-550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "agent_type": "content",
  "action": "generate_content"
}
```

### Check Status

```bash
curl http://localhost:8000/api/commands/cmd-550e8400-e29b-41d4-a716-446655440000
```

### Complete the Work

```bash
curl -X POST http://localhost:8000/api/commands/cmd-550e8400-e29b-41d4-a716-446655440000/complete \
  -H "Content-Type: application/json" \
  -d '{
    "result": {
      "content": "Generated blog post about AI trends...",
      "tokens": 1500,
      "model": "gpt-4"
    }
  }'
```

---

## 📚 Full API Reference

### 1️⃣ Dispatch Command

```
POST /api/commands/
```

**Request:**

```json
{
  "agent_type": "content|financial|compliance|market",
  "action": "string",
  "payload": {}
}
```

**Response:**

```json
{
  "id": "uuid",
  "status": "pending",
  "created_at": "iso-timestamp"
}
```

---

### 2️⃣ Get Command Status

```
GET /api/commands/{command_id}
```

**Response Examples:**

Pending:

```json
{
  "id": "cmd-...",
  "status": "pending",
  "created_at": "2025-10-25T14:30:00"
}
```

Completed:

```json
{
  "id": "cmd-...",
  "status": "completed",
  "result": {
    "content": "Generated content...",
    "tokens": 1500
  },
  "completed_at": "2025-10-25T14:35:00"
}
```

Failed:

```json
{
  "id": "cmd-...",
  "status": "failed",
  "error": "API rate limit exceeded",
  "retry_count": 1,
  "max_retries": 3
}
```

---

### 3️⃣ List Commands

```
GET /api/commands/?status=pending&limit=50&skip=0
```

**Query Parameters:**

- `status` - Filter by: `pending`, `processing`, `completed`, `failed`, `cancelled`
- `limit` - Results per page (default: 100, max: 1000)
- `skip` - Pagination offset (default: 0)

**Response:**

```json
{
  "commands": [
    {
      "id": "cmd-...",
      "status": "pending",
      ...
    }
  ],
  "total": 42,
  "status_filter": "pending"
}
```

---

### 4️⃣ Mark as Completed

```
POST /api/commands/{command_id}/complete
```

**Request:**

```json
{
  "result": {
    "content": "Generated content",
    "tokens": 1500,
    "model": "gpt-4"
  }
}
```

**Response:** Updated command with `status: "completed"` and result included

---

### 5️⃣ Mark as Failed

```
POST /api/commands/{command_id}/fail
```

**Request:**

```json
{
  "error": "Model API rate limit exceeded",
  "retry": true
}
```

**Behavior:**

- If `retry: true` and `retry_count < max_retries` → moves back to `pending`
- Otherwise → moves to `failed`

---

### 6️⃣ Cancel Command

```
POST /api/commands/{command_id}/cancel
```

**Response:** Updated command with `status: "cancelled"`

**Note:** Can only cancel `pending` or `processing` commands

---

### 7️⃣ Get Queue Statistics

```
GET /api/commands/stats/queue-stats
```

**Response:**

```json
{
  "total_commands": 1547,
  "pending_commands": 3,
  "by_status": {
    "completed": 1500,
    "pending": 3,
    "processing": 1,
    "failed": 25,
    "cancelled": 18
  },
  "timestamp": "2025-10-25T14:40:00"
}
```

---

### 8️⃣ Clean Up Old Commands

```
POST /api/commands/cleanup/clear-old?max_age_hours=24
```

**Query Parameters:**

- `max_age_hours` - Delete completed commands older than this (default: 24, range: 1-720)

**Response:**

```json
{
  "message": "Old commands (>24h) cleared"
}
```

---

## 🔄 Command Status Flow

```
┌─────────┐
│ pending │  ← Command created via POST /api/commands/
└────┬────┘
     │
     ↓
┌────────────┐
│ processing │  ← Agent processing (optional status)
└────┬───────┘
     │
     ├─→ ┌───────────┐
     │   │ completed │  ← Agent calls /complete
     │   └───────────┘
     │
     ├─→ ┌────────────────────┐
     │   │ failed (with retry)│  ← Retry: true, retry_count < max
     │   │ → moves to pending │
     │   └────────────────────┘
     │
     └─→ ┌────────┐
         │ failed │  ← Retry: false or max_retries exceeded
         └────────┘
```

---

## 💾 Example: Content Generation Workflow

### Step 1: Request Generation

```bash
COMMAND_ID=$(curl -s -X POST http://localhost:8000/api/commands/ \
  -H "Content-Type: application/json" \
  -d '{
    "agent_type": "content",
    "action": "generate_content",
    "payload": {"topic": "Python tips", "style": "blog"}
  }' | jq -r '.id')

echo "Command ID: $COMMAND_ID"
```

### Step 2: Check Status

```bash
curl -s http://localhost:8000/api/commands/$COMMAND_ID | jq '.status'
# Output: "pending"
```

### Step 3: Simulate Agent Processing

```bash
curl -X POST http://localhost:8000/api/commands/$COMMAND_ID/complete \
  -H "Content-Type: application/json" \
  -d '{
    "result": {
      "content": "# 10 Python Tips for Better Code\n\n1. Use list comprehensions...",
      "tokens": 2100,
      "model": "gpt-4"
    }
  }'
```

### Step 4: Verify Completion

```bash
curl -s http://localhost:8000/api/commands/$COMMAND_ID | jq '.result'
# Output: { "content": "...", "tokens": 2100, ... }
```

---

## 🧪 Testing with Python

```python
import requests
import json

BASE_URL = "http://localhost:8000/api/commands"

def test_command_workflow():
    # 1. Dispatch command
    response = requests.post(f"{BASE_URL}/", json={
        "agent_type": "content",
        "action": "generate_content",
        "payload": {"topic": "AI"}
    })
    cmd = response.json()
    print(f"✅ Dispatched: {cmd['id']}")
    assert cmd['status'] == 'pending'

    # 2. Check status
    response = requests.get(f"{BASE_URL}/{cmd['id']}")
    assert response.status_code == 200
    print(f"✅ Status: {response.json()['status']}")

    # 3. Complete command
    response = requests.post(f"{BASE_URL}/{cmd['id']}/complete", json={
        "result": {"content": "Generated content", "tokens": 150}
    })
    assert response.json()['status'] == 'completed'
    print(f"✅ Completed with result: {response.json()['result']}")

    # 4. List all completed commands
    response = requests.get(f"{BASE_URL}/?status=completed&limit=10")
    commands = response.json()['commands']
    print(f"✅ Found {len(commands)} completed commands")

    # 5. Get stats
    response = requests.get(f"{BASE_URL}/stats/queue-stats")
    stats = response.json()
    print(f"✅ Queue stats: {stats['by_status']}")

if __name__ == "__main__":
    test_command_workflow()
```

---

## ⚙️ Error Handling

### Command Not Found

```json
{
  "status_code": 404,
  "detail": "Command not found: cmd-invalid-id"
}
```

### Invalid Status Filter

```json
{
  "status_code": 400,
  "detail": "Invalid status. Must be one of: pending, processing, completed, failed, cancelled"
}
```

### Cannot Cancel Completed Command

```json
{
  "status_code": 400,
  "detail": "Cannot cancel completed/failed command: cmd-..."
}
```

---

## 🔗 Agent Implementation Example

### Polling Pattern

```python
import requests
import asyncio
import time

class ContentAgent:
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
        self.queue_url = f"{base_url}/api/commands"

    async def start(self):
        """Start polling for commands"""
        print("📢 Content agent started, polling for commands...")

        while True:
            try:
                # Get next pending command
                response = requests.get(
                    f"{self.queue_url}/?status=pending&limit=1"
                )
                commands = response.json()['commands']

                if commands:
                    for cmd in commands:
                        await self.process_command(cmd)
                else:
                    print("⏳ No pending commands, checking again in 5s...")

            except Exception as e:
                print(f"❌ Error: {e}")

            # Poll every 5 seconds
            await asyncio.sleep(5)

    async def process_command(self, cmd):
        """Process a single command"""
        cmd_id = cmd['id']
        print(f"🔄 Processing command: {cmd_id}")

        try:
            # Do the work (generate content)
            result = await self.generate_content(cmd['payload'])

            # Report completion
            response = requests.post(
                f"{self.queue_url}/{cmd_id}/complete",
                json={"result": result}
            )
            print(f"✅ Command completed: {cmd_id}")

        except Exception as e:
            # Report failure
            error_msg = str(e)
            print(f"❌ Command failed: {cmd_id} - {error_msg}")

            requests.post(
                f"{self.queue_url}/{cmd_id}/fail",
                json={"error": error_msg, "retry": True}
            )

    async def generate_content(self, payload):
        """Generate content (mock implementation)"""
        topic = payload.get('topic', 'General')
        return {
            "content": f"Generated blog post about {topic}",
            "tokens": 1500,
            "model": "gpt-4"
        }

# Usage
if __name__ == "__main__":
    agent = ContentAgent()
    asyncio.run(agent.start())
```

---

## 📊 Performance Notes

- **Command Creation:** <1ms
- **Status Check:** <1ms
- **List Commands:** 5-50ms (depends on query size)
- **Completion:** <1ms
- **In-Memory Storage:** Suitable for development

---

## 🚀 Ready to Use!

The command queue API is live and ready for integration with agents.

**Start the backend:**

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

**Test the API:**

```bash
curl http://localhost:8000/api/commands/stats/queue-stats
```

---

**See Also:** `docs/PHASE_2_COMMAND_QUEUE_API.md` for detailed documentation
