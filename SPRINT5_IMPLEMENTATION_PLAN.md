# Sprint 5 Implementation Plan: Performance & Analytics

**Status:** STARTING  
**Scheduled:** February 19, 2026 onwards  
**Estimated Effort:** 12 hours  
**Sprint Goal:** Add observability (metrics collection) and build analytics dashboard to identify performance bottlenecks

---

## 🎯 Sprint Objectives

1. **Task 5.1:** Add instrumentation to TaskExecutor (4h)
   - Collect execution time per phase
   - Track token usage and API costs
   - Monitor error rates and retries
   - Log queue wait times

2. **Task 5.2:** Build Analytics Dashboard (5h)
   - Display execution time trends
   - Show cost breakdown by model/provider
   - Visualize error rates
   - Create performance leaderboard

3. **Task 5.3:** Profile & Optimize Slowest Paths (3h)
   - Identify bottleneck phases
   - Implement targeted optimizations
   - Reduce average execution time by 10-20%

---

## Current State Analysis

### Existing Infrastructure

**Logging System:**
- ✅ Python logging configured in main.py
- ✅ Loggers used throughout services
- ✅ Log level configurable via environment
- 🟡 No structured metrics logging yet

**Database:**
- ✅ `tasks` table with status tracking
- ✅ `metadata` JSONB field for storing extra data
- 🟡 No dedicated metrics/analytics table yet
- 🟡 No indexes for analytics queries

**Frontend:**
- ✅ Oversight Hub dashboard exists
- ✅ Material-UI components available
- ⚠️ No dedicated analytics component yet
- ⚠️ No real-time metrics endpoint yet

**Existing Dashboards:**
- ✅ ExecutiveDashboard in components/pages/
- ✅ PerformanceDashboard in routes/
- ✅ CostMetricsDashboard in routes/
- ⏳ Need to extend/enhance these with new metrics

### What Needs to Be Built

```
┌─────────────────────────────────────┐
│    Task Executor Instrumentation    │  ← Task 5.1
│    (Collect metrics per phase)      │
└────────────┬────────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│    Metrics Storage (admin_logs)     │  ← New or enhance
│    (Persist collected data)         │
└────────────┬────────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│    Analytics API Endpoints          │  ← New routes
│    (Query metrics by time/phase)    │
└────────────┬────────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│    Analytics Dashboard              │  ← Task 5.2
│    (Visualize trends & bottlenecks) │
└─────────────────────────────────────┘
```

---

## Task Breakdown

### Task 5.1: Add Instrumentation to TaskExecutor (4h)

#### Phase 1a: Define Metrics Collection Points

**Metrics to Collect:**

1. **Phase Execution Time**
   ```python
   {
       "task_id": "uuid",
       "phase": "research|draft|assess|refine|finalize|publish",
       "duration_ms": 45000,
       "start_time": "2026-02-19T10:30:00Z",
       "end_time": "2026-02-19T10:30:45Z"
   }
   ```

2. **LLM API Calls**
   ```python
   {
       "task_id": "uuid",
       "llm_call_id": "uuid",
       "phase": "research",
       "model": "claude-3.5-sonnet",
       "provider": "anthropic",
       "tokens_in": 1250,
       "tokens_out": 3100,
       "cost_usd": 0.045,
       "duration_ms": 5200,
       "status": "success"  # or "error", "retry"
   }
   ```

3. **Queue Metrics**
   ```python
   {
       "task_id": "uuid",
       "queue_wait_ms": 2300,
       "queue_position_start": 15,
       "queue_position_end": 1,
       "timestamp": "2026-02-19T10:30:00Z"
   }
   ```

4. **Error Tracking**
   ```python
   {
       "task_id": "uuid",
       "phase": "research",
       "error_type": "APIError|ValidationError|TimeoutError|etc",
       "error_message": "Pexels API timeout after 10s",
       "retry_count": 2,
       "final_status": "success"  # or "failed"
   }
   ```

#### Phase 1b: Modify TaskExecutor

**File:** `src/cofounder_agent/services/task_executor.py`

**Changes:**
1. Add metrics collection class
   ```python
   class TaskMetrics:
       def __init__(self, task_id: str):
           self.task_id = task_id
           self.phases = {}  # phase_name -> {duration_ms, timestamp}
           self.llm_calls = []
           self.errors = []
           self.queue_wait_ms = 0
   ```

2. Instrument execute_task() method
   ```python
   async def execute_task(self, task_id: str) -> ExecutionResult:
       metrics = TaskMetrics(task_id)
       
       # Measure queue wait time
       metrics.queue_wait_ms = measure_queue_wait(task_id)
       
       # Instrument each phase
       for phase in [research, draft, assess, refine, finalize, publish]:
           start = time.time()
           try:
               result = await orchestrator.run_phase(phase)
               duration = (time.time() - start) * 1000
               metrics.phases[phase] = {
                   "duration_ms": duration,
                   "status": "success"
               }
           except Exception as e:
               metrics.errors.append({
                   "phase": phase,
                   "error": str(e),
                   "duration_ms": (time.time() - start) * 1000
               })
       
       # Store metrics
       await db_service.save_metrics(metrics)
       return ExecutionResult(...)
   ```

3. Add LLM call instrumentation
   ```python
   # In model_router.py or when calling LLMs
   async def call_llm_with_metrics(self, prompt: str, model: str):
       start = time.time()
       tokens_in = count_tokens(prompt)
       
       try:
           response = await llm_client.complete(prompt, model=model)
           tokens_out = count_tokens(response)
           duration = (time.time() - start) * 1000
           
           cost = calculate_cost(model, tokens_in, tokens_out)
           
           # Log metrics
           await db_service.log_llm_call({
               "model": model,
               "tokens_in": tokens_in,
               "tokens_out": tokens_out,
               "cost_usd": cost,
               "duration_ms": duration,
               "status": "success"
           })
           
           return response
       except Exception as e:
           # Log error
           await db_service.log_llm_call({
               "model": model,
               "tokens_in": tokens_in,
               "duration_ms": (time.time() - start) * 1000,
               "status": "error",
               "error": str(e)
           })
           raise
   ```

#### Phase 1c: Create Analytics API Endpoints

**File:** `src/cofounder_agent/routes/analytics_routes.py` (already exists, will enhance)

**Endpoints to Add:**

1. **GET /api/analytics/execution-time**
   ```
   Query: ?period=1h|1d|1w|1mo&phase=all|research|draft|...
   
   Return: {
       "period": "1d",
       "data": [
           {"timestamp": "2026-02-19T10:00Z", "avg_ms": 125000, "min_ms": 45000, "max_ms": 300000},
           ...
       ]
   }
   ```

2. **GET /api/analytics/costs**
   ```
   Query: ?period=1d&breakdown=by_model|by_provider|by_phase
   
   Return: {
       "total_cost_usd": 125.45,
       "period": "1d",
       "breakdown": [
           {"model": "claude-3.5-sonnet", "cost_usd": 78.90, "percent": 63},
           {"model": "gpt-4-turbo", "cost_usd": 46.55, "percent": 37}
       ]
   }
   ```

3. **GET /api/analytics/errors**
   ```
   Query: ?period=1d&phase=all
   
   Return: {
       "error_rate": 0.05,  # 5%
       "total_tasks": 200,
       "failed_tasks": 10,
       "errors_by_phase": [
           {"phase": "research", "count": 4, "rate": 0.02},
           {"phase": "finalize", "count": 6, "rate": 0.03}
       ]
   }
   ```

4. **GET /api/analytics/slowest-tasks**
   ```
   Query: ?limit=10&phase=all
   
   Return: {
       "tasks": [
           {
               "task_id": "uuid",
               "task_name": "Blog: AI Trends",
               "duration_ms": 285000,
               "phase_breakdown": {
                   "research": 45000,
                   "draft": 90000,
                   "assess": 35000,
                   "refine": 75000,
                   "finalize": 20000,
                   "publish": 20000
               }
           },
           ...
       ]
   }
   ```

5. **GET /api/analytics/queue-metrics**
   ```
   Query: ?period=1d
   
   Return: {
       "avg_queue_wait_ms": 3200,
       "max_queue_depth": 45,
       "current_queue_depth": 8
   }
   ```

---

### Task 5.2: Build Analytics Dashboard (5h)

#### Phase 2a: Create AnalyticsDashboard Component

**File:** `web/oversight-hub/src/components/AnalyticsDashboard.jsx` (or enhance existing)

**Components to Build:**

1. **Header with Period Selector**
   - Buttons: Last 24 hours | Last 7 days | Last 30 days | Custom range
   - KPI cards showing current metrics

2. **Execution Time Trends Chart**
   - Line chart showing avg execution time over time
   - Breakdown by phase (stacked area chart)
   - Interactive legend to hide/show phases

3. **Cost Breakdown Pie Chart**
   - Pie chart: Cost by model/provider
   - Legend with percentages
   - Drill-down: Click to see details

4. **Error Rates Table**
   - Bar chart: Error rate by phase
   - Identify problematic phases
   - Show retry attempts

5. **Slowest Tasks Leaderboard**
   - Table of slowest executions
   - Columns: Task name, Total time, Phase breakdown, Status
   - Sort by duration or cost

6. **Queue Depth Over Time**
   - Line chart: Queue depth throughout the day
   - Identify peak hours
   - Suggest optimal execution times

#### Phase 2b: Add Analytics Routes

**File:** `src/cofounder_agent/routes/analytics_routes.py`

Complete implementation of 5 endpoints above, with:
- Database queries for metrics
- Time range filtering
- Data aggregation
- Proper error handling

#### Phase 2c: Add Navigation

**File:** `web/oversight-hub/src/components/LayoutWrapper.jsx`

Add "Analytics" link to sidebar navigation:
```
{ label: 'Analytics', icon: '📊', path: 'analytics' }
```

**File:** `web/oversight-hub/src/routes/AppRoutes.jsx`

Add route:
```jsx
<Route path="/analytics" element={<ProtectedRoute><LayoutWrapper><AnalyticsDashboard/></LayoutWrapper></ProtectedRoute>} />
```

---

### Task 5.3: Profile & Optimize (3h)

#### Phase 3a: Run Performance Profiling

**Steps:**
1. Create 10 sample tasks across different types
2. Monitor execution with instrumentation enabled
3. Collect baseline metrics
4. Identify slowest phases

**Expected Results:**
```
Average Execution Times (Baseline):
- Research phase: ~45s (API calls to search, retrieval)
- Draft phase: ~90s (LLM content generation)
- Assess phase: ~35s (Quality scoring)
- Refine phase: ~75s (LLM refinement)
- Finalize phase: ~20s (Formatting)
- Publish phase: ~20s (CMS integration)
- TOTAL AVERAGE: ~285 seconds (4.8 minutes)
```

#### Phase 3b: Identify Bottlenecks

**Likely candidates:**
1. **Draft/Refine Phases** (~165s combined)
   - LLM API calls are slow (30-60s per call)
   - Multiple sequential calls
   - **Opportunity:** Batch requests or use faster models (Mistral vs Claude)

2. **Research Phase** (~45s)
   - Pexels API search + web search
   - Multiple failed fallback attempts
   - **Opportunity:** Cache search results, reduce retries

3. **Image Selection** (~10s, part of finalize)
   - Pexels API call
   - **Opportunity:** Parallel with other phases, cache results

#### Phase 3c: Implement Optimizations

**Optimization 1: Parallel Phase Execution**
```python
# Current: Sequential
research -> draft -> assess -> refine -> finalize -> publish

# Proposed: Parallel-safe phases
research + assess (no dependencies) → draft → refine → finalize → publish
```

**Optimization 2: Model Selection**
```python
# Use cheaper/faster models for early phases
phases = {
    "research": "ollama-mistral",      # Fast, local (5-10s)
    "draft": "claude-3.5-sonnet",      # Quality (60-90s)
    "assess": "ollama-mistral",        # Fast (10-15s)
    "refine": "claude-3.5-sonnet",     # Quality (40-60s)
    "finalize": "ollama-mistral",      # Fast (5-10s)
    "publish": "N/A",                   # No LLM call
}
```

**Optimization 3: Response Caching**
```python
# Cache common research queries
# Example: "AI trends 2026" same across multiple tasks
# TTL: 1 hour
cache_key = f"research:{topic}:{keywords}"
if cache.get(cache_key):
    return cache.get(cache_key)
```

**Optimization 4: Early Termination**
```python
# If quality_score < threshold, fail early (don't refine)
if quality_score < MIN_THRESHOLD and not task.requires_refinement:
    return ExecutionResult(status="failed", reason="failed_quality_check")
```

---

## Implementation Timeline

### Day 1-2: Task 5.1 (4 hours)
- [ ] Define metrics data structures (1h)
- [ ] Instrument TaskExecutor with metrics collection (1.5h)
- [ ] Create analytics API endpoints (1.5h)
- [ ] Test metrics collection end-to-end (0.5h)

### Day 3-4: Task 5.2 (5 hours)
- [ ] Create AnalyticsDashboard component (2h)
- [ ] Build charts (execution time, costs, errors) (2h)
- [ ] Implement analytics API endpoints (0.5h)
- [ ] Add navigation & routing (0.5h)

### Day 5: Task 5.3 (3 hours)
- [ ] Run performance profiling (1h)
- [ ] Identify bottlenecks (0.5h)
- [ ] Implement optimizations (1h)
- [ ] Re-test and measure improvements (0.5h)

---

## Database Schema Updates

### New/Modified Tables

**Table: admin_logs (already exists, enhance)**
```sql
-- Add metrics columns if not present
ALTER TABLE admin_logs ADD COLUMN IF NOT EXISTS metric_type VARCHAR;
ALTER TABLE admin_logs ADD COLUMN IF NOT EXISTS metric_value FLOAT;
ALTER TABLE admin_logs ADD COLUMN IF NOT EXISTS metric_context JSONB;

-- Index for analytics queries
CREATE INDEX IF NOT EXISTS idx_admin_logs_metric_timestamp 
  ON admin_logs(created_at DESC) 
  WHERE metric_type IS NOT NULL;
```

**Sample data structure in metric_context:**
```json
{
  "task_id": "uuid",
  "phase": "research",
  "duration_ms": 45000,
  "tokens_in": 1250,
  "tokens_out": 3100,
  "cost_usd": 0.045,
  "model": "claude-3.5-sonnet",
  "provider": "anthropic"
}
```

---

## Success Criteria

### Task 5.1
- ✅ Metrics collected for all phases
- ✅ Token usage and costs tracked
- ✅ Error rates monitored
- ✅ Data persisted to database

### Task 5.2
- ✅ Analytics dashboard accessible at `/analytics`
- ✅ Execution time chart shows trends
- ✅ Cost breakdown visible
- ✅ Error rates by phase displayed
- ✅ Slowest tasks leaderboard working

### Task 5.3
- ✅ Baseline performance measured
- ✅ Bottlenecks identified
- ✅ At least 1 optimization implemented
- ✅ 10-20% improvement in execution time verified

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Metrics logging overhead slows tasks | Medium | Use async logging, batch writes |
| Database queries slow down dashboard | Low | Add indexes, aggregate pre-computed metrics |
| Parallel phases cause race conditions | High | Only enable for independent phases |
| Cost calculation accuracy | Medium | Verify against API receipts |

---

## Next Steps (Sprint 6+)

1. **Real-time Alerts** - Notify when execution time > threshold
2. **Cost Optimization** - Auto-switch to cheaper models
3. **Approval Notifications** - Email/Slack when tasks await review
4. **SLA Tracking** - Monitor approval turnaround times
5. **Capacity Planning** - Predict queue depth based on trends

---

**Last Updated:** 2026-02-19  
**Status:** READY TO BEGIN  
**Estimated Duration:** 12 hours across 3-5 days

