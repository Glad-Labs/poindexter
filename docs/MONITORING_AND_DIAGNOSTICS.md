# Analytics & Profiling: Monitoring & Diagnostics Guide

**Last Updated:** February 20, 2026  
**For:** DevOps, Platform Engineers, Development Team

---

## Quick Health Check

Run this command to verify the entire monitoring stack is operational:

```bash
# Check all three services
curl http://localhost:8000/health                    # Backend health
curl http://localhost:8000/api/profiling/health      # Profiling system
curl http://localhost:3001                           # Oversight Hub frontend
curl http://localhost:3000                           # Public site frontend
```

Expected responses:

- Backend: `{"status": "ok", ...}`
- Profiling: `{"status": "healthy", "profiles_tracked": N, ...}`

---

## Monitoring Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   Glad Labs Monitoring Stack                │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  COLLECTION LAYER                                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  ProfilingMiddleware (ASGI)                          │   │
│  │  - Intercepts all HTTP requests                      │   │
│  │  - Measures latency (wall-clock time)                │   │
│  │  - Detects slow endpoints (threshold: 1000ms)        │   │
│  │  - Stores last 1000 profiles in memory               │   │
│  │  - Tags: is_slow, endpoint, method, status_code      │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  METRICS LAYER                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  TaskMetrics Service (In TaskExecutor)               │   │
│  │  - Records Phase 1: Content generation               │   │
│  │  - Records Phase 2: Quality assessment + refinement  │   │
│  │  - Records Phase 3: Publishing                       │   │
│  │  - Tracks: LLM calls, errors, costs, tokens used     │   │
│  │  - Persists to admin_logs table (JSONB)              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  AGGREGATION LAYER                                           │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Analytics Routes (/api/analytics/*)                 │   │
│  │  - Query admin_logs table for time series data       │   │
│  │  - Calculate KPIs (cost, success rate, speed)        │   │
│  │  - Group by provider, model, category                │   │
│  │  - Support multiple time ranges (7d, 30d, 90d, all)  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  PROFILING LAYER                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Profiling Routes (/api/profiling/*)                 │   │
│  │  - Access in-memory profiles from middleware         │   │
│  │  - Calculate percentiles (P95, P99)                  │   │
│  │  - Identify slow endpoints                           │   │
│  │  - Real-time: No database queries                    │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  VISUALIZATION LAYER                                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  React Dashboard (AnalyticsDashboard.jsx)            │   │
│  │  - KPI Tab: Executive summary, trends                │   │
│  │  - Tasks Tab: Volume, distribution, trends           │   │
│  │  - Costs Tab: Breakdown by provider/model            │   │
│  │  - Refreshes every 60 seconds                        │   │
│  │                                                       │   │
│  │  Performance Dashboard (PerformanceDashboard.jsx)    │   │
│  │  - Recharts visualizations                           │   │
│  │  - Latency profiles, model router decisions          │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Troubleshooting Guide

### 1. Profiling Middleware Not Capturing Requests

**Symptoms:** Profiling endpoints return empty data

**Diagnosis:**

```bash
# Check if middleware is initialized
curl http://localhost:8000/api/profiling/health

# Check if creating tasks triggers profiling
tail -f server.log | grep "ProfileData\|is_slow"

# Verify middleware is in correct order in main.py
grep -n "add_middleware\|ProfilingMiddleware" src/cofounder_agent/main.py
```

**Common Causes:**

- Middleware not registered in `middleware_config.py`
- Middleware registered AFTER a route (must be BEFORE)
- Health checks filtered out (they are, by design)

**Fix:**

```python
# In src/cofounder_agent/utils/middleware_config.py
# Profiling middleware should be near top (after CORS, before RequestLogging)
def setup_middleware(app):
    app.add_middleware(ProfilingMiddleware)  # Add this FIRST
    app.add_middleware(CORSMiddleware, ...)
    app.add_middleware(RequestLogging, ...)
```

---

### 2. Analytics Dashboard Shows No Data

**Symptoms:** AnalyticsDashboard.jsx shows empty charts or "Loading..." forever

**Diagnosis:**

```bash
# Check if analytics endpoints work
curl http://localhost:8000/api/analytics/kpis?range=30d
curl http://localhost:8000/api/analytics/tasks?range=30d

# Check browser console for errors
# Press F12 → Console tab → Look for 403/404/500 errors

# Check if admin_logs table exists
psql $DATABASE_URL -c "\dt admin_logs"
```

**Common Causes:**

- Database empty (no tasks executed yet)
- CORS misconfiguration
- Database query timestamp filtering wrong
- PostgreSQL connection failed

**Fix:**

```bash
# Generate test data
npm run test:python:smoke

# Verify database has records
psql $DATABASE_URL -c "SELECT COUNT(*) FROM admin_logs LIMIT 5;"

# Check CORS headers in response
curl -i http://localhost:8000/api/analytics/kpis | grep -i "access-control"
```

---

### 3. Performance Dashboard Charts Not Rendering

**Symptoms:** Charts display as broken or "undefined" on PerformanceDashboard.jsx

**Diagnosis:**

```bash
# Verify Recharts is installed
npm ls recharts --workspace=oversight-hub

# Check if performance endpoint returns data
curl http://localhost:8000/api/metrics/performance | jq .
```

**Common Causes:**

- Recharts not installed (`npm install` not run after package.json update)
- API endpoint returns wrong data format
- Browser version doesn't support Recharts

**Fix:**

```bash
# Install dependencies
cd web/oversight-hub
npm install

# Verify recharts version
npm ls recharts

# Clear browser cache
# Ctrl+Shift+Delete → Clear cache → Reload page
```

---

### 4. Slow Request Not Detected

**Symptoms:** Request took 2000ms but profiling reports it's not slow

**Diagnosis:**

```bash
# Check slow endpoint threshold
curl http://localhost:8000/api/profiling/slow-endpoints?threshold_ms=1000 | jq '.threshold_ms'

# Get recent requests and check is_slow flag
curl http://localhost:8000/api/profiling/recent-requests | jq '.profiles[] | select(.duration_ms > 1000)'
```

**Common Causes:**

- Threshold too high (default 1000ms)
- Request completed before profiling report generated
- Profiling overhead affected timing

**Fix:**

```bash
# Lower threshold to detect more slow requests
curl "http://localhost:8000/api/profiling/slow-endpoints?threshold_ms=500"

# Monitor with custom threshold
watch -n 1 'curl -s http://localhost:8000/api/profiling/slow-endpoints?threshold_ms=100 | jq ".count"'
```

---

### 5. High CPU Usage from Profiling

**Symptoms:** Server CPU usage spikes, profiling adds latency

**Diagnosis:**

```bash
# Check profiling overhead
curl http://localhost:8000/api/profiling/endpoint-stats | jq '.endpoints[] | {endpoint: .endpoint, avg_ms: .avg_duration_ms}' | head -10

# Monitor profile count (should stay under 1000)
while true; do
  COUNT=$(curl -s http://localhost:8000/api/profiling/health | jq .profiles_tracked)
  echo "Profiles: $COUNT"
  sleep 5
done
```

**Common Causes:**

- Too many requests being profiled
- Profile storage unlimited (should be 1000 max)
- Profiling percentile calculation inefficient

**Fix:**

```python
# In profiling_middleware.py, limit profile storage
class ProfilingMiddleware:
    MAX_PROFILES = 1000  # Limit stored profiles

    def store_profile(self, profile):
        if len(self.profiles) >= self.MAX_PROFILES:
            self.profiles.pop(0)  # Remove oldest
        self.profiles.append(profile)
```

---

### 6. Inconsistent Cost Reporting

**Symptoms:** Analytics shows different costs than admin dashboard

**Diagnosis:**

```bash
# Check what's being recorded
psql $DATABASE_URL -c "
SELECT COUNT(*),
       ROUND(SUM((data->>'total_cost')::numeric), 2) as total_cost
FROM admin_logs
WHERE data ? 'total_cost'
ORDER BY created_at DESC LIMIT 10;"

# Check which provider is being used per task
curl http://localhost:8000/api/analytics/costs?range=7d | jq '.by_provider'
```

**Common Causes:**

- Multiple LLM providers active (costs scattered across providers)
- TaskMetrics not recording costs at phase end
- Query time range mismatch

**Fix:**

```python
# In task_executor.py, ensure cost is recorded per phase
task_metrics.cost_usd = result.get("cost_usd", 0.0)
task_metrics.llm_provider = result.get("provider", "unknown")
task_metrics.record_phase_end("generation")
```

---

## Monitoring Best Practices

### 1. Set Up Real-Time Dashboards

```bash
# Monitor slow endpoints continuously
watch -n 5 'curl -s http://localhost:8000/api/profiling/slow-endpoints?threshold_ms=500 | jq ".slow_endpoints | keys"'

# Monitor cost in real-time
watch -n 10 'curl -s "http://localhost:8000/api/analytics/costs?range=7d" | jq ".total_cost_usd"'

# Monitor task success rate
watch -n 10 'curl -s "http://localhost:8000/api/analytics/tasks?range=7d" | jq ".by_status"'
```

### 2. Alert on Anomalies

```python
# alerts.py - Check for anomalies
import requests
import json

def check_profiling_health():
    health = requests.get("http://localhost:8000/api/profiling/health").json()

    if health["slow_endpoints_detected"] > 5:
        print("⚠️  WARNING: Multiple slow endpoints detected")
        return False

    if health["profiles_tracked"] > 900:
        print("⚠️  WARNING: Profile memory near capacity")
        return False

    return True

def check_cost_spike():
    costs = requests.get(
        "http://localhost:8000/api/analytics/costs?range=7d"
    ).json()

    daily_avg = costs["total_cost_usd"] / 7
    today_cost = costs["time_series"][-1]["total_cost"]

    if today_cost > daily_avg * 1.5:
        print(f"⚠️  WARNING: Cost spike detected (${today_cost} vs ${daily_avg:.2f} avg)")
        return False

    return True

# Run checks
if not check_profiling_health():
    # Send alert to Sentry/PagerDuty
    pass
```

### 3. Regular Cleanup

```bash
# Clear old profiling data (runs when system restarts)
# Profiling uses in-memory storage, so restart clears it
systemctl restart cofounder_agent

# Archive analytics to long-term storage (optional)
psql $DATABASE_URL -c "
COPY (
  SELECT * FROM admin_logs
  WHERE created_at < NOW() - INTERVAL '90 days'
) TO PROGRAM 'gzip > /backups/analytics_$(date +%Y%m%d).json.gz';"
```

---

## Key Metrics to Monitor

| Metric                   | Threshold         | Action                        |
| ------------------------ | ----------------- | ----------------------------- |
| Slow endpoints (1000ms+) | < 3               | None                          |
| Slow endpoints           | 3-10              | Review and optimize           |
| Slow endpoints           | > 10              | Page on-call engineer         |
| Avg cost/task            | < $0.50           | None                          |
| Avg cost/task            | $0.50-$1.00       | Review model selection        |
| Avg cost/task            | > $1.00           | Escalate to product           |
| Task success rate        | > 95%             | None                          |
| Task success rate        | 85-95%            | Investigate failures          |
| Task success rate        | < 85%             | Page on-call engineer         |
| P99 latency              | < 2000ms          | None                          |
| P99 latency              | 2-5 seconds       | Optimize slow endpoints       |
| P99 latency              | > 5 seconds       | Incident response             |
| Profiling overhead       | < 2ms per request | None                          |
| Profiling overhead       | > 5ms per request | Disable profiling temporarily |

---

## Performance Baseline

These are typical metrics for healthy operation:

```json
{
  "profiling": {
    "avg_endpoint_latency_ms": 450,
    "p95_latency_ms": 1200,
    "p99_latency_ms": 1800,
    "slow_endpoints": 2,
    "profiles_tracked": 450
  },
  "analytics": {
    "tasks_per_day": 150,
    "success_rate": 96.5,
    "avg_cost_per_task": 0.67,
    "avg_quality_score": 83.2
  },
  "resource_usage": {
    "profiling_memory_mb": 8,
    "profiling_cpu_percent": 0.5,
    "database_connections": 10,
    "response_time_ms": 25
  }
}
```

---

## Debugging Common Scenarios

### Scenario: Website is slow after deployment

```bash
# Step 1: Check slow endpoints
curl http://localhost:8000/api/profiling/slow-endpoints | jq '.slow_endpoints | keys'

# Step 2: Check specific endpoint stats
curl http://localhost:8000/api/profiling/endpoint-stats | jq '.endpoints[] | select(.endpoint == "/api/tasks") | {avg: .avg_duration_ms, p95: .p95_duration_ms}'

# Step 3: Check for errors
curl http://localhost:8000/api/profiling/recent-requests?limit=50 | jq '.profiles[] | select(.status_code >= 400)'

# Step 4: Check database performance
psql $DATABASE_URL -c "
EXPLAIN ANALYZE
SELECT * FROM admin_logs WHERE created_at > NOW() - INTERVAL '1 hour';"
```

### Scenario: Cost unexpectedly increased

```bash
# Step 1: Check daily trend
curl http://localhost:8000/api/analytics/costs?range=30d | jq '.time_series[-7:] | map({date, total: .total_cost})'

# Step 2: Check which provider
curl http://localhost:8000/api/analytics/costs?range=7d | jq '.by_provider | to_entries | sort_by(.value) | reverse'

# Step 3: Check which model
curl http://localhost:8000/api/analytics/costs?range=7d | jq '.by_model | to_entries | sort_by(.value.total) | reverse | .[0:3]'

# Step 4: Did usage increase?
curl http://localhost:8000/api/analytics/tasks?range=7d | jq '.total_tasks'
```

### Scenario: Tasks are failing but dashboard shows success rate as high

```bash
# Step 1: Check recent task failures
curl http://localhost:8000/api/analytics/tasks?range=7d | jq '.by_status'

# Step 2: View actual failures in logs
tail -f server.log | grep -i "error\|failed\|exception" | head -20

# Step 3: Check if errors are being recorded
psql $DATABASE_URL -c "
SELECT COUNT(*), MAX(data->>'error_message') as latest_error
FROM admin_logs
WHERE data @> '{\"phase\": \"quality_assessment\"}'
AND data ? 'error_message'
ORDER BY created_at DESC LIMIT 10;"

# Step 4: Manually execute one task to debug
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Test", "category": "blog_post"}'
```

---

## When to Escalate

**Page on-call engineer if:**

- ❌ Task success rate drops below 85%
- ❌ More than 10 slow endpoints detected
- ❌ P99 latency exceeds 5 seconds
- ❌ Profiling system shows "degraded" status
- ❌ Database connection pool exhausted
- ❌ Cost per task exceeds $2.00

**Schedule optimization if:**

- ⚠️ 3-10 slow endpoints detected
- ⚠️ P99 latency between 2-5 seconds
- ⚠️ Cost per task is $0.50-$1.00
- ⚠️ Success rate between 85-95%

**No action needed:**

- ✅ Slow endpoints < 3
- ✅ P99 latency < 2 seconds
- ✅ Cost per task < $0.50
- ✅ Success rate > 95%

---

## Additional Resources

- [Analytics Quick Start](ANALYTICS_QUICK_START.md)
- [Capability-Based Task System](CAPABILITY_BASED_TASK_SYSTEM.md)
- [Architecture & Design](02-ARCHITECTURE_AND_DESIGN.md)
- [Operations & Maintenance](06-OPERATIONS_AND_MAINTENANCE.md)
