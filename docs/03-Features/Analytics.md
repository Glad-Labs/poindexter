# Analytics & Profiling: Developer Quick Start

**For:** Frontend developers, DevOps engineers
**Time to Setup:** 5 minutes
**Last Updated:** March 10, 2026

---

## What You're Getting

Sprint 5 adds three observability layers to Glad Labs:

1. **Real-Time Profiling** - Automatic latency tracking on every request
2. **Metrics Collection** - Task execution metrics (timing, costs, errors) stored in database
3. **Analytics Dashboard** - React component for visualizing everything

---

## Installation (5 min)

### Step 1: Install Frontend Dependencies

```bash
cd web/oversight-hub
npm install

# Verify recharts is installed
npm ls recharts
# Should show: recharts@^2.14.6 or higher
```

### Step 2: Restart Services

```bash
# From project root
npm run dev

# Or if already running, restart oversight-hub:
# In terminal where npm run dev:oversight is running
# Press Ctrl+C, then run again
```

### Step 3: Verify Backend is Running

```bash
# These should all return 200 OK
curl http://localhost:8000/health
curl http://localhost:8000/api/profiling/health
curl http://localhost:8000/api/analytics/kpis
```

---

## Using the Analytics Dashboard

### Navigate to Dashboard

1. Open http://localhost:3001 (Oversight Hub)
2. Click "Analytics" in the navigation menu
3. Or direct link: http://localhost:3001/analytics

### Dashboard Features

**KPI Tab** - Executive summary

- Total cost for period
- Average cost per task
- Task success rate
- Average execution time

**Tasks Tab** - Task trends and distribution

- Line chart: Tasks completed over time
- Status breakdown: Pie chart of completed/failed/pending
- Category breakdown: Which content types were created

**Costs Tab** - Cost analysis

- Daily cost trend
- Cost by provider (OpenAI, Claude, Google, Ollama)
- Cost by model (which LLM burned the most money?)

**Time Range Selector** - Bottom left

- Select 7d, 30d, 90d, or all-time
- Dashboard refreshes automatically every 60 seconds

### Interpreting the Data

```
Good health:
- Success rate > 95% ✅
- Cost per task < $0.50 ✅
- Tasks completing in < 10s ✅
- Mostly Ollama/Gemini usage ✅

Needs attention:
- Success rate 85-95% ⚠️
- Cost per task > $1.00 ⚠️
- P99 latency > 2s ⚠️
- Heavy GPT-4 usage ⚠️

Critical issues:
- Success rate < 85% ❌
- 10+ slow endpoints ❌
- Database errors in logs ❌
```

---

## Working with Profiling Data

### Real-Time Performance Monitoring

```bash
# See which endpoints are slow
curl http://localhost:8000/api/profiling/slow-endpoints

# Get percentile latencies
curl http://localhost:8000/api/profiling/endpoint-stats | jq '.endpoints[] | {endpoint, p95_ms: .p95_duration_ms, p99_ms: .p99_duration_ms}'

# Monitor recent requests
curl http://localhost:8000/api/profiling/recent-requests?limit=100 | jq '.profiles[] | select(.is_slow == true)'
```

### Phase Breakdown (Where Time is Spent)

```bash
# See how long each phase takes
curl http://localhost:8000/api/profiling/phase-breakdown | jq '.phase_breakdown[] | {phase: .key, avg_ms: .value.avg_duration_ms}'
```

Example output:

```json
{
  "generation": { "avg_ms": 5250 }, // Content generation (longest)
  "quality_assessment": { "avg_ms": 1200 }, // Quality check
  "publishing": { "avg_ms": 800 } // Final publishing
}
```

---

## Common Development Tasks

### Add New Analytics Metric

1. **Decide where metric is collected:**
   - From a request endpoint? → Use ProfilingMiddleware
   - From task execution? → Use TaskMetrics in task_executor.py
   - From database query? → Add to analytics_routes.py

2. **Collect the data:**

   ```python
   # In task_executor.py
   task_metrics.custom_field = value
   task_metrics.record_phase_end("your_phase")

   # Or in profiling_middleware.py
   profile = ProfileData(
       endpoint="/your/endpoint",
       duration_ms=elapsed_time,
       status_code=status_code,
   )
   self.profiles.append(profile)
   ```

3. **Expose via API:**

   ```python
   # In analytics_routes.py or profiling_routes.py
   @router.get("/api/analytics/your-metric")
   async def get_your_metric(range: str = "30d"):
       # Query database or middleware
       return {"metric": value}
   ```

4. **Add to dashboard:**
   ```jsx
   // In AnalyticsDashboard.jsx
   const data = await analyticsService.getYourMetric(timeRange);
   return <YourChart data={data} />;
   ```

### Debug Missing Profiling Data

```bash
# Is middleware running?
grep -n "ProfilingMiddleware" src/cofounder_agent/utils/middleware_config.py

# Are profiles being collected?
curl http://localhost:8000/api/profiling/health

# Try making a request
curl http://localhost:8000/api/tasks -X POST \
  -H "Content-Type: application/json" \
  -d '{"title": "Test"}'

# Check if it was profiled
curl http://localhost:8000/api/profiling/recent-requests
```

### Generate Test Data

```bash
# Run tests to generate profiling data
npm run test:python:smoke

# Or create dummy tasks via API
for i in {1..10}; do
  curl -X POST http://localhost:8000/api/tasks \
    -H "Content-Type: application/json" \
    -d "{\"title\": \"Test Task $i\", \"category\": \"blog_post\"}"
done
```

### Test Dashboard Locally

```bash
# Three ways to test locally:

# 1. With real backend (recommended)
npm run dev  # Starts all services

# 2. With mock data
# Edit AnalyticsDashboard.jsx to return mock data
const mockData = {
  kpis: [{ label: "Total Cost", value: "$100" }]
};

# 3. Check browser network tab
# Open DevTools (F12) → Network → Filter to XHR
# Make dashboard refresh (or wait 60s)
# Verify requests to /api/analytics/* succeed
```

---

## Integration with Existing Code

### PerformanceDashboard Integration

The PerformanceDashboard.jsx already receives performance data from:

```javascript
// In PerformanceDashboard.jsx
const { performanceData } = usePerformanceContext();
// Now includes profiling middleware metrics automatically
```

Charts use Recharts (same as AnalyticsDashboard):

- Bar charts for endpoint latencies
- Pie charts for model router decisions
- Line charts for trends

### TaskExecutor Integration

TaskMetrics are automatically recorded:

```python
# In task_executor.py
task_metrics = TaskMetrics()
# Phase 1: Generation
async with task_executor.execute():
    task_metrics.record_phase_end("generation")  # Auto-recorded
# Phase 2: Quality
async with quality_service.evaluate():
    task_metrics.record_phase_end("quality_assessment")  # Auto-recorded
# Phase 3: Publishing
await publish_service.publish():
    task_metrics.record_phase_end("publishing")  # Auto-recorded
```

---

## Performance Impact

**Profiling Overhead:** < 1ms per request

```
Request latency: 450ms
Profiling adds: ~0.5ms (~0.1%)
Total: 450.5ms (negligible)
```

**Database Impact:** Minimal

```
Metrics recorded: 1 JSON entry per task per phase (3-4 per task)
Storage: ~500 bytes per task
90-day retention: ~50MB for 100 tasks/day
```

**Memory Usage:**

```
Profiling stores last 1000 requests in memory
Profile size: ~200 bytes each
Max memory: ~200KB
```

---

## Troubleshooting

### "Analytics Dashboard not loading"

```bash
# 1. Check backend is running
curl http://localhost:8000/health

# 2. Check specific endpoint
curl http://localhost:8000/api/analytics/kpis

# 3. Check for CORS errors
# Open DevTools (F12) → Console → Look for "CORS" error

# 4. Check database has data
psql $DATABASE_URL -c "SELECT COUNT(*) FROM admin_logs LIMIT 1;"
```

### "Charts show 'undefined' or blank"

```bash
# 1. Is recharts installed?
npm ls recharts --workspace=oversight-hub

# 2. Is data coming from API?
curl http://localhost:8000/api/analytics/kpis | jq .

# 3. Check browser console for JavaScript errors
# Open DevTools (F12) → Console tab
```

### "Profiling endpoints return empty"

```bash
# 1. Make a test request
curl http://localhost:8000/api/tasks -X POST \
  -H "Content-Type: application/json" \
  -d '{"title": "Test"}'

# 2. Check if it was recorded
curl http://localhost:8000/api/profiling/recent-requests

# 3. Verify middleware is active
curl http://localhost:8000/api/profiling/health
```

---

## Next Steps

1. **Navigate to dashboard:** http://localhost:3001/analytics
2. **Generate some test data:** Run tests or create tasks manually
3. **Explore the data:** Use tab selector and time range
4. **Check performance:** In PerformanceDashboard.jsx
5. **Monitor in production:** See Monitoring & Diagnostics guide

---

## File Structure

```
src/cofounder_agent/
├── middleware/
│   └── profiling_middleware.py       # ASGI profiling (NEW)
├── routes/
│   ├── analytics_routes.py           # Analytics endpoints (EXISTING)
│   └── profiling_routes.py           # Profiling endpoints (NEW)
├── services/
│   └── task_executor.py              # TaskMetrics integration (MODIFIED)
└── utils/
    ├── middleware_config.py          # Middleware setup (MODIFIED)
    └── route_registration.py         # Route setup (MODIFIED)

web/oversight-hub/
├── src/
│   ├── pages/
│   │   ├── AnalyticsDashboard.jsx   # New dashboard (NEW)
│   │   └── PerformanceDashboard.jsx # Enhanced w/ charts (MODIFIED)
│   ├── routes/
│   │   └── AnalyticsDashboard.css   # Dashboard styling (NEW)
│   └── services/
│       └── analyticsService.js      # API wrapper (NEW)
└── package.json                      # recharts added (MODIFIED)
```

---

## Support Resources

- **Metrics Collection Details:** See [CAPABILITY_BASED_TASK_SYSTEM.md](../CAPABILITY_BASED_TASK_SYSTEM.md)
- **Production Monitoring:** See [MONITORING_AND_DIAGNOSTICS.md](../MONITORING_AND_DIAGNOSTICS.md)
- **Architecture:** See [02-ARCHITECTURE_AND_DESIGN.md](../02-ARCHITECTURE_AND_DESIGN.md)

---

## Quick Reference: API Endpoints

| Endpoint                         | Purpose            | Query Params   |
| -------------------------------- | ------------------ | -------------- |
| `/api/profiling/health`          | System status      | None           |
| `/api/profiling/slow-endpoints`  | Find slow routes   | `threshold_ms` |
| `/api/profiling/endpoint-stats`  | Latency stats      | None           |
| `/api/profiling/recent-requests` | Last N requests    | `limit`        |
| `/api/profiling/phase-breakdown` | Task phase timing  | None           |
| `/api/analytics/kpis`            | Executive metrics  | `range`        |
| `/api/analytics/tasks`           | Task trends        | `range`        |
| `/api/analytics/costs`           | Cost breakdown     | `range`        |
| `/api/analytics/content`         | Publishing metrics | `range`        |
| `/api/analytics/quality`         | Quality scores     | `range`        |
| `/api/analytics/agents`          | Agent performance  | `range`        |

All analytics endpoints accept `range=7d|30d|90d|all`

---

**Questions?** Check the troubleshooting guide above or review the full documentation links.
