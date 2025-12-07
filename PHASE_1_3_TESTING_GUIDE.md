# 🧪 Phase 1-3 Testing Guide - Unified Task Orchestration

**Last Updated:** November 24, 2025  
**Status:** Ready for Testing  
**What to Test:** Natural Language → Execution Plan → Task Creation

---

## 🚀 Quick Start

### 1. Start Backend Services

```bash
# Option A: Full stack
npm run dev

# Option B: Backend only
cd src/cofounder_agent
python main.py
```

**Expected Output:**

```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

### 2. Verify Services Running

```bash
# Backend API
curl http://localhost:8000/docs

# FastAPI Swagger UI should load
```

---

## 🧪 Test Sequence: Phase 1-3 Complete Flow

### Test 1: Intent Parsing (TaskIntentRouter)

**Request:**

```bash
curl -X POST http://localhost:8000/api/tasks/intent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "user_input": "Generate blog post about AI trends + images, high quality",
    "business_metrics": {
      "budget": 50.0,
      "quality_preference": "high"
    }
  }'
```

**Expected Response (200 OK):**

```json
{
  "intent_request": {
    "intent_type": "content_generation",
    "task_type": "blog_post",
    "confidence": 0.95,
    "parameters": {
      "topic": "AI trends",
      "include_images": true,
      "quality_preference": "high"
    },
    "suggested_subtasks": ["research", "creative", "qa", "images", "format"],
    "requires_confirmation": false,
    "execution_strategy": "sequential"
  },
  "execution_plan": {
    "title": "Blog Post Execution Plan",
    "description": "Create content through 5 stages: research, creative, qa, images, format",
    "estimated_time": "~2 minutes",
    "estimated_cost": "$0.40",
    "confidence": "High",
    "key_stages": [
      "Research (15s) - Gather information about AI trends",
      "Creative (38s) - Generate high quality draft",
      "QA (12s) - Review for quality",
      "Images (8s) - Find relevant images",
      "Format (3s) - Format for publication"
    ],
    "warnings": null,
    "opportunities": null,
    "total_estimated_duration_ms": 76000,
    "total_estimated_cost": 0.4,
    "parallelization_strategy": "sequential"
  },
  "ready_to_execute": true,
  "warnings": null
}
```

**✅ Validation:**

- [ ] `intent_type` correctly detected as "content_generation"
- [ ] `task_type` correctly mapped to "blog_post"
- [ ] `parameters.topic` extracted as "AI trends"
- [ ] `parameters.include_images` detected as true
- [ ] `suggested_subtasks` includes all 5 stages
- [ ] `execution_plan.estimated_time` shows realistic duration
- [ ] `execution_plan.estimated_cost` calculated correctly
- [ ] `confidence` level reasonable (High, Medium, Low)

---

### Test 2: Alternative Strategies

**Request (Same endpoint, different input):**

```bash
curl -X POST http://localhost:8000/api/tasks/intent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "user_input": "Generate blog post about AI trends + images, quick and cheap",
    "business_metrics": {
      "budget": 10.0,
      "quality_preference": "draft"
    }
  }'
```

**Expected Response (200 OK):**

```json
{
  "execution_plan": {
    "estimated_time": "~1 minute",
    "estimated_cost": "$0.23",
    "confidence": "Medium",
    "parallelization_strategy": "sequential"
  }
}
```

**✅ Validation:**

- [ ] `draft` quality reduces estimated cost
- [ ] Time estimate adjusted downward (~1 min vs ~2 min)
- [ ] Cost lower than high-quality version
- [ ] Confidence level adjusted (Medium vs High)

---

### Test 3: Task Confirmation & Creation

**Request:**

```bash
# Use response from Test 1
curl -X POST http://localhost:8000/api/tasks/confirm-intent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "intent_request": {
      "intent_type": "content_generation",
      "task_type": "blog_post",
      "confidence": 0.95,
      "parameters": {
        "topic": "AI trends",
        "include_images": true
      },
      "suggested_subtasks": ["research", "creative", "qa", "images", "format"]
    },
    "execution_plan": {
      "title": "Blog Post Execution Plan",
      "total_estimated_duration_ms": 76000,
      "total_estimated_cost": 0.4
    },
    "user_confirmed": true,
    "modifications": null
  }'
```

**Expected Response (201 Created):**

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Task created and queued for execution. Estimated duration: 76 seconds",
  "execution_plan_id": "plan-550e8400-e29b"
}
```

**✅ Validation:**

- [ ] `task_id` generated (UUID format)
- [ ] `status` is "pending"
- [ ] `execution_plan_id` stored for reference
- [ ] Task visible in database

---

### Test 4: Task Status Polling

**Request (immediately after Test 3):**

```bash
curl -X GET http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000 \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Expected Response (200 OK) - First Poll (0-5 seconds):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "task_name": "Generate blog post about AI trends + images",
  "task_type": "blog_post",
  "status": "pending",
  "metadata": {
    "execution_plan": {
      "total_estimated_duration_ms": 76000,
      "stages": [...]
    },
    "intent_request": {...}
  }
}
```

**Expected Response - Second Poll (5-15 seconds):**

```json
{
  "status": "in_progress",
  "metadata": {
    "current_stage": "research",
    "stages_completed": 0,
    "stages_total": 5,
    "estimated_time_remaining": "~70s"
  }
}
```

**Expected Response - After 80 seconds:**

```json
{
  "status": "completed",
  "result": {
    "content": "# AI Trends: Understanding the Future of Artificial Intelligence\n\n...",
    "featured_image_url": "https://...",
    "post_id": "strapi-post-123"
  },
  "metadata": {
    "stages_completed": 5,
    "total_execution_time_ms": 76000,
    "total_cost": 0.4,
    "quality_score": 87.5
  }
}
```

**✅ Validation:**

- [ ] `status` transitions: pending → in_progress → completed
- [ ] `current_stage` updates as execution progresses
- [ ] `estimated_time_remaining` decreases
- [ ] Final result contains generated content
- [ ] `featured_image_url` populated
- [ ] `quality_score` calculated

---

### Test 5: Independent Subtask Execution

**Test 5a: Research Only**

```bash
curl -X POST http://localhost:8000/api/content/subtasks/research \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "topic": "Machine Learning",
    "keywords": ["neural networks", "deep learning"]
  }'
```

**Expected Response (200 OK):**

```json
{
  "subtask_id": "subtask-uuid",
  "stage": "research",
  "status": "completed",
  "result": {
    "research_data": "Research findings about machine learning...",
    "sources": [...],
    "key_points": [...]
  },
  "metadata": {
    "duration_ms": 14200,
    "tokens_used": 2450,
    "model": "gpt-4",
    "quality_score": 88.5
  }
}
```

**Test 5b: Use Research in Creative**

```bash
curl -X POST http://localhost:8000/api/content/subtasks/creative \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "topic": "Machine Learning",
    "style": "technical",
    "tone": "authoritative",
    "target_length": 2000,
    "research_output": "Research findings about machine learning..."
  }'
```

**Expected Response (200 OK):**

```json
{
  "subtask_id": "subtask-uuid-2",
  "stage": "creative",
  "status": "completed",
  "result": {
    "title": "Machine Learning: A Comprehensive Technical Guide",
    "content": "# Machine Learning: A Comprehensive Technical Guide\n\n...",
    "estimated_reading_time": 8
  },
  "metadata": {
    "duration_ms": 26500,
    "tokens_used": 3200,
    "model": "claude-opus",
    "quality_score": 92.0
  }
}
```

**Test 5c: QA Review**

```bash
curl -X POST http://localhost:8000/api/content/subtasks/qa \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "topic": "Machine Learning",
    "creative_output": "# Machine Learning: A Comprehensive Technical Guide\n\n...",
    "research_output": "Research findings about machine learning...",
    "style": "technical",
    "tone": "authoritative",
    "max_iterations": 2
  }'
```

**Expected Response (200 OK):**

```json
{
  "subtask_id": "subtask-uuid-3",
  "stage": "qa",
  "status": "completed",
  "result": {
    "refined_content": "# Machine Learning: A Comprehensive Technical Guide\n\n...",
    "feedback": "Content is well-structured and technically accurate. Added section on practical applications.",
    "quality_score": 94.5,
    "approved": true
  },
  "metadata": {
    "iterations": 1,
    "duration_ms": 11800,
    "tokens_used": 2100
  }
}
```

**✅ Validation (All subtasks):**

- [ ] Each subtask returns a unique `subtask_id`
- [ ] `stage` correctly identified
- [ ] `status` is "completed" (or "failed" with error details)
- [ ] Results contain expected output for stage
- [ ] `metadata` includes execution metrics
- [ ] Can chain subtasks: research output → creative input
- [ ] QA score increases after review

---

### Test 6: Error Cases

**Test 6a: Missing Required Parameter**

```bash
curl -X POST http://localhost:8000/api/tasks/intent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "user_input": "",
    "business_metrics": {}
  }'
```

**Expected Response (422 Unprocessable Entity):**

```json
{
  "detail": [
    {
      "loc": ["body", "user_input"],
      "msg": "ensure this value has at least 10 characters",
      "type": "value_error.string.min_length"
    }
  ]
}
```

**Test 6b: Invalid Intent**

```bash
curl -X POST http://localhost:8000/api/tasks/intent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "user_input": "asdfgh zxcvbn poiuytrewq lkjhgfdsazxcvbn random gibberish",
    "business_metrics": {}
  }'
```

**Expected Response (200 OK with fallback):**

```json
{
  "intent_request": {
    "intent_type": "generic",
    "task_type": "generic",
    "confidence": 0.15,
    "parameters": {},
    "suggested_subtasks": ["research", "format"],
    "warnings": [
      "Low confidence detection - you may need to clarify your request"
    ]
  },
  "ready_to_execute": false
}
```

**Test 6c: Unauthorized Request**

```bash
curl -X POST http://localhost:8000/api/tasks/intent \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "Generate blog post"
  }'
# No Authorization header
```

**Expected Response (401 Unauthorized):**

```json
{
  "detail": "Not authenticated"
}
```

**✅ Validation:**

- [ ] Missing parameters properly validated
- [ ] Invalid input handled gracefully
- [ ] Low-confidence detection shows warnings
- [ ] Unauthorized requests rejected
- [ ] Error messages helpful and informative

---

## 📊 Test Results Template

```markdown
## Phase 1-3 Testing Results

**Date:** [Date]
**Tester:** [Name]

### Test 1: Intent Parsing

- [ ] Intent Type Detected ✓
- [ ] Task Type Mapped ✓
- [ ] Parameters Extracted ✓
- [ ] Subtasks Listed ✓
- [ ] Execution Plan Generated ✓
- Status: PASS / FAIL

### Test 2: Alternative Strategies

- [ ] Draft Option Lower Cost ✓
- [ ] High Quality Higher Cost ✓
- [ ] Confidence Levels Adjusted ✓
- Status: PASS / FAIL

### Test 3: Task Confirmation

- [ ] Task Created ✓
- [ ] Status Set to "pending" ✓
- [ ] Background Executor Started ✓
- Status: PASS / FAIL

### Test 4: Task Status Polling

- [ ] Status Transitions Correct ✓
- [ ] Stages Progress Tracked ✓
- [ ] Final Result Contains Content ✓
- Status: PASS / FAIL

### Test 5: Independent Subtasks

- [ ] Research Executes ✓
- [ ] Creative Uses Research ✓
- [ ] QA Reviews Content ✓
- [ ] Subtasks Chainable ✓
- Status: PASS / FAIL

### Test 6: Error Handling

- [ ] Validation Errors Clear ✓
- [ ] Low Confidence Warnings ✓
- [ ] Auth Failures Proper ✓
- Status: PASS / FAIL

### Overall: PASS / FAIL
```

---

## 🐛 Troubleshooting

### Issue: 401 Unauthorized on All Requests

**Cause:** No valid JWT token provided

**Solution:**

```bash
# Option 1: Get token from UI
# Login to Oversight Hub, copy token from localStorage

# Option 2: Use test token (if available)
# Check src/cofounder_agent/tests/conftest.py for test setup

# Option 3: Bypass for testing (development only)
# Temporarily disable auth in main.py (NOT for production)
```

### Issue: 404 Not Found on /api/tasks/intent

**Cause:** Subtask routes not registered

**Solution:**

```bash
# Verify registration in main.py
grep "subtask_router" src/cofounder_agent/main.py

# Should see 2 matches:
# 1. Import: from routes.subtask_routes import router as subtask_router
# 2. Registration: app.include_router(subtask_router)

# If missing, run setup:
npm run setup:all
npm run dev:cofounder
```

### Issue: 500 Internal Server Error

**Cause:** Service dependency missing

**Solution:**

```bash
# Check logs for specific error
# Look for: [ERROR] in console output

# Common issues:
# 1. NLPIntentRecognizer not initialized
# 2. TaskIntentRouter missing models
# 3. Database connection failed

# Check database
psql $DATABASE_URL -c "SELECT 1"

# Restart services
npm run dev:cofounder
```

### Issue: Intent Type Always "generic"

**Cause:** NLPIntentRecognizer not finding patterns

**Solution:**

```bash
# Verify NLP service loaded
curl http://localhost:8000/api/health

# Test with clearer intent
"Generate a blog post about machine learning with images"
# vs
"asdfgh zxcvbn poiuytrewq"

# Check confidence score
# If < 0.5, input is too ambiguous
```

---

## ✅ Sign-Off Checklist

- [ ] All 6 tests executed successfully
- [ ] Error cases handled gracefully
- [ ] Performance acceptable (<2s response times)
- [ ] Database integration working
- [ ] Task execution follows plan
- [ ] Independent subtasks callable
- [ ] Documentation accurate
- [ ] Ready for Phase 4 (UI Enhancement)

---

**Status:** ✅ Testing Guide Complete  
**Ready for:** Phase 1-3 Validation Testing  
**Next Phase:** Phase 4 (UI Enhancement)
