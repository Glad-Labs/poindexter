# NLP Agent Workflow - Quick Reference Card

## 🚀 Quick Start Commands

```bash
# Start all services
npm run dev

# Start just backend
npm run dev:cofounder

# Start just Oversight Hub
npm run dev:oversight

# Test health
curl http://localhost:8000/health

# Run full test suite
bash test-nlp-workflow.sh

# Run detailed chat tests
bash test-chat-detailed.sh
```

---

## 📡 API Endpoints Quick Reference

### 1. Health & Status

```bash
# Quick health (instant)
GET /health
Response: {"status": "ok"}

# Detailed health (with components)
GET /api/health
Response: {"status": "healthy", "components": {"database": "ok", "orchestrator": "ok"}}

# Model health (shows available providers)
GET /api/models/health
Response: {"providers": {"ollama": "available", "openai": "requires_key", ...}}
```

### 2. Chat & Conversation

```bash
# Single message chat
POST /api/chat
Body: {
  "message": "Ask something here",
  "model": "ollama-llama2",      # or: openai, claude, gemini
  "conversationId": "conv-123",   # ID to track multi-turn
  "temperature": 0.7,             # 0=deterministic, 2=creative
  "max_tokens": 500               # Max output length
}
Response: {
  "response": "The answer is...",
  "model": "ollama-llama2",
  "provider": "ollama",
  "conversationId": "conv-123",
  "timestamp": "2026-02-13T...",
  "tokens_used": 45
}

# Available Models
- ollama-llama2      (free, ~10s)
- ollama-mistral     (free, ~8s)
- ollama-neural-chat (free, ~12s)
- openai-gpt-3.5     (requires OPENAI_API_KEY)
- openai-gpt-4       (requires OPENAI_API_KEY)
- claude-opus        (requires ANTHROPIC_API_KEY)
- claude-sonnet      (requires ANTHROPIC_API_KEY)
- gemini             (requires GOOGLE_API_KEY)
```

### 3. NLP Task Composition

```bash
# Compose task from natural language (no execution)
POST /api/tasks/capability/compose-from-natural-language
Headers: Authorization: Bearer YOUR_TOKEN
Body: {
  "request": "Write a blog post about AI safety",
  "auto_execute": false,
  "save_task": true
}
Response: {
  "success": true,
  "task_definition": {
    "id": "task-uuid",
    "type": "content_generation",
    "title": "Write a blog post...",
    "parameters": {
      "topic": "AI safety",
      "content_type": "blog_post",
      "tone": "informative"
    },
    "estimated_duration": 120
  }
}

# Compose AND execute immediately
POST /api/tasks/capability/compose-and-execute
Headers: Authorization: Bearer YOUR_TOKEN
Body: {
  "request": "Write a blog post about AI safety",
  "auto_execute": true,
  "save_task": true
}
Response: {
  "success": true,
  "execution_id": "exec-uuid",
  "task_id": "task-uuid",
  "status": "in_progress",
  "estimated_completion": "120 seconds"
}

# Poll for execution results
GET /api/tasks/{execution_id}/status
Headers: Authorization: Bearer YOUR_TOKEN
Response: {
  "status": "completed",
  "result": {
    "content": "...",
    "quality_score": 0.92
  }
}
```

### 4. Task Management

```bash
# Get all tasks
GET /api/tasks?limit=10&offset=0
Response: {
  "tasks": [{
    "id": "task-uuid",
    "title": "...",
    "status": "completed",
    "created_at": "2026-02-13T..."
  }]
}

# Create task
POST /api/tasks
Headers: Authorization: Bearer YOUR_TOKEN
Body: {
  "type": "blog_post",
  "title": "My Blog Post",
  "parameters": {...}
}

# Execute task
POST /api/tasks/{task_id}/execute
Headers: Authorization: Bearer YOUR_TOKEN

# Get task status
GET /api/tasks/{task_id}
Response: {
  "id": "task-id",
  "status": "in_progress",
  "progress": 45,
  "result": null  // Set when complete
}
```

### 5. Metrics & Analytics

```bash
# Task metrics
GET /api/metrics
Response: {
  "total_tasks": 1542,
  "completed": 1438,
  "failed": 24,
  "pending": 80,
  "success_rate": 98.4,
  "average_time": 4521
}

# Cost tracking
GET /api/analytics/costs
Response: {
  "total": "$324.56",
  "api_calls": "$156.23",
  "image_generation": "$68.33"
}
```

### 6. Authentication

```bash
# Get JWT token
POST /api/auth/token
Body: {
  "username": "user@example.com",
  "password": "password"
}
Response: {
  "access_token": "eyJ0eXAi...",
  "token_type": "bearer"
}

# Refresh token
POST /api/auth/refresh
Headers: Authorization: Bearer YOUR_TOKEN

# OAuth (GitHub example)
GET /api/auth/github
# Browser redirects to: https://github.com/login/oauth/authorize?client_id=...
# After auth, redirects back with code
# System exchanges code for access_token

# Logout
POST /api/auth/logout
Headers: Authorization: Bearer YOUR_TOKEN
```

---

## 🎯 Common Test Scenarios

### Scenario 1: Test Local Ollama

```bash
# Prerequisites
ollama serve &  # or use Ollama app
ollama pull llama2

# Test chat
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is machine learning?",
    "model": "ollama-llama2",
    "conversationId": "test-1"
  }' | jq '.response'

# Expected: Response in ~10 seconds, ~0.5-1k tokens
```

### Scenario 2: Test with Claude (if API key set)

```bash
# Prerequisites
export ANTHROPIC_API_KEY=sk-ant-...

# Test chat
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is machine learning?",
    "model": "claude",
    "conversationId": "test-1"
  }' | jq '.response'

# Expected: Response in ~2-3 seconds, higher quality
```

### Scenario 3: Multi-turn Conversation

```bash
CONV_ID="conversation-$(date +%s)"

# Turn 1
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Hello, what is your name?\",
    \"model\": \"ollama-llama2\",
    \"conversationId\": \"$CONV_ID\"
  }" | jq '.response'

# Turn 2 (agent should remember context)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Tell me more about yourself\",
    \"model\": \"ollama-llama2\",
    \"conversationId\": \"$CONV_ID\"
  }" | jq '.response'
```

### Scenario 4: Natural Language Task

```bash
TOKEN="your-jwt-token-here"

# Get token first (or use existing)
# export TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token \
#   -H "Content-Type: application/json" \
#   -d '{"username":"user@example.com","password":"pass"}' | jq -r '.access_token')

# Compose task from natural language
curl -X POST http://localhost:8000/api/tasks/capability/compose-from-natural-language \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "request": "Write a LinkedIn post about digital transformation",
    "auto_execute": false,
    "save_task": true
  }' | jq '.task_definition'

# Expected: Task with extracted parameters (topic, content_type, tone, etc.)
```

### Scenario 5: Full Pipeline (Compose + Execute)

```bash
TOKEN="your-jwt-token-here"

# Compose and execute
curl -X POST http://localhost:8000/api/tasks/capability/compose-and-execute \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "request": "Write a Twitter thread about AI trends",
    "auto_execute": true,
    "save_task": true
  }' | jq '.'

# Follow up: Poll for results
EXEC_ID=$(curl -s ... | jq -r '.execution_id')
curl http://localhost:8000/api/tasks/$EXEC_ID/status \
  -H "Authorization: Bearer $TOKEN" | jq '.status'

# Keep polling until status is "completed" or "failed"
```

---

## 🧩 Model Provider Fallback Chain

```
Your Request
    ↓
1. OLLAMA (free, local) → available?
    ├─ YES → Use Ollama model
    └─ NO → Continue
    ↓
2. ANTHROPIC (Claude) → API key available?
    ├─ YES → Use Claude
    └─ NO → Continue
    ↓
3. OPENAI (GPT) → API key available?
    ├─ YES → Use GPT
    └─ NO → Continue
    ↓
4. GOOGLE (Gemini) → API key available?
    ├─ YES → Use Gemini
    └─ NO → Continue
    ↓
5. ECHO → Mock/fallback response
    └─ Returns: "I don't have a real AI response..."
```

---

## 🔧 Environment Variables Checklist

```bash
# Required
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs

# At least ONE of these
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
OLLAMA_BASE_URL=http://localhost:11434

# Optional but recommended
LOG_LEVEL=debug              # For troubleshooting
SQL_DEBUG=false              # See SQL queries
SENTRY_DSN=...               # Error tracking
LLM_PROVIDER=claude          # Force specific provider
DEFAULT_MODEL_TEMPERATURE=0.7
```

---

## ✅ Pre-Testing Checklist

- [ ] Backend running: `npm run dev:cofounder`
- [ ] Health check passes: `curl http://localhost:8000/health`
- [ ] Database connected: Check `/api/health` components
- [ ] At least one LLM available:
  - [ ] Ollama running on 11434, OR
  - [ ] OPENAI_API_KEY set, OR
  - [ ] ANTHROPIC_API_KEY set, OR
  - [ ] GOOGLE_API_KEY set
- [ ] Frontend running: `npm run dev:oversight` (port 3001)
- [ ] JWT token available (for /api/tasks/* endpoints)

---

## 🐛 Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| Chat returns 500 | Check backend logs, verify Ollama or API key |
| "model not found" | Run `ollama pull llama2` or check API keys |
| Composition fails | Ensure DB connected, check `/api/health` |
| CORS error | Set REACT_APP_API_URL=<http://localhost:8000> |
| WebSocket fails | Use correct endpoint (no /api prefix) |
| Token expired | Set valid JWT or refresh token |
| Slow responses | Check if Ollama is memory-constrained |
| UI not updating | Check browser Network tab, backend logs |

---

## 📞 Getting Help

1. **Backend Issues:**

   ```bash
   # Check logs
   npm run dev:cofounder  # Watch this terminal
   # Search for [Chat], [Orchestrator] entries
   ```

2. **Frontend Issues:**

   ```bash
   # Check browser console
   # Network tab → inspect failed requests
   # Check REACT_APP_API_URL in .env
   ```

3. **Model Issues:**

   ```bash
   # Check Ollama
   ollama list
   ollama serve  # Should be running
   
   # Check API keys
   echo $OPENAI_API_KEY
   echo $ANTHROPIC_API_KEY
   ```

4. **Database Issues:**

   ```bash
   # Test connection
   psql "$DATABASE_URL" -c "SELECT 1"
   
   # Check tables exist
   psql "$DATABASE_URL" -c "\dt"
   ```

---

## 📊 Performance Expectations

**Local Ollama (Free):**

- Response time: 8-15 seconds
- Quality: Medium
- Cost: $0
- Best for: Testing, development

**Claude 3.5 Sonnet ($0.003 input / $0.015 output):**

- Response time: 2-4 seconds  
- Quality: Excellent
- Cost: Low
- Best for: Production, complex tasks

**GPT-4 ($0.03 input / $0.06 output):**

- Response time: 1-3 seconds
- Quality: Excellent
- Cost: Medium-High
- Best for: Critical tasks, complex reasoning

**Gemini ($0.0005 input / $0.0015 output):**

- Response time: 2-4 seconds
- Quality: Good
- Cost: Very Low
- Best for: Cost-sensitive, bulk operations
