# Implementation Roadmap: Phase 1 Optimizations

**Target Timeline:** 1-2 weeks  
**Estimated Effort:** 12-16 hours  
**Expected Impact:** 35-40% performance improvement

---

## Task 1: React Route-Based Code Splitting

### 1.1 Update AppRoutes.jsx to Use Lazy Loading

```jsx
// web/oversight-hub/src/routes/AppRoutes.jsx
import React, { Suspense, lazy } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';

// Lazy-load route components
const ExecutiveDashboard = lazy(() => import('../components/pages/ExecutiveDashboard'));
const TaskManagement = lazy(() => import('./TaskManagement'));
const Content = lazy(() => import('./Content'));
const AIStudio = lazy(() => import('./AIStudio'));
const Settings = lazy(() => import('./Settings'));
const CostMetricsDashboard = lazy(() => import('./CostMetricsDashboard'));
const PerformanceDashboard = lazy(() => import('./PerformanceDashboard'));
const UnifiedServicesPanel = lazy(() => import('../components/pages/UnifiedServicesPanel'));

// Loading fallback component
const LoadingFallback = () => (
  <div style={{ padding: '20px', textAlign: 'center', color: '#666' }}>
    Loading...
  </div>
);

function AppRoutes() {
  return (
    <Routes>
      {/* Public Routes */}
      <Route path="/login" element={<Login />} />
      <Route path="/auth/callback" element={<AuthCallback />} />

      {/* Protected Routes with Suspense */}
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <Suspense fallback={<LoadingFallback />}>
                <ExecutiveDashboard />
              </Suspense>
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      
      {/* Repeat pattern for all other routes */}
      <Route
        path="/tasks"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <Suspense fallback={<LoadingFallback />}>
                <TaskManagement />
              </Suspense>
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      
      {/* Additional routes... */}
      
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default AppRoutes;
```

**Verification:**

- Run `npm run build` and check output for chunk files
- Expected: Multiple `.chunk.js` files instead of single bundle.js
- Expected reduction: 40-50% initial load

---

## Task 2: FastAPI Structured Logging Middleware

### 2.1 Create Logging Middleware

```python
# src/cofounder_agent/middleware/logging_middleware.py
"""
Structured request/response logging middleware for performance tracking
"""

import time
import logging
from typing import Callable
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from datetime import datetime

logger = logging.getLogger(__name__)

class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs all requests/responses with structured data for observability
    """
    
    # Endpoints to skip logging (reduce noise)
    SKIP_PATHS = {
        '/api/health',
        '/api/docs',
        '/api/redoc',
        '/api/openapi.json',
    }
    
    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        # Skip noisy endpoints
        if any(request.url.path.startswith(path) for path in self.SKIP_PATHS):
            return await call_next(request)
        
        # Record start time
        start_time = time.time()
        request_id = str(int(start_time * 1000000))  # Microsecond timestamp
        
        # Log request
        logger.info(
            f"[REQ-{request_id}] {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "query_params": dict(request.query_params),
                "client_ip": request.client.host if request.client else "unknown",
                "user_agent": request.headers.get("user-agent", "unknown"),
            }
        )
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            
            # Log response
            logger.info(
                f"[RESP-{request_id}] {request.method} {request.url.path} - {response.status_code}",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": int(duration_ms),
                    "cached": response.headers.get("X-Cache-Hit") == "true",
                }
            )
            
            # Add duration header for debugging
            response.headers["X-Response-Time"] = f"{duration_ms:.1f}ms"
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"[ERROR-{request_id}] {request.method} {request.url.path}",
                exc_info=True,
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": int(duration_ms),
                    "error_type": type(e).__name__,
                }
            )
            raise
```

### 2.2 Register Middleware in main.py

```python
# src/cofounder_agent/main.py - Add this import at top
from middleware.logging_middleware import StructuredLoggingMiddleware

# In the app creation section (around line 195):
# ===== MIDDLEWARE SETUP =====
logger.info("Registering middleware...")

# Register logging middleware FIRST (executes last, logs everything)
app.add_middleware(StructuredLoggingMiddleware)

# Then register other middleware
middleware_config = MiddlewareConfig()
middleware_config.register_all_middleware(app)

logger.info("✅ Middleware registered")
```

**Verification:**

- Restart backend: `npm run dev:cofounder`
- Make an API request: `curl http://localhost:8000/api/models/available`
- Check logs: Should see structured log entries with request_id, duration_ms, status_code
- Example log: `[REQ-1707946200000] GET /api/models/available`

---

## Task 3: HTTP Cache Control Headers

### 3.1 Create Cache Control Middleware

```python
# src/cofounder_agent/middleware/cache_control_middleware.py
"""
Add Cache-Control headers to responses for HTTP caching
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from typing import Callable

class CacheControlMiddleware(BaseHTTPMiddleware):
    """
    Sets appropriate Cache-Control headers based on endpoint
    """
    
    # Define cache policies per endpoint
    CACHE_POLICIES = {
        # Public, cacheable for 5 min (browser) + 1 hour (CDN)
        "/api/models": "public, max-age=300, s-maxage=3600",
        "/api/models/available": "public, max-age=300, s-maxage=3600",
        "/api/agents": "public, max-age=300, s-maxage=3600",
        "/api/agents/list": "public, max-age=300, s-maxage=3600",
        
        # Performance metrics - refresh every 60s
        "/api/metrics/performance": "public, max-age=60, s-maxage=300",
        
        # Tasks and content - private, no cache (always fresh)
        "/api/tasks": "private, max-age=0, must-revalidate",
        "/api/content": "private, max-age=0, must-revalidate",
        
        # Default: don't cache authenticated endpoints
        "/api/": "private, max-age=0, must-revalidate",
    }
    
    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        response = await call_next(request)
        
        # Find matching cache policy
        cache_header = None
        for path, policy in self.CACHE_POLICIES.items():
            if request.url.path.startswith(path):
                cache_header = policy
                break
        
        # Apply cache header if matched
        if cache_header:
            response.headers["Cache-Control"] = cache_header
        
        return response
```

### 3.2 Register in main.py

```python
# src/cofounder_agent/main.py
from middleware.cache_control_middleware import CacheControlMiddleware

# Add this middleware AFTER StructuredLoggingMiddleware
app.add_middleware(StructuredLoggingMiddleware)
app.add_middleware(CacheControlMiddleware)
```

**Verification:**

```bash
# Check cache headers are present
curl -i http://localhost:8000/api/models/available | grep -A 5 "Cache-Control"
# Expected: "Cache-Control: public, max-age=300, s-maxage=3600"

curl -i http://localhost:8000/api/tasks | grep -A 5 "Cache-Control"
# Expected: "Cache-Control: private, max-age=0, must-revalidate"
```

---

## Task 4: Enable Response Compression

### 4.1 Add GZip Middleware

```python
# src/cofounder_agent/main.py - Add this import
from fastapi.middleware.gzip import GZIPMiddleware

# Add this after other middleware (should be one of the first to execute)
app.add_middleware(GZIPMiddleware, minimum_size=1000)
```

**Verification:**

```bash
# Response should be gzipped
curl -H "Accept-Encoding: gzip" -i http://localhost:8000/api/models/available | head -20
# Expected: "Content-Encoding: gzip" header
# Expected: Much smaller response size
```

---

## Task 5: Add Slow Query Detection

### 5.1 Create Query Monitoring Decorator

```python
# src/cofounder_agent/utils/query_monitor.py
"""
Database query performance monitoring
"""

import time
import logging
from functools import wraps
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger(__name__)

def log_query_performance(threshold_ms: int = 100):
    """
    Decorator to log slow database queries
    
    Args:
        threshold_ms: Log as warning if query exceeds this duration
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            func_name = func.__name__
            
            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                
                # Log slow queries
                if duration_ms > threshold_ms:
                    logger.warning(
                        f"[SLOW QUERY] {func_name} took {duration_ms:.1f}ms (threshold: {threshold_ms}ms)",
                        extra={
                            "function": func_name,
                            "duration_ms": int(duration_ms),
                            "threshold_ms": threshold_ms,
                        }
                    )
                
                return result
            
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                logger.error(
                    f"[QUERY ERROR] {func_name} failed after {duration_ms:.1f}ms",
                    exc_info=True,
                    extra={
                        "function": func_name,
                        "duration_ms": int(duration_ms),
                        "error": str(e),
                    }
                )
                raise
        
        return async_wrapper
    
    return decorator
```

### 5.2 Apply to Key Database Methods

```python
# src/cofounder_agent/services/database_service.py
from utils.query_monitor import log_query_performance

class DatabaseService:
    @log_query_performance(threshold_ms=50)
    async def list_tasks(self, limit: int = 10, offset: int = 0):
        # existing implementation
        pass
    
    @log_query_performance(threshold_ms=100)
    async def get_task_by_id(self, task_id: str):
        # existing implementation
        pass
```

**Verification:**

- Make API requests and watch logs for slow query warnings
- Expected: Any query >100ms (or configured threshold) will be logged
- Use to identify optimization candidates

---

## Task 6: Create Observability Dashboard Route

### 6.1 Create Diagnostics Routes

```python
# src/cofounder_agent/routes/diagnostics_routes.py
"""
Observability and diagnostics endpoints
"""

import logging
from datetime import datetime
from fastapi import APIRouter, Request
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

@router.get("/performance-summary")
async def get_performance_summary(request: Request) -> Dict[str, Any]:
    """
    Return real-time performance summary from current service state
    """
    try:
        redis_cache = request.app.state.redis_cache
        
        # Get Redis stats
        cache_info = {}
        try:
            if redis_cache and hasattr(redis_cache, 'info'):
                cache_info = redis_cache.info('stats')
        except Exception as e:
            logger.warning(f"Could not get Redis stats: {e}")
        
        # Calculate cache hit rate
        hits = cache_info.get('keyspace_hits', 0)
        misses = cache_info.get('keyspace_misses', 0)
        total = hits + misses
        hit_rate = (hits / total * 100) if total > 0 else 0
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "status": "healthy",
            "cache": {
                "provider": "redis",
                "hits": hits,
                "misses": misses,
                "total_requests": total,
                "hit_rate_percent": round(hit_rate, 2),
                "memory_mb": cache_info.get('used_memory', 0) // (1024 * 1024),
            },
            "services": {
                "database": {
                    "status": "connected" if request.app.state.database else "disconnected",
                },
                "cache": {
                    "status": "connected" if redis_cache else "disconnected",
                },
            },
        }
    
    except Exception as e:
        logger.error(f"Error in performance summary: {e}", exc_info=True)
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "status": "error",
            "error": str(e),
        }

@router.get("/health-detailed")
async def get_health_detailed(request: Request) -> Dict[str, Any]:
    """
    Detailed health check with component status
    """
    app = request.app
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "version": app.version,
        "components": {
            "database": {
                "status": "healthy" if app.state.database else "unhealthy",
            },
            "cache": {
                "status": "healthy" if app.state.redis_cache else "unhealthy",
            },
            "startup": {
                "complete": app.state.startup_complete if hasattr(app.state, 'startup_complete') else False,
                "error": app.state.startup_error if hasattr(app.state, 'startup_error') else None,
            },
        },
    }
```

### 6.2 Register Routes in route_registration.py

```python
# src/cofounder_agent/utils/route_registration.py
def register_all_routes(app, **kwargs):
    status = {}
    
    # ... existing route registrations ...
    
    try:
        from routes.diagnostics_routes import router as diagnostics_router
        app.include_router(diagnostics_router)
        logger.info("diagnostics_router registered")
        status["diagnostics_router"] = True
    except Exception as e:
        logger.error(f"diagnostics_router failed: {e}")
        status["diagnostics_router"] = False
    
    return status
```

**Verification:**

```bash
curl http://localhost:8000/api/diagnostics/performance-summary
curl http://localhost:8000/api/diagnostics/health-detailed
```

---

## Verification Checklist

After implementing all Phase 1 tasks:

- [ ] **Code Splitting:** Build produces chunk files, not single bundle

  ```bash
  npm run build
  # Check: Multiple .chunk.js files in build/static/js/
  ```

- [ ] **Logging Middleware:** Logs show structured request/response data

  ```bash
  npm run dev:cofounder 2>&1 | grep "REQ-\|RESP-"
  # Should see request IDs with duration_ms
  ```

- [ ] **Cache Headers:** Endpoints return Cache-Control headers

  ```bash
  curl -i http://localhost:8000/api/models/available | grep Cache-Control
  ```

- [ ] **Compression:** Responses are gzipped

  ```bash
  curl -H "Accept-Encoding: gzip" -I http://localhost:8000/api/models/available | grep Content-Encoding
  ```

- [ ] **Slow Query Detection:** Slow queries logged to console

  ```bash
  # Make slow database call, check logs for [SLOW QUERY]
  ```

- [ ] **Diagnostics Endpoints:** Health check endpoints work

  ```bash
  curl http://localhost:8000/api/diagnostics/performance-summary
  curl http://localhost:8000/api/diagnostics/health-detailed
  ```

---

## Performance Impact Measurement

### Before Phase 1

```
Initial bundle size: ~250KB gzipped
First Contentful Paint (FCP): ~2.5s
API p99 latency: ~500ms
Cache hit rate: ~60%
Network requests on page load: 15-20
```

### After Phase 1 (Target)

```
Initial bundle size: ~150KB gzipped
First Contentful Paint (FCP): ~1.8s
API p99 latency: ~350ms
Cache hit rate: ~75%
Network requests on page load: 8-12
```

### Measurement Commands

```bash
# Check bundle size
npm run build
ls -lh build/static/js/*.js | awk '{print $5, $9}' | sort -h

# Check gzipped size
gzip -c build/static/js/main.*.js | wc -c

# Check performance (Chrome DevTools)
# Open http://localhost:3001
# Lighthouse → Generate Report
# Compare FCP, LCP, TTI metrics
```

---

## Next Steps

After Phase 1 is complete:

1. **Measure improvements** with Lighthouse/DevTools
2. **Document baseline vs. new metrics**
3. **Begin Phase 2** (Request Deduplication + React Query)
4. **Plan Phase 3** (Material-UI optimization)

**Timeline:** Expect to complete Phase 1 in 2-3 days with these step-by-step instructions.
