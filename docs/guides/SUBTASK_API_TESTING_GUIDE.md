# 🧪 Subtask API Testing & Implementation Guide

**Last Updated:** November 24, 2025  
**Status:** ✅ Complete & Ready to Test  
**Framework:** FastAPI + pytest  
**Coverage:** 5 core subtask endpoints with full examples

---

## 📋 Quick Navigation

- **[Subtask API Overview](#subtask-api-overview)** - What subtasks do
- **[Getting Started](#getting-started)** - Prerequisites and setup
- **[Running Tests](#running-tests)** - Test commands and examples
- **[API Endpoints](#api-endpoints)** - Complete endpoint reference
- **[Testing Patterns](#testing-patterns)** - Reusable test patterns
- **[Common Issues](#common-issues)** - Troubleshooting

---

## 🎯 Subtask API Overview

### What Are Subtasks?

The Subtask API breaks the **7-stage content creation pipeline** into **independent, callable endpoints**. This enables:

✅ **Flexible Pipeline Execution**

- Run individual stages without the full pipeline
- Reuse outputs from one stage as inputs to another
- Chain stages together in custom orders

✅ **Task-Specific Operations**

- "Just find images" without regenerating content
- "Polish this with QA" on external content
- "Re-generate with different style" for existing research

✅ **Dependency Chaining**

- Research output feeds into Creative stage
- Creative output feeds into QA stage
- Track parent/child task relationships in database

✅ **Independent Workflow Building**

- Custom agents can call subtask endpoints
- External systems can integrate any stage
- Parallel execution of independent stages

### 5 Core Subtask Endpoints

| Endpoint                              | Purpose                       | Input                              | Output                           |
| ------------------------------------- | ----------------------------- | ---------------------------------- | -------------------------------- |
| `POST /api/content/subtasks/research` | Gather background information | topic, keywords                    | research_data                    |
| `POST /api/content/subtasks/creative` | Generate draft content        | topic, [research_output], style    | draft_content                    |
| `POST /api/content/subtasks/qa`       | Review & improve content      | topic, creative_output, [research] | refined_content, feedback, score |
| `POST /api/content/subtasks/images`   | Find visual assets            | topic, [content]                   | featured_image_url               |
| `POST /api/content/subtasks/format`   | Format for publication        | topic, content, [image_url], tags  | formatted_content, excerpt       |

---

## 🚀 Getting Started

### Prerequisites

```bash
# 1. Backend running
npm run dev:cofounder
# OR manually
cd src/cofounder_agent && python main.py

# 2. Valid JWT token for testing
# See: docs/reference/TESTING.md for token generation
```

### Authentication

All subtask endpoints require JWT authentication:

```bash
# Generate test token (if auth is enabled)
export TOKEN="your-jwt-token-here"

# Use in requests
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/content/subtasks/research
```

### Quick Test

```bash
# Test if API is responding (no auth needed for health check)
curl http://localhost:8000/api/health

# Test subtask endpoint (will show auth error if not authenticated)
curl -X POST http://localhost:8000/api/content/subtasks/research \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-token" \
  -d '{"topic":"AI trends"}'
```

---

## 🧪 Running Tests

### Backend Subtask Tests

**Run all subtask tests:**

```bash
cd src/cofounder_agent

# All tests
python -m pytest tests/ -v -k "subtask"

# Specific test file
python -m pytest tests/test_subtask_endpoints.py -v

# Single test
python -m pytest tests/test_subtask_endpoints.py::test_research_subtask_success -v

# With coverage
python -m pytest tests/test_subtask_endpoints.py -v --cov=routes.subtask_routes
```

### CI/CD Test Commands

```bash
# Quick smoke tests (includes subtask tests)
npm run test:python:smoke

# Full test suite
npm run test:python

# Full with coverage
npm run test:python -- --cov=.
```

### Pytest Fixtures for Subtask Testing

The test suite includes helpful fixtures in `tests/conftest.py`:

```python
@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)

@pytest.fixture
def auth_headers():
    """Valid JWT auth headers"""
    return {"Authorization": "Bearer test-token"}

@pytest.fixture
def sample_research_request():
    """Sample research subtask request"""
    return {
        "topic": "AI trends in 2025",
        "keywords": ["machine learning", "innovation"],
        "parent_task_id": None
    }

@pytest.fixture
def sample_creative_request():
    """Sample creative subtask request"""
    return {
        "topic": "AI trends",
        "research_output": "Research findings...",
        "style": "professional",
        "tone": "informative",
        "target_length": 2000
    }
```

---

## 📡 API Endpoints

### 1. Research Subtask

**Request:**

```bash
curl -X POST http://localhost:8000/api/content/subtasks/research \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "topic": "AI trends in 2025",
    "keywords": ["machine learning", "innovation", "transformers"],
    "parent_task_id": null
  }'
```

**Request Body (Pydantic Model):**

```python
class ResearchSubtaskRequest(BaseModel):
    topic: str                          # Required: Topic to research
    keywords: List[str] = []            # Optional: Keywords to focus on
    parent_task_id: Optional[str] = None  # Optional: For task chaining
```

**Response:**

```json
{
  "subtask_id": "abc-123-def",
  "stage": "research",
  "parent_task_id": null,
  "status": "completed",
  "result": {
    "research_data": "Comprehensive research findings about AI trends...",
    "topic": "AI trends in 2025",
    "keywords": ["machine learning", "innovation", "transformers"]
  },
  "metadata": {
    "duration_ms": 15000,
    "tokens_used": 1250,
    "model": "gpt-4"
  }
}
```

**Use Cases:**

- Gather background information independently
- Update research for an existing task
- Parallel research for multiple topics

---

### 2. Creative Subtask

**Request:**

```bash
curl -X POST http://localhost:8000/api/content/subtasks/creative \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "topic": "AI trends in 2025",
    "research_output": "Research findings from previous step...",
    "style": "professional",
    "tone": "informative",
    "target_length": 2500,
    "parent_task_id": "research-task-id"
  }'
```

**Request Body:**

```python
class CreativeSubtaskRequest(BaseModel):
    topic: str                              # Required
    research_output: Optional[str] = None   # Optional: From research stage
    style: Optional[str] = "professional"   # professional, casual, academic
    tone: Optional[str] = "informative"     # informative, persuasive, entertaining
    target_length: Optional[int] = 2000     # Word count target
    parent_task_id: Optional[str] = None    # For chaining
```

**Response:**

```json
{
  "subtask_id": "def-456-ghi",
  "stage": "creative",
  "parent_task_id": "research-task-id",
  "status": "completed",
  "result": {
    "title": "The Future of AI: 2025 Trends",
    "content": "# The Future of AI\n\nAs we enter 2025...",
    "style": "professional",
    "tone": "informative"
  },
  "metadata": {
    "duration_ms": 25000,
    "tokens_used": 2500,
    "model": "gpt-4"
  }
}
```

**Use Cases:**

- Generate content without research
- Re-generate with different style/tone
- Iterate on existing research

---

### 3. QA Subtask

**Request:**

```bash
curl -X POST http://localhost:8000/api/content/subtasks/qa \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "topic": "AI trends in 2025",
    "creative_output": "Draft content from creative stage...",
    "research_output": "Research findings for context...",
    "style": "professional",
    "tone": "informative",
    "max_iterations": 2,
    "parent_task_id": "creative-task-id"
  }'
```

**Request Body:**

```python
class QASubtaskRequest(BaseModel):
    topic: str                              # Required
    creative_output: str                    # Required: Content to review
    research_output: Optional[str] = None   # Optional: Original research
    style: Optional[str] = "professional"
    tone: Optional[str] = "informative"
    max_iterations: int = 2                 # 1-5: Refinement iterations
    parent_task_id: Optional[str] = None
```

**Response:**

```json
{
  "subtask_id": "ghi-789-jkl",
  "stage": "qa",
  "parent_task_id": "creative-task-id",
  "status": "completed",
  "result": {
    "content": "Refined content from QA loop...",
    "feedback": ["Improved clarity in section 2", "Added more examples"],
    "quality_score": 8.7,
    "iterations": 2
  },
  "metadata": {
    "duration_ms": 12000,
    "tokens_used": 1800,
    "model": "gpt-4",
    "quality_score": 8.7
  }
}
```

**Use Cases:**

- Review existing content without full pipeline
- Run additional QA passes
- Get feedback on external content

---

### 4. Images Subtask

**Request:**

```bash
curl -X POST http://localhost:8000/api/content/subtasks/images \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "topic": "AI trends in 2025",
    "content": "Full article content for context...",
    "number_of_images": 3,
    "parent_task_id": "creative-task-id"
  }'
```

**Request Body:**

```python
class ImageSubtaskRequest(BaseModel):
    topic: str                              # Required
    content: Optional[str] = None           # Optional: Article context
    number_of_images: int = 1               # 1-5: How many images
    parent_task_id: Optional[str] = None
```

**Response:**

```json
{
  "subtask_id": "jkl-012-mno",
  "stage": "images",
  "parent_task_id": "creative-task-id",
  "status": "completed",
  "result": {
    "featured_image_url": "https://images.example.com/ai-trends.jpg",
    "topic": "AI trends in 2025",
    "number_requested": 3
  },
  "metadata": {
    "duration_ms": 8000,
    "tokens_used": 0,
    "model": "vision",
    "images_found": 1
  }
}
```

**Use Cases:**

- Find images for existing content
- Update images without regenerating content
- Search images independently

---

### 5. Format Subtask

**Request:**

```bash
curl -X POST http://localhost:8000/api/content/subtasks/format \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "topic": "AI trends in 2025",
    "content": "Article content to format...",
    "featured_image_url": "https://images.example.com/ai-trends.jpg",
    "tags": ["AI", "trends", "2025"],
    "category": "technology",
    "parent_task_id": "creative-task-id"
  }'
```

**Request Body:**

```python
class FormatSubtaskRequest(BaseModel):
    topic: str                              # Required
    content: str                            # Required: Content to format
    featured_image_url: Optional[str] = None  # Optional: Featured image
    tags: List[str] = []                    # Optional: Content tags
    category: Optional[str] = None          # Optional: Content category
    parent_task_id: Optional[str] = None
```

**Response:**

```json
{
  "subtask_id": "mno-345-pqr",
  "stage": "format",
  "parent_task_id": "creative-task-id",
  "status": "completed",
  "result": {
    "formatted_content": "# AI Trends in 2025\n\n... formatted content ...",
    "excerpt": "Short excerpt for preview...",
    "tags": ["AI", "trends", "2025"],
    "category": "technology"
  },
  "metadata": {
    "duration_ms": 3000,
    "tokens_used": 500,
    "model": "gpt-3.5"
  }
}
```

**Use Cases:**

- Format content for specific platforms
- Convert between formats
- Update metadata without regenerating

---

## 🔄 Testing Patterns

### Pattern 1: Simple Subtask Execution

**Python pytest:**

```python
import pytest
from fastapi.testclient import TestClient
from src.cofounder_agent.main import app

client = TestClient(app)

@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}

def test_research_subtask_success(auth_headers):
    """Test successful research subtask execution"""
    response = client.post(
        "/api/content/subtasks/research",
        headers=auth_headers,
        json={
            "topic": "AI trends",
            "keywords": ["machine learning", "innovation"]
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["stage"] == "research"
    assert data["status"] == "completed"
    assert "result" in data
    assert "metadata" in data
    assert data["result"]["research_data"] is not None
```

### Pattern 2: Task Dependency Chaining

**Test chaining research → creative → QA:**

```python
def test_subtask_chaining(auth_headers):
    """Test chaining subtasks together"""

    # 1. Run research
    research_response = client.post(
        "/api/content/subtasks/research",
        headers=auth_headers,
        json={
            "topic": "AI trends",
            "keywords": ["ML", "innovation"]
        }
    )
    assert research_response.status_code == 200
    research_data = research_response.json()
    research_id = research_data["subtask_id"]
    research_output = research_data["result"]["research_data"]

    # 2. Run creative with research output
    creative_response = client.post(
        "/api/content/subtasks/creative",
        headers=auth_headers,
        json={
            "topic": "AI trends",
            "research_output": research_output,
            "style": "professional",
            "target_length": 2000,
            "parent_task_id": research_id
        }
    )
    assert creative_response.status_code == 200
    creative_data = creative_response.json()
    creative_id = creative_data["subtask_id"]
    creative_output = creative_data["result"]["content"]

    # 3. Run QA with creative output
    qa_response = client.post(
        "/api/content/subtasks/qa",
        headers=auth_headers,
        json={
            "topic": "AI trends",
            "creative_output": creative_output,
            "research_output": research_output,
            "max_iterations": 2,
            "parent_task_id": creative_id
        }
    )
    assert qa_response.status_code == 200
    qa_data = qa_response.json()

    # Verify chaining
    assert qa_data["parent_task_id"] == creative_id
    assert qa_data["result"]["quality_score"] > 0
```

### Pattern 3: Validation Testing

**Test invalid inputs:**

```python
def test_research_missing_topic(auth_headers):
    """Test validation - missing required field"""
    response = client.post(
        "/api/content/subtasks/research",
        headers=auth_headers,
        json={
            "keywords": ["ML"]  # Missing 'topic'
        }
    )

    assert response.status_code == 422  # Validation error
    data = response.json()
    assert "detail" in data

def test_qa_without_auth():
    """Test authorization - missing auth header"""
    response = client.post(
        "/api/content/subtasks/qa",
        json={
            "topic": "Test",
            "creative_output": "Content"
        }
    )

    assert response.status_code == 403  # Forbidden
```

### Pattern 4: Database State Verification

**Verify database updates after subtask:**

```python
def test_subtask_creates_database_record(auth_headers, db_service):
    """Test that subtask creates database record"""

    response = client.post(
        "/api/content/subtasks/research",
        headers=auth_headers,
        json={
            "topic": "Test topic",
            "keywords": []
        }
    )

    assert response.status_code == 200
    subtask_id = response.json()["subtask_id"]

    # Verify database record
    task = db_service.get_task_by_id(subtask_id)
    assert task is not None
    assert task["task_type"] == "subtask"
    assert task["status"] == "completed"
    assert task["metadata"]["stage"] == "research"
```

---

## 🐛 Common Issues

### Issue 1: "Invalid or expired token"

**Symptom:**

```json
{ "detail": "Invalid or expired token" }
```

**Cause:** Missing or invalid JWT token in Authorization header

**Solution:**

```bash
# Generate a valid token (depends on your auth implementation)
# For testing, you might bypass auth or use a test token

# Option 1: Check if auth is actually required
curl http://localhost:8000/api/health  # Should work without auth

# Option 2: Get a valid token from your auth system
# See: docs/reference/TESTING.md for token generation

# Option 3: Test without auth by modifying test setup
# Edit tests to disable auth temporarily during development
```

### Issue 2: Database connection error

**Symptom:**

```json
{ "detail": "Database connection failed" }
```

**Cause:** PostgreSQL not running or connection string incorrect

**Solution:**

```bash
# Verify PostgreSQL is running
psql $DATABASE_URL -c "SELECT 1"

# Check connection string
echo $DATABASE_URL
# Expected format: postgresql://user:password@localhost:5432/dbname

# If not set, check .env file
cat src/cofounder_agent/.env | grep DATABASE_URL
```

### Issue 3: Model router fails to initialize

**Symptom:**

```
RuntimeError: No AI model providers available
```

**Cause:** No LLM providers configured (OpenAI, Anthropic, Ollama, etc.)

**Solution:**

```bash
# Check which providers are available
curl http://localhost:8000/api/models/status

# Configure at least one provider:

# Option 1: Use Ollama (free, local)
ollama serve  # In another terminal
export OLLAMA_HOST=http://localhost:11434

# Option 2: Set API keys
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export GOOGLE_API_KEY="AIza-..."

# Restart backend
pkill -f "python main.py"
cd src/cofounder_agent && python main.py
```

### Issue 4: Subtask endpoint returns 404

**Symptom:**

```json
{ "detail": "Not Found" }
```

**Cause:** Subtask routes not properly registered in FastAPI app

**Solution:**

```bash
# Verify endpoint exists in Swagger UI
curl -s http://localhost:8000/docs | grep -i "subtask"

# Check main.py includes subtask router
grep "subtask_router" src/cofounder_agent/main.py

# Expected output:
# from routes.subtask_routes import router as subtask_router
# app.include_router(subtask_router)
```

### Issue 5: Parent task ID not working for chaining

**Symptom:** parent_task_id is ignored or causes errors

**Solution:**

```bash
# Make sure parent task exists in database
curl http://localhost:8000/api/tasks/{parent_task_id}

# If parent doesn't exist, create it first
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "title": "Parent Task",
    "type": "content_generation"
  }'

# Then use returned task ID as parent_task_id in subtask request
```

---

## 📊 Performance Metrics

### Expected Response Times

| Subtask  | Duration | Model      | Tokens    |
| -------- | -------- | ---------- | --------- |
| Research | 15-20s   | GPT-4      | 1000-2000 |
| Creative | 20-30s   | GPT-4      | 2000-4000 |
| QA       | 10-15s   | GPT-4      | 1500-3000 |
| Images   | 5-10s    | Vision API | 0         |
| Format   | 2-5s     | GPT-3.5    | 500-1000  |

### Cost Estimation

- **Research:** $0.03-0.06
- **Creative:** $0.06-0.12
- **QA:** $0.04-0.09
- **Images:** Free (Pexels API)
- **Format:** $0.01-0.02

---

## ✅ Before Submitting Tests

```bash
# 1. Run all subtask tests
cd src/cofounder_agent
python -m pytest tests/ -v -k "subtask"

# 2. Check coverage
python -m pytest tests/test_subtask_endpoints.py --cov=routes.subtask_routes

# 3. Test manually with curl
curl -X POST http://localhost:8000/api/content/subtasks/research \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-token" \
  -d '{"topic":"Test"}'

# 4. Verify database records created
# Check PostgreSQL: SELECT * FROM tasks WHERE task_type = 'subtask';
```

---

## 🔗 Related Documentation

- **[Full Testing Guide](../reference/TESTING.md)** - Comprehensive testing patterns
- **[API Contracts](../reference/API_CONTRACT_CONTENT_CREATION.md)** - Full API spec
- **[Architecture](../02-ARCHITECTURE_AND_DESIGN.md)** - System design
- **[AI Agents](../05-AI_AGENTS_AND_INTEGRATION.md)** - Agent orchestration

---

**Happy Testing! 🚀**

For questions about specific subtasks, check the implementation:  
`src/cofounder_agent/routes/subtask_routes.py`
