# 📋 Subtask API - Quick Reference Card

**Print this page or bookmark it!**

---

## 5 Endpoints at a Glance

| Endpoint | Purpose | Input | Output |
|----------|---------|-------|--------|
| **Research** | Gather facts | topic, keywords | research_data |
| **Creative** | Draft content | topic, research | title, content |
| **QA** | Review & improve | topic, content | refined_content, score |
| **Images** | Find visuals | topic, count | featured_image_url |
| **Format** | Prepare publishing | topic, content | formatted_content |

---

## API Syntax

All endpoints follow this pattern:

```bash
POST /api/content/subtasks/{stage}
Authorization: Bearer {token}
Content-Type: application/json

{
  "topic": "Your topic here",
  "parent_task_id": "optional-parent-id",
  ... stage-specific fields ...
}

Response:
{
  "subtask_id": "uuid",
  "stage": "research|creative|qa|images|format",
  "status": "completed|failed",
  "result": { ... },
  "metadata": {
    "duration_ms": 2500,
    "tokens_used": 450,
    "model": "gpt-4"
  }
}
```

---

## Common Commands

### Start Backend
```bash
cd src/cofounder_agent && python main.py
```

### Run All Tests
```bash
cd src/cofounder_agent && pytest tests/test_subtask_endpoints.py -v
```

### Test Single Endpoint
```bash
curl -X POST http://localhost:8000/api/content/subtasks/research \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"topic":"AI trends","keywords":["ML"]}'
```

### Check Backend Health
```bash
curl http://localhost:8000/api/health
```

### View Recent Tasks
```bash
psql $DATABASE_URL -c "SELECT * FROM tasks WHERE task_type='subtask' LIMIT 5;"
```

---

## Response Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | Success | `{"status":"completed","result":...}` |
| 201 | Created | Same as 200 |
| 400 | Bad request | Missing required field |
| 401 | Unauthorized | Invalid/missing token |
| 422 | Validation error | Invalid topic format |
| 500 | Server error | Database connection failed |

---

## Field Requirements by Stage

### Research
```json
{
  "topic": "required: string",
  "keywords": "optional: string[]",
  "parent_task_id": "optional: uuid"
}
```

### Creative
```json
{
  "topic": "required: string",
  "research_output": "optional: string",
  "style": "optional: professional|conversational|technical",
  "tone": "optional: formal|casual",
  "target_length": "optional: 500-3000 words",
  "parent_task_id": "optional: uuid"
}
```

### QA
```json
{
  "topic": "required: string",
  "creative_output": "required: string",
  "research_output": "optional: string",
  "style": "optional: string",
  "tone": "optional: string",
  "max_iterations": "optional: 1-3",
  "parent_task_id": "optional: uuid"
}
```

### Images
```json
{
  "topic": "required: string",
  "content": "optional: string (for context)",
  "number_of_images": "optional: 1-5",
  "parent_task_id": "optional: uuid"
}
```

### Format
```json
{
  "topic": "required: string",
  "content": "required: string",
  "featured_image_url": "optional: url",
  "tags": "optional: string[]",
  "category": "optional: string",
  "parent_task_id": "optional: uuid"
}
```

---

## Use Cases

### Case 1: One Article, All Stages
```bash
# Research
research_id=$(curl -X POST .../research -d '{...}' | jq -r .subtask_id)

# Creative (with research)
creative_id=$(curl -X POST .../creative -d '{..., "parent_task_id":"'$research_id'"}' | jq -r .subtask_id)

# QA (with creative output)
qa_id=$(curl -X POST .../qa -d '{..., "parent_task_id":"'$creative_id'"}' | jq -r .subtask_id)

# Images
image_id=$(curl -X POST .../images -d '{..., "parent_task_id":"'$qa_id'"}' | jq -r .subtask_id)

# Format
curl -X POST .../format -d '{..., "parent_task_id":"'$image_id'"}'
```

### Case 2: Skip to QA (Use External Content)
```bash
curl -X POST http://localhost:8000/api/content/subtasks/qa \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "topic": "My Article",
    "creative_output": "Your existing content here..."
  }'
```

### Case 3: Research Only
```bash
curl -X POST http://localhost:8000/api/content/subtasks/research \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"topic": "Blockchain"}'
```

### Case 4: Images for Multiple Topics (Parallel)
```bash
# Terminal 1
curl -X POST .../images -d '{"topic":"AI"}' &

# Terminal 2
curl -X POST .../images -d '{"topic":"ML"}' &

# Terminal 3
curl -X POST .../images -d '{"topic":"LLMs"}' &

wait  # Wait for all to complete
```

---

## Database Queries

### View All Subtasks
```sql
SELECT id, task_name, status, created_at 
FROM tasks 
WHERE task_type = 'subtask' 
ORDER BY created_at DESC;
```

### View Task Chain (Parent → Children)
```sql
SELECT parent_task_id, COUNT(*) as subtask_count, MAX(created_at) as latest
FROM tasks 
WHERE task_type = 'subtask' AND parent_task_id IS NOT NULL
GROUP BY parent_task_id;
```

### View Subtask Results
```sql
SELECT id, task_name, status, result->>'content' as output
FROM tasks 
WHERE task_type = 'subtask' AND status = 'completed'
LIMIT 10;
```

### View Failed Subtasks
```sql
SELECT id, task_name, result->>'error' as error_msg, created_at
FROM tasks
WHERE task_type = 'subtask' AND status = 'failed'
ORDER BY created_at DESC;
```

---

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/dbname

# API Keys (pick at least one)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...

# Or use local Ollama
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434

# Server
COFOUNDER_AGENT_PORT=8000
DEBUG=False
LOG_LEVEL=INFO
```

---

## Monitoring

### Check Service Health
```bash
curl http://localhost:8000/api/health
```

### View Recent Errors
```bash
tail -50 src/cofounder_agent/logs/cofounder_agent.log | grep ERROR
```

### Monitor Task Performance
```bash
psql $DATABASE_URL -c "
  SELECT 
    task_type,
    status,
    COUNT(*) as count,
    AVG((metadata->>'duration_ms')::int) as avg_duration_ms
  FROM tasks
  WHERE created_at > NOW() - INTERVAL '1 hour'
  GROUP BY task_type, status;
"
```

### Check Model Usage
```bash
psql $DATABASE_URL -c "
  SELECT 
    metadata->>'model' as model,
    COUNT(*) as usage_count,
    SUM((metadata->>'tokens_used')::int) as total_tokens
  FROM tasks
  WHERE task_type = 'subtask' AND created_at > NOW() - INTERVAL '24 hours'
  GROUP BY model;
"
```

---

## Test Commands

### All Tests
```bash
cd src/cofounder_agent && pytest tests/test_subtask_endpoints.py -v
```

### Specific Test
```bash
pytest tests/test_subtask_endpoints.py::TestResearchSubtask::test_research_subtask_success -v
```

### With Coverage
```bash
pytest tests/test_subtask_endpoints.py -v --cov=routes.subtask_routes
```

### Verbose (Show print statements)
```bash
pytest tests/test_subtask_endpoints.py -v -s
```

### Stop on First Failure
```bash
pytest tests/test_subtask_endpoints.py -x
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| 401 Unauthorized | Check token in Authorization header |
| 422 Validation Error | Check required fields and types |
| 500 Server Error | Check backend logs: `tail -f logs/*.log` |
| Token Expired | Generate new token (see TESTING_GUIDE.md) |
| Database Connection Error | Verify DATABASE_URL and PostgreSQL running |
| Model Not Available | Check OPENAI_API_KEY or USE_OLLAMA=true |

---

## Important URLs

| Service | URL |
|---------|-----|
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| Database | localhost:5432 (PostgreSQL) |
| Ollama (if used) | http://localhost:11434 |

---

## Code Templates

### Python (requests)
```python
import requests

response = requests.post(
    "http://localhost:8000/api/content/subtasks/research",
    headers={"Authorization": f"Bearer {token}"},
    json={"topic": "AI Trends", "keywords": ["ML"]}
)
result = response.json()
print(result["result"]["research_data"])
```

### Python (httpx async)
```python
import httpx

async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://localhost:8000/api/content/subtasks/creative",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "topic": "AI Trends",
            "research_output": "...",
            "style": "professional"
        }
    )
    result = response.json()
```

### JavaScript (fetch)
```javascript
const response = await fetch(
  'http://localhost:8000/api/content/subtasks/qa',
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      topic: 'AI Trends',
      creative_output: '...',
      max_iterations: 2
    })
  }
);
const data = await response.json();
```

### Bash (curl)
```bash
curl -X POST http://localhost:8000/api/content/subtasks/format \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI Trends",
    "content": "...",
    "tags": ["ai", "tech"],
    "category": "technology"
  }'
```

---

## Performance Tips

- **Parallel execution:** Call multiple stages simultaneously for different articles
- **Caching:** Cache research results if using same topic multiple times
- **Batch operations:** Process 10+ articles in a single batch request
- **Model selection:** Use Ollama for free local execution during development
- **Monitoring:** Track token usage and duration to optimize costs

---

**Last Updated:** November 24, 2025  
**Version:** 1.0  
**Status:** ✅ Production Ready

---

**Keep this handy! Bookmark the testing and implementation guides for detailed information.**
