## Analytics & Profiling API Reference

**Last Updated:** February 20, 2026  
**API Version:** 3.0.1

---

## Base URL

```
http://localhost:8000
```

## Authentication

All endpoints support optional authentication via `Authorization: Bearer <token>` header.

Most endpoints work without authentication in development mode (see `.env.local` AUTH_BYPASS).

---

## Analytics Endpoints

### GET /api/analytics/kpis

Fetch key performance indicators for a time period.

**Query Parameters:**
- `range` (string): Time range - `7d`, `30d`, `90d`, `all` (default: `30d`)

**Response:**
```json
{
  "range": "30d",
  "kpis": [
    {
      "label": "Total Cost (Period)",
      "value": "$125.50",
      "change": "150 tasks",
      "positive": true
    },
    {
      "label": "Avg Cost/Task",
      "value": "$0.837",
      "change": "Optimization target",
      "positive": true
    },
    {
      "label": "Task Success Rate",
      "value": "94.6%",
      "change": "12.3% higher than last period",
      "positive": true
    },
    {
      "label": "Avg Execution Time",
      "value": "8.3s",
      "change": "0.5s faster",
      "positive": true
    }
  ]
}
```

**Example:**
```bash
curl http://localhost:8000/api/analytics/kpis?range=30d
```

---

### GET /api/analytics/tasks

Fetch task execution metrics and trends.

**Query Parameters:**
- `range` (string): Time range - `7d`, `30d`, `90d`, `all` (default: `30d`)

**Response:**
```json
{
  "range": "30d",
  "time_series": [
    {
      "date": "2026-02-01",
      "completed": 12,
      "failed": 1,
      "pending": 2
    },
    {
      "date": "2026-02-02",
      "completed": 15,
      "failed": 0,
      "pending": 1
    }
  ],
  "by_status": {
    "completed": 445,
    "failed": 25,
    "pending": 10,
    "awaiting_approval": 8
  },
  "by_category": {
    "blog_post": 180,
    "social_media": 220,
    "email": 65,
    "newsletter": 23
  },
  "total_tasks": 488
}
```

---

### GET /api/analytics/costs

Fetch cost breakdown and trends.

**Query Parameters:**
- `range` (string): Time range - `7d`, `30d`, `90d`, `all` (default: `30d`)

**Response:**
```json
{
  "range": "30d",
  "time_series": [
    {
      "date": "2026-02-01",
      "total_cost": 8.50,
      "llm_cost": 7.20,
      "api_cost": 1.30
    },
    {
      "date": "2026-02-02",
      "total_cost": 9.75,
      "llm_cost": 8.40,
      "api_cost": 1.35
    }
  ],
  "by_provider": {
    "openai": "45.80",
    "anthropic": "38.20",
    "google": "15.90",
    "ollama": "0.00"
  },
  "by_model": {
    "gpt-4": {
      "total": 35.40,
      "requests": 120,
      "avg_cost": 0.295
    },
    "claude-3-sonnet": {
      "total": 28.10,
      "requests": 95,
      "avg_cost": 0.296
    }
  },
  "total_cost_usd": 100.00
}
```

---

### GET /api/analytics/content

Fetch content publishing metrics.

**Query Parameters:**
- `range` (string): Time range - `7d`, `30d`, `90d`, `all` (default: `30d`)

**Response:**
```json
{
  "range": "30d",
  "published": 180,
  "pending_review": 12,
  "rejected": 3,
  "by_channel": {
    "blog": 80,
    "twitter": 65,
    "linkedin": 35
  },
  "engagement_metrics": {
    "avg_views": 450,
    "avg_engagement_rate": 4.2,
    "top_performing_post": {
      "title": "AI Trends 2026",
      "views": 2340,
      "engagement_rate": 8.5
    }
  }
}
```

---

### GET /api/analytics/quality

Fetch content quality metrics.

**Query Parameters:**
- `range` (string): Time range - `7d`, `30d`, `90d`, `all` (default: `30d`)

**Response:**
```json
{
  "range": "30d",
  "avg_quality_score": 82.5,
  "quality_distribution": {
    "excellent": 120,
    "good": 250,
    "fair": 100,
    "poor": 18
  },
  "by_dimension": {
    "clarity": 85.3,
    "readability": 83.1,
    "engagement": 79.4,
    "accuracy": 87.2,
    "originality": 81.9,
    "relevance": 84.6,
    "structure": 82.8
  },
  "approval_rate": 94.6
}
```

---

### GET /api/analytics/agents

Fetch agent performance metrics.

**Query Parameters:**
- `range` (string): Time range - `7d`, `30d`, `90d`, `all` (default: `30d`)

**Response:**
```json
{
  "range": "30d",
  "agents": [
    {
      "name": "content_agent",
      "tasks_completed": 180,
      "tasks_failed": 5,
      "success_rate": 97.3,
      "avg_execution_time_s": 8.5,
      "tokens_used": 450000,
      "cost_usd": 45.80
    },
    {
      "name": "financial_agent",
      "tasks_completed": 45,
      "tasks_failed": 1,
      "success_rate": 97.8,
      "avg_execution_time_s": 3.2,
      "tokens_used": 120000,
      "cost_usd": 12.50
    }
  ]
}
```

---

## Profiling Endpoints

### GET /api/profiling/slow-endpoints

Identify endpoints exceeding latency threshold.

**Query Parameters:**
- `threshold_ms` (integer): Latency threshold in milliseconds (default: `1000`, min: `0`)

**Response:**
```json
{
  "threshold_ms": 1000,
  "slow_endpoints": {
    "/api/tasks": {
      "avg_duration_ms": 1250.5,
      "count": 15,
      "max_duration_ms": 2100.0,
      "min_duration_ms": 900.0,
      "status_codes": [200, 201]
    },
    "/api/tasks/bulk": {
      "avg_duration_ms": 3450.2,
      "count": 3,
      "max_duration_ms": 4200.0,
      "min_duration_ms": 2800.0,
      "status_codes": [200]
    }
  },
  "count": 2
}
```

**Thresholds:**
- `< 100ms` - Excellent
- `100-500ms` - Good
- `500-1000ms` - Fair
- `> 1000ms` - Slow (action required)

---

### GET /api/profiling/endpoint-stats

Get comprehensive statistics for all endpoints.

**Query Parameters:** None

**Response:**
```json
{
  "timestamp": "2026-02-20T10:30:45Z",
  "endpoint_count": 24,
  "endpoints": {
    "/api/tasks": {
      "total_requests": 150,
      "avg_duration_ms": 850.5,
      "max_duration_ms": 2100.0,
      "min_duration_ms": 150.0,
      "p95_duration_ms": 1450.0,
      "p99_duration_ms": 1900.0,
      "error_count": 2,
      "success_rate": 98.67
    },
    "/api/tasks/{id}": {
      "total_requests": 300,
      "avg_duration_ms": 450.2,
      "max_duration_ms": 1200.0,
      "min_duration_ms": 50.0,
      "p95_duration_ms": 800.0,
      "p99_duration_ms": 1100.0,
      "error_count": 0,
      "success_rate": 100.0
    }
  }
}
```

**Percentile Explanation:**
- `p95_duration_ms`: 95% of requests completed within this time
- `p99_duration_ms`: 99% of requests completed within this time

---

### GET /api/profiling/recent-requests

Get recent request profiles for debugging.

**Query Parameters:**
- `limit` (integer): Number of recent requests to return (default: `100`, max: `1000`)

**Response:**
```json
{
  "limit": 100,
  "count": 45,
  "profiles": [
    {
      "endpoint": "/api/tasks",
      "method": "POST",
      "status_code": 201,
      "duration_ms": 1250.5,
      "timestamp": "2026-02-20T10:35:12.456Z",
      "is_slow": true
    },
    {
      "endpoint": "/api/tasks/550e8400",
      "method": "GET",
      "status_code": 200,
      "duration_ms": 125.0,
      "timestamp": "2026-02-20T10:34:55.123Z",
      "is_slow": false
    }
  ]
}
```

---

### GET /api/profiling/phase-breakdown

Get task execution breakdown by phase.

**Query Parameters:** None

**Response:**
```json
{
  "phase_breakdown": {
    "generation": {
      "avg_duration_ms": 5250.0,
      "endpoints": ["/api/tasks"],
      "total_requests": 150
    },
    "quality_assessment": {
      "avg_duration_ms": 1200.0,
      "endpoints": ["/api/tasks"],
      "total_requests": 150
    },
    "publishing": {
      "avg_duration_ms": 800.0,
      "endpoints": ["/api/tasks"],
      "total_requests": 150
    }
  },
  "slow_phases": {
    "generation": {
      "avg_duration_ms": 5250.0,
      "endpoints": ["/api/tasks"],
      "total_requests": 150
    }
  },
  "slow_phase_count": 1
}
```

---

### GET /api/profiling/health

Health check for profiling system.

**Query Parameters:** None

**Response:**
```json
{
  "status": "healthy",
  "profiles_tracked": 847,
  "slow_endpoints_detected": 3,
  "max_profiles": 1000
}
```

**Status Values:**
- `healthy` - System operational
- `not_initialized` - Middleware not loaded
- `degraded` - Memory limit approaching

---

## Common Query Patterns

### Get cost for last 7 days
```bash
curl "http://localhost:8000/api/analytics/costs?range=7d"
```

### Monitor endpoint performance
```bash
curl http://localhost:8000/api/profiling/endpoint-stats | jq '.endpoints | map(select(.avg_duration_ms > 500))'
```

### Find slow requests in real-time
```bash
curl "http://localhost:8000/api/profiling/recent-requests?limit=50" | jq '.profiles | map(select(.is_slow == true))'
```

### Get P99 latency for all endpoints
```bash
curl http://localhost:8000/api/profiling/endpoint-stats | jq '.endpoints | map({endpoint: .key, p99_ms: .value.p99_duration_ms})'
```

### Track quality trends
```bash
curl "http://localhost:8000/api/analytics/quality?range=30d" | jq '{score: .avg_quality_score, distribution: .quality_distribution}'
```

---

## Error Handling

All endpoints may return errors in the following format:

```json
{
  "detail": "Error description",
  "error_code": "ERROR_CODE",
  "timestamp": "2026-02-20T10:30:45Z"
}
```

**Common Status Codes:**
- `200` - Success
- `400` - Bad request (invalid parameters)
- `401` - Unauthorized
- `404` - Resource not found
- `503` - Service unavailable

---

## Rate Limiting

Analytics endpoints have soft rate limits:
- **Development:** Unlimited
- **Production:** 100 requests/minute per IP

Profiling endpoints are not rate-limited to ensure real-time monitoring.

---

## Data Retention

- **Profiling profiles:** 1000 most recent requests (in-memory)
- **Admin logs (metrics):** 90 days (database)
- **Analytics aggregates:** Full history (database)

---

## Integration Examples

### Python Client
```python
import requests

BASE_URL = "http://localhost:8000"

def get_analytics(range_days="30d"):
    """Get analytics for a period"""
    response = requests.get(
        f"{BASE_URL}/api/analytics/kpis",
        params={"range": range_days}
    )
    return response.json()

def get_slow_endpoints(threshold_ms=1000):
    """Get endpoints exceeding threshold"""
    response = requests.get(
        f"{BASE_URL}/api/profiling/slow-endpoints",
        params={"threshold_ms": threshold_ms}
    )
    return response.json()

# Usage
kpis = get_analytics("30d")
print(f"Total Cost: {kpis['kpis'][0]['value']}")

slow = get_slow_endpoints(1000)
print(f"Slow endpoints: {slow['count']}")
```

### JavaScript Client
```javascript
const BASE_URL = "http://localhost:8000";

async function getAnalytics(range = "30d") {
  const response = await fetch(
    `${BASE_URL}/api/analytics/kpis?range=${range}`
  );
  return response.json();
}

async function getProfileData() {
  const response = await fetch(
    `${BASE_URL}/api/profiling/endpoint-stats`
  );
  return response.json();
}

// Usage
const kpis = await getAnalytics("30d");
const profiles = await getProfileData();
console.log(`Total endpoints: ${profiles.endpoint_count}`);
```

---

## Support

For issues or questions:
1. Check `/api/profiling/health` endpoint
2. Review logs in `src/cofounder_agent/` output
3. Verify DATABASE_URL is set in `.env.local`
4. Check that middleware/routes are registered in main.py
