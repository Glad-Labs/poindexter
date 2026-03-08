# GitHub Issues - Technical Debt Analysis (March 8, 2026)

**Purpose:** Specifications for creating/updating GitHub issues from codebase technical debt analysis  
**Date:** March 8, 2026  
**Scan Results:** 15 total debt items identified (0 P1, 0 P2, 12 P3, 3 P4)

---

## New Issues to Create

### Issue #51: [P3-MEDIUM] Optimize database queries (SELECT \* to specific columns)

**Title:** `[P3-MEDIUM] Optimize database queries - replace SELECT * with specific columns`

**Labels:** `type:enhancement`, `priority:medium`, `area:database`, `performance`

**Milestone:** Phase 3A

**Description:**

````markdown
## Problem

Database queries using SELECT \* are inefficient and should specify exact columns needed.
This improves:

- Network bandwidth (smaller result sets)
- Query execution (database doesn't load unnecessary columns)
- Memory usage (application)
- Cache Hit ratio

## Current Findings

10+ instances of SELECT \* found in codebase:

### Files Identified

1. **admin_db.py**
   - Line 136: `SELECT * FROM cost_logs`
   - Line 235: `SELECT * FROM settings WHERE key = $1`
   - Line 261: `SELECT * FROM settings WHERE category = $1 AND is_active = true`
   - Line 264: `SELECT * FROM settings WHERE is_active = true ORDER BY key`

2. **custom_workflows_service.py**
   - Line 946: `SELECT * FROM workflow_executions WHERE id = $1 AND owner_id = $2`

3. **route_utils.py**
   - Lines 52, 199, 217, 466, 496, 520, 543: Multiple `SELECT * FROM tasks`

4. **postgres_cms_client.py**
   - Line 261: `SELECT * FROM posts WHERE slug = $1`

## Solution

Replace all SELECT \* with explicit column lists based on actual usage:

```python
# Before
sql = "SELECT * FROM settings WHERE key = $1 AND is_active = true"
row = await conn.fetchrow(sql, key)

# After
sql = "SELECT id, key, value, category, updated_at FROM settings WHERE key = $1 AND is_active = true"
row = await conn.fetchrow(sql, key)
```
````

## Acceptance Criteria

- [ ] All 10+ SELECT \* statements audited
- [ ] Columns needed for each query documented
- [ ] Queries updated with explicit column lists
- [ ] No functional regressions
- [ ] Performance improvement measured (query execution time)

## Effort Estimate

4-6 hours

## Related Issues

None

## Priority Justification

Improves query performance and reduces database load before production scaling.

````

---

## Issues to Update

### Issue #36: Add comprehensive type hints to service functions

**Title:** `[P3-MEDIUM] Add comprehensive type hints to service layer`

**Current Status:** Open

**Update:** Add detailed scope and effort estimates

**New Content to Add:**

```markdown
## Codebase Metrics (March 8, 2026 Scan)

- **Service Modules:** 116 files in `src/cofounder_agent/services/`
- **Type Hint Coverage:** Unknown but incomplete
- **Affected Scope:** All 116 service modules

## Recommended Approach

1. Start with high-impact services (database_service.py, model_router.py, task_executor.py)
2. Use Python 3.10+ type hints (from __future__ import annotations)
3. Focus on function parameters AND return types
4. Use proper types (not generic Any)

## Effort Estimate

20-30 hours for comprehensive coverage

## Benefits

- Better IDE autocomplete and error detection
- Improved code maintainability
- Easier onboarding for new developers
````

---

### Issue #37: Standardize exception handling

**Title:** `[P3-MEDIUM] Standardize exception handling - replace generic Exception handlers`

**Current Status:** In Progress (Phase 1C partial)

**Update:** Add detailed audit findings

**New Content to Add:**

````markdown
## Codebase Findings (March 8, 2026 Scan)

### Generic Exception Handlers Found: 30+ instances

Routes affected:

- routes/analytics_routes.py (3 instances: lines 372, 406, 500)
- routes/agent_registry_routes.py (8 instances: lines 117, 150, 199, 235, 271, 320, 368, 417, 476)
- routes/agents_routes.py (8 instances: lines 127, 152, 193, 258, 322, 394, 478, ...)
- routes/chat_routes.py (5 instances: lines 215, 256, 303, 342, 369)
- routes/auth_unified.py (2 instances: lines 348, 380)
- migrations/ (multiple migration files)

### Current Pattern

```python
except Exception as e:
    logger.error(f"[operation_name] message", exc_info=True)
    # Fallback or error response
```
````

### Recommended Pattern

```python
except DatabaseError as e:
    logger.error(f"[operation_name] Database error", exc_info=True)
    return error_response(500, "Database unavailable")
except ValidationError as e:
    logger.error(f"[operation_name] Validation failed", exc_info=True)
    return error_response(422, str(e))
except Exception as e:
    logger.error(f"[operation_name] Unexpected error", exc_info=True)
    return error_response(500, "Internal server error")
```

## Status

- Phase 1C: Partial standardization in some service files
- Remaining: Complete routes and migrations

## Effort Estimate

8-10 hours to complete

## Related Issues

- Phase 1C completion
- Issue #6 (closed) - Similar pattern standardization

````

---

### Issue #20: Add test coverage for frontend applications

**Title:** `[P3-MEDIUM] Add test coverage for Oversight Hub (React) and Public Site (Next.js)`

**Current Status:** Open

**Update:** Add comprehensive scope and metrics

**New Content to Add:**

```markdown
## Current Situation (March 8, 2026)

### Test Coverage Status

| Application | Framework | Test Files | Status |
|-------------|-----------|-----------|--------|
| **Oversight Hub** | React 18 + Material-UI | 0 | ⚠️ MISSING |
| **Public Site** | Next.js 15 (App Router) | 0 | ⚠️ MISSING |

This is a **CRITICAL GAP** for production releases.

### Impact

Without frontend tests:
- No automated regression detection
- Cannot integrate into CI/CD pipeline
- Manual testing burden on every release
- Higher risk of bugs in production

## Recommended Approach

### Oversight Hub (React 18 + Material-UI)

Use **Vitest** + React Testing Library:

```bash
npm install -D vitest @vitest/ui @testing-library/react @testing-library/jest-dom
````

**Priority Components to Test:**

1. Dashboard layout and navigation (LayoutWrapper.jsx)
2. Agent management (agent list, create, update)
3. Task monitoring (task status, history, metrics)
4. Chat interface (message display, input)
5. Model selection
6. Critical workflows

**Target:** 5-10 component tests minimum for MVP

### Public Site (Next.js 15)

Use **Jest** + React Testing Library:

```bash
npm install -D jest @testing-library/react @testing-library/jest-dom babel-jest
```

**Priority Pages to Test:**

1. Home page (renders)
2. Blog post page (markdown rendering, metadata)
3. Contact form (validation, submission)
4. OAuth callback (basic flow)
5. Navigation (links work)

**Target:** 3-5 page tests minimum for MVP

## Effort Estimates

- **Oversight Hub Tests:** 15-20 hours (setup + 5-10 tests)
- **Public Site Tests:** 10-15 hours (setup + 3-5 tests)
- **CI/CD Integration:** 5 hours
- **Total:** 30-40 hours

## Benefits

- Automated regression detection
- Confidence in releases
- CI/CD integration (block merges if tests fail)
- Faster development cycle (tests catch bugs early)

## Critical for Release

This is **BLOCKING** for Phase 4 release candidates.

## Related Issues

- None directly, but blocks downstream release testing

````

---

### Issue #38: Add rate limiting middleware for DoS protection

**Title:** `[P3-MEDIUM][Security] Add rate limiting middleware for DoS protection`

**Current Status:** Open

**Update:** Add implementation guidance

**New Content to Add:**

```markdown
## Priority

HIGH - Security risk if not implemented before production

## Current State

No rate limiting middleware. All endpoints are freely accessible.

## Risk Assessment

- API endpoints exposed to public internet
- Vulnerable to denial-of-service (DoS) attacks
- No protection for expensive endpoints (content generation, image processing)

## Recommended Implementation

Use **slowapi** (FastAPI extension for rate limiting):

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter

@app.get("/api/tasks")
@limiter.limit("100/minute")
async def get_tasks(request: Request):
    # ... endpoint logic
````

## Rate Limiting Strategy

```
Global: 100 requests/minute per IP
/api/tasks: 50 requests/minute
/api/workflows/execute: 10 requests/minute (expensive)
/api/content-generation: 5 requests/minute (very expensive)
/api/auth/*: 30 requests/minute
```

## Effort Estimate

4-6 hours (setup + configuration + testing)

## Acceptance Criteria

- [ ] Rate limiting middleware configured
- [ ] Rate limits applied to all public endpoints
- [ ] Different tiers for expensive operations
- [ ] Proper error response (429 Too Many Requests)
- [ ] Logging of rate limit violations
- [ ] Documentation updated

## Related Issues

- Issue #39 (webhook validation)
- Issue #41 (HTTP caching)

````

---

### Issue #40: Tune database connection pool for production

**Title:** `[P3-MEDIUM] Optimize database connection pool for production workloads`

**Current Status:** Open

**New Content to Add:**

```markdown
## Current Configuration

Located in `database_service.py` and `.env.local`:

```python
DATABASE_POOL_MIN_SIZE=5      # Minimum connections
DATABASE_POOL_MAX_SIZE=20     # Maximum connections
````

## Problem

Current settings are suitable for development but need tuning for production:

- Too few min connections under high load
- Max might be too high causing resource exhaustion
- No testing with realistic load patterns

## Tuning Parameters to Test

### Conservative (e.g., 3-person startup)

```
MIN_SIZE = 10
MAX_SIZE = 30
```

### Moderate Load (e.g., 50+ concurrent users)

```
MIN_SIZE = 20
MAX_SIZE = 50
```

### High Load (e.g., 500+ concurrent users)

```
MIN_SIZE = 50
MAX_SIZE = 150
```

## Testing Approach

1. Set up load test (e.g., with k6 or Apache JMeter)
2. Monitor connection usage:
   - `SELECT count(*) FROM pg_stat_activity;`
   - Monitor application response times
3. Adjust min_size until no connection idle timeouts
4. Adjust max_size until no connection exhaustion errors
5. Test failover/recovery behavior

## Reference Metric

PostgreSQL default max connections: 100

## Effort Estimate

3-5 hours (testing + tuning + documentation)

## Related Issues

- Issue #32 (performance monitoring) - related

````

---

### Issue #43: Implement training data capture in content phases

**Title:** `[P3-MEDIUM] Implement training data capture in content generation phases`

**Current Status:** Open

**Location:** `src/cofounder_agent/services/phases/content_phases.py:547`

**Update:** Add detailed purpose and approach

**New Content to Add:**

```markdown
## Purpose

Capture training data from content generation pipeline to enable:
- Fine-tuning custom models
- Quality analysis and improvement
- User preference learning
- A/B testing variations

## Current TODO

File: `src/cofounder_agent/services/phases/content_phases.py` (line 547)

```python
# TODO: Capture training data (inputs, outputs, quality scores) for fine-tuning
````

## Implementation Plan

### Data to Capture

```python
training_record = {
    "phase": "content_generation",
    "timestamp": datetime.now(),
    "model": "gpt-4",
    "cost_tier": "balanced",
    "input": {
        "topic": "...",
        "style": "...",
        "tone": "..."
    },
    "output": {
        "content": "...",
        "word_count": 1250,
        "readability_score": 8.5
    },
    "quality_metrics": {
        "relevance_score": 0.92,
        "engagement_score": 0.88,
        "seo_score": 0.85
    },
    "user_feedback": {
        "approved": True,
        "edits": "...",
        "quality_notes": "..."
    }
}
```

### Storage

1. **Phase 1:** PostgreSQL table `training_data`
   - Columns: id, phase, timestamp, model, input_json, output_json, metrics_json, user_feedback_json
   - Indexed by: timestamp, phase, model, approval status

2. **Phase 2 (Future):** Export to S3 for ML pipeline training

### Effort Estimate

6-8 hours (schema + collection + validation)

## Acceptance Criteria

- [ ] Schema created
- [ ] Collection during content phases
- [ ] Data validated
- [ ] Query performance acceptable
- [ ] No impact on content generation latency (<100ms overhead)

## Related Issues

- Issue #31 (GDPR) - ensure compliance with data retention

````

---

### Issue #45: Replace in-process workflow task queue with robust async queue

**Title:** `[P3-MEDIUM] Replace in-process workflow task queue with Celery/RQ/Arq`

**Current Status:** Open

**TODO Location:** `src/cofounder_agent/services/workflow_execution_adapter.py:730`

**Update:** Add comprehensive migration guidance

**New Content to Add:**

```markdown
## Current Problem

Current implementation: `asyncio.create_task()` in-process

Limitations:
- **No Persistence:** Tasks lost on restart
- **No Distribution:** Can't run on multiple servers
- **No Advanced Monitoring:** Limited observability
- **No Auto-retry:** Manual retry handling
- **No Scheduling:** Can't schedule future tasks
- **Single Point of Failure:** One machine = all tasks

## Recommended Solution

Migrate to **Celery** with Redis broker:

### Why Celery?

- Industry standard for Python async tasks
- Integration with FastAPI simple
- Redis broker is lightweight and fast
- Excellent monitoring tools available
- Battle-tested at scale

### Migration Path

#### Step 1: Install Dependencies

```bash
poetry add celery redis flower
````

#### Step 2: Create celery_app.py

```python
# src/cofounder_agent/celery_app.py
from celery import Celery
from kombu import Exchange, Queue

celery_app = Celery(
    'glad_labs',
    broker='redis://localhost:6379',
    backend='redis://localhost:6379'
)

celery_app.conf.task_serializer = 'json'
celery_app.conf.accept_content = ['json']
celery_app.conf.result_serializer = 'json'
celery_app.conf.task_track_started = True
celery_app.conf.task_time_limit = 30 * 60  # 30 minutes hard limit
celery_app.conf.task_soft_time_limit = 25 * 60  # 25 minutes soft limit

celery_app.conf.task_default_queue = 'workflows'
celery_app.conf.task_queues = (
    Queue('workflows', Exchange('workflows')),
    Queue('content', Exchange('content')),
    Queue('priority', Exchange('priority')),
)
```

#### Step 3: Create Task Wrapper

```python
# src/cofounder_agent/tasks/workflow_tasks.py
from celery_app import celery_app

@celery_app.task(bind=True, max_retries=3)
def execute_workflow_task(self, workflow_id: str, execution_id: str):
    try:
        # Move execute_custom_workflow_execution logic here
        result = execute_custom_workflow_execution(...)
        return {"status": "success", "result": result}
    except Exception as exc:
        # Auto-retry with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
```

#### Step 4: Update Routes

```python
# Before
asyncio.create_task(execute_custom_workflow_execution(...))

# After
from tasks.workflow_tasks import execute_workflow_task
execute_workflow_task.apply_async(
    args=(workflow_id, execution_id),
    queue='workflows'
)
```

### Testing

```bash
# Terminal 1: Redis
redis-server

# Terminal 2: Celery Worker
celery -A celery_app worker --loglevel=info

# Terminal 3: Flower Monitoring
celery -A celery_app flower

# Terminal 4: FastAPI
uvicorn main:app --reload
```

## Effort Estimate

12-16 hours

- Setup & configuration: 2 hours
- Task migration: 6 hours
- Testing & monitoring: 3 hours
- Documentation: 2 hours
- Monitoring (Flower integration): 1 hour

## Acceptance Criteria

- [ ] Celery installed and configured
- [ ] Workflow tasks migrated to Celery
- [ ] Redis persistence verified
- [ ] Monitoring (Flower) working
- [ ] Auto-retry logic functional
- [ ] No regressions in workflow execution
- [ ] Performance impact <50ms per task

## Production Considerations

- Run Celery workers in separate containers/processes
- Use Redis Sentinel for high availability
- Set up monitoring alerts for task failure rates
- Configure task routing by priority queue

## Related Issues

- Issue #41 (HTTP caching) - monitor cache hit rates

```

---

## Summary

### Issues by Priority

| # | ID | Title | Effort | Status |
|----|-----|-------|---------|---------|
| 1 | 51 | Database query optimization | 4-6h | **NEW** |
| 2 | 20 | Frontend test coverage | 30-40h | **CRITICAL UPDATE** |
| 3 | 37 | Exception standardization | 8-10h | **UPDATE** |
| 4 | 36 | Type hints | 20-30h | **UPDATE** |
| 5 | 38 | Rate limiting | 4-6h | **UPDATE** |
| 6 | 40 | Connection pool tuning | 3-5h | **UPDATE** |
| 7 | 43 | Training data capture | 6-8h | **UPDATE** |
| 8 | 45 | Async queue (Celery) | 12-16h | **UPDATE** |

### Total Effort Estimate

**87-115 hours** (2-3 person-weeks)

### Recommended Execution Order

1. **Week 1:** #20 (frontend tests), #37 (exceptions), #38 (rate limiting)
2. **Week 2:** #45 (async queue), #51 (query optimization)
3. **Week 3:** #36 (type hints), #40 (pool tuning), #43 (training data)

---

**Created:** March 8, 2026
**Repository:** Glad-Labs/glad-labs-codebase
**Branch:** dev
```
