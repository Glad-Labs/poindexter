# 🔧 Glad Labs FastAPI - Technical Recommendations & Implementation Guide

**Document Date:** December 6, 2025  
**Purpose:** Detailed implementation guidance for analysis recommendations  
**Audience:** Engineering team, DevOps, Product leads

---

## Table of Contents

1. [Security Hardening (Priority 1)](#security-hardening)
2. [Performance Optimization (Priority 2)](#performance-optimization)
3. [Testing & Coverage (Priority 3)](#testing--coverage)
4. [DevOps & Monitoring (Priority 4)](#devops--monitoring)
5. [Code Quality & Refactoring (Priority 5)](#code-quality--refactoring)
6. [Implementation Checklists](#implementation-checklists)

---

## Security Hardening

### 1. Fix CORS Configuration (Critical - 1 hour)

**Problem:** Hardcoded, overly permissive CORS configuration

**Current Code:**

```python
# src/cofounder_agent/main.py:351-355
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],  # ❌ Allows all methods including DELETE
    allow_headers=["*"],  # ❌ Allows all headers
)
```

**Solution:**

Create a new configuration module:

```python
# src/cofounder_agent/config/cors_config.py
"""
CORS Configuration - Environment-based, production-safe
"""
import os
from typing import List

def get_cors_config() -> dict:
    """
    Load CORS configuration from environment variables.

    Environment Variables:
        CORS_ORIGINS: Comma-separated list of allowed origins
        CORS_METHODS: Comma-separated list of allowed methods
        CORS_HEADERS: Comma-separated list of allowed headers
        CORS_CREDENTIALS: Whether to allow credentials (true/false)
    """

    # Parse origins from environment
    origins_str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:3001"  # Dev default
    )
    origins = [o.strip() for o in origins_str.split(",") if o.strip()]

    # Parse methods from environment
    methods_str = os.getenv(
        "CORS_METHODS",
        "GET,POST,PUT,DELETE,OPTIONS"  # Common methods
    )
    methods = [m.strip() for m in methods_str.split(",") if m.strip()]

    # Parse headers from environment
    headers_str = os.getenv(
        "CORS_HEADERS",
        "Content-Type,Authorization"  # Essential headers only
    )
    headers = [h.strip() for h in headers_str.split(",") if h.strip()]

    # Credentials flag
    allow_credentials = os.getenv("CORS_CREDENTIALS", "true").lower() == "true"

    return {
        "allow_origins": origins,
        "allow_methods": methods,
        "allow_headers": headers,
        "allow_credentials": allow_credentials,
    }
```

**Update main.py:**

```python
# src/cofounder_agent/main.py - Replace CORS section

from config.cors_config import get_cors_config

# ... other imports ...

app = FastAPI(...)

# Initialize OpenTelemetry tracing
setup_telemetry(app)

# CORS middleware - Now environment-based
cors_config = get_cors_config()
app.add_middleware(CORSMiddleware, **cors_config)

# ... rest of app setup ...
```

**Environment Variables (.env, .env.staging, .env.production):**

```bash
# Development
CORS_ORIGINS=http://localhost:3000,http://localhost:3001
CORS_METHODS=GET,POST,PUT,DELETE,OPTIONS
CORS_HEADERS=Content-Type,Authorization
CORS_CREDENTIALS=true

# Staging
CORS_ORIGINS=https://staging.gladlabs.io
CORS_METHODS=GET,POST,PUT,OPTIONS
CORS_HEADERS=Content-Type,Authorization
CORS_CREDENTIALS=true

# Production
CORS_ORIGINS=https://app.gladlabs.io,https://api.gladlabs.io
CORS_METHODS=GET,POST,PUT,OPTIONS
CORS_HEADERS=Content-Type,Authorization
CORS_CREDENTIALS=true
```

**Testing:**

```python
# src/cofounder_agent/tests/test_cors_config.py
import pytest
from config.cors_config import get_cors_config
import os

def test_cors_from_environment():
    """CORS config should respect environment variables"""
    os.environ["CORS_ORIGINS"] = "https://example.com"
    os.environ["CORS_METHODS"] = "GET,POST"

    config = get_cors_config()

    assert config["allow_origins"] == ["https://example.com"]
    assert config["allow_methods"] == ["GET", "POST"]
    assert config["allow_credentials"] is True

def test_cors_defaults():
    """CORS config should have secure defaults"""
    # Clear environment
    for key in ["CORS_ORIGINS", "CORS_METHODS", "CORS_HEADERS", "CORS_CREDENTIALS"]:
        os.environ.pop(key, None)

    config = get_cors_config()

    assert "http://localhost" in config["allow_origins"]  # Dev default
    assert "GET" in config["allow_methods"]
    assert "POST" in config["allow_methods"]
    assert "DELETE" in config["allow_methods"]  # Still allowed in dev
```

---

### 2. Implement Rate Limiting (Critical - 2 hours)

**Problem:** No rate limiting on expensive endpoints, API cost explosion risk

**Solution:**

```bash
pip install slowapi
```

```python
# src/cofounder_agent/services/rate_limiter.py
"""
Rate limiting configuration and utilities
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import HTTPException, status

# Create global limiter
limiter = Limiter(key_func=get_remote_address)

# Rate limit configurations by endpoint type
RATE_LIMITS = {
    "content_generation": "5/minute",      # Expensive: 5 per minute per IP
    "list_operations": "30/minute",        # List operations: 30 per minute
    "auth": "10/minute",                   # Auth: 10 per minute
    "webhooks": "100/minute",              # Webhooks: 100 per minute
    "health_check": "1000/minute",         # Health checks: unlimited
    "bulk_operations": "2/minute",         # Bulk operations: 2 per minute
}

async def rate_limit_exceeded_handler(request, exc):
    """Handle rate limit exceeded exceptions"""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Rate limit exceeded. {exc.detail}"
    )
```

**Apply Rate Limiting to Endpoints:**

```python
# src/cofounder_agent/routes/content_routes.py
from fastapi import Request
from services.rate_limiter import limiter, RATE_LIMITS

@content_router.post("/api/content/tasks")
@limiter.limit(RATE_LIMITS["content_generation"])
async def create_task(request: Request, task_req: CreateBlogPostRequest, ...):
    """Create a new content task - Rate limited to 5/minute"""
    # ... existing implementation ...

@content_router.get("/api/content/tasks")
@limiter.limit(RATE_LIMITS["list_operations"])
async def list_tasks(request: Request, ...):
    """List tasks - Rate limited to 30/minute"""
    # ... existing implementation ...
```

**Apply to bulk operations:**

```python
# src/cofounder_agent/routes/bulk_task_routes.py
@bulk_task_router.post("/api/content/tasks/bulk/update")
@limiter.limit(RATE_LIMITS["bulk_operations"])
async def bulk_update_tasks(request: Request, bulk_req: BulkUpdateRequest):
    """Bulk update tasks - Rate limited to 2/minute"""
    if len(bulk_req.task_ids) > 100:
        raise HTTPException(
            status_code=400,
            detail="Bulk operations limited to 100 tasks per request"
        )
    # ... rest of implementation ...
```

**Add to main.py:**

```python
# src/cofounder_agent/main.py
from slowapi.errors import RateLimitExceeded
from services.rate_limiter import limiter

app = FastAPI(...)

# Add rate limiter to app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
```

**Test Rate Limiting:**

```python
# src/cofounder_agent/tests/test_rate_limiting.py
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

@pytest.mark.asyncio
async def test_rate_limiting_on_content_creation():
    """Content creation endpoint should be rate limited"""
    for i in range(6):  # Try 6 requests, 5th should be limited
        response = client.post(
            "/api/content/tasks",
            json={
                "task_type": "blog_post",
                "topic": f"Test topic {i}",
                "style": "technical",
                "tone": "professional"
            }
        )

        if i < 5:
            assert response.status_code in [200, 201]  # Should succeed
        else:
            assert response.status_code == 429  # Should be rate limited

def test_bulk_operations_rate_limited():
    """Bulk operations should have stricter limits"""
    response = client.post(
        "/api/content/tasks/bulk/update",
        json={"task_ids": ["id1", "id2"]}
    )
    assert response.status_code in [200, 429]  # 200 if under limit, 429 if over
```

---

### 3. Add HTML Sanitization (High - 3 hours)

**Problem:** Generated content stored without sanitization, XSS risk

**Solution:**

```bash
pip install bleach
```

```python
# src/cofounder_agent/services/content_sanitizer.py
"""
Content sanitization for XSS prevention
"""
import bleach
from typing import Dict, Any

# Allowed HTML tags for blog content
ALLOWED_TAGS = [
    "p", "br", "strong", "em", "u", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "code", "pre", "a", "img",
    "table", "thead", "tbody", "tr", "th", "td", "div", "span"
]

# Allowed attributes per tag
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title", "width", "height"],
    "code": ["class"],  # For syntax highlighting classes
    "div": ["class", "id"],
    "span": ["class", "id"],
}

def sanitize_html_content(content: str) -> str:
    """
    Sanitize HTML content to prevent XSS attacks.

    Args:
        content: Raw HTML content

    Returns:
        Sanitized HTML content with dangerous tags/attributes removed
    """
    if not content:
        return ""

    # Clean HTML with allowed tags
    cleaned = bleach.clean(
        content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True  # Remove disallowed tags, don't escape
    )

    # Additional: linkify URLs that aren't already links
    cleaned = bleach.linkify(cleaned)

    return cleaned

def sanitize_content_task(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize all content fields in a task.

    Args:
        task_data: Task data dictionary

    Returns:
        Task data with sanitized content fields
    """
    sanitized = task_data.copy()

    # Sanitize content field
    if "content" in sanitized and sanitized["content"]:
        sanitized["content"] = sanitize_html_content(sanitized["content"])

    # Sanitize excerpt field
    if "excerpt" in sanitized and sanitized["excerpt"]:
        sanitized["excerpt"] = bleach.clean(
            sanitized["excerpt"],
            tags=[],  # No HTML in excerpts
            strip=True
        )

    return sanitized
```

**Apply sanitization in content routes:**

```python
# src/cofounder_agent/routes/content_routes.py
from services.content_sanitizer import sanitize_content_task

@content_router.post("/api/content/tasks/{task_id}/approve")
async def approve_task(task_id: str, approval_req: ApprovalRequest):
    """Approve and publish content task"""

    # Get task from database
    task = await database_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Sanitize content before publishing
    sanitized_task = sanitize_content_task(task)

    # Update with sanitized content
    await database_service.update_task(
        task_id,
        {
            "content": sanitized_task["content"],
            "excerpt": sanitized_task["excerpt"],
            "status": "published"
        }
    )

    return {"message": "Content published successfully"}
```

**Test HTML sanitization:**

```python
# src/cofounder_agent/tests/test_content_sanitizer.py
import pytest
from services.content_sanitizer import sanitize_html_content

def test_sanitize_removes_script_tags():
    """Should remove script tags and contents"""
    content = "<p>Hello</p><script>alert('xss')</script>"
    result = sanitize_html_content(content)
    assert "<script>" not in result
    assert "alert" not in result

def test_sanitize_keeps_allowed_tags():
    """Should keep allowed tags like <p>, <strong>, <a>"""
    content = "<p>Hello <strong>world</strong> <a href='#'>link</a></p>"
    result = sanitize_html_content(content)
    assert "<p>" in result
    assert "<strong>" in result
    assert "<a" in result

def test_sanitize_removes_dangerous_attributes():
    """Should remove dangerous attributes like onclick, onerror"""
    content = "<img src='img.jpg' onerror='alert(1)'>"
    result = sanitize_html_content(content)
    assert "onerror" not in result

def test_sanitize_handles_null_content():
    """Should handle null/empty content gracefully"""
    assert sanitize_html_content(None) == ""
    assert sanitize_html_content("") == ""
```

---

### 4. Implement Webhook Signature Verification (High - 2 hours)

**Problem:** Webhooks can be triggered by anyone, no authorization

**Solution:**

```python
# src/cofounder_agent/services/webhook_security.py
"""
Webhook security: signature verification and timestamp validation
"""
import hmac
import hashlib
import time
from typing import Dict, Any
from fastapi import HTTPException, status

def generate_webhook_signature(
    payload: str,
    secret: str,
    timestamp: int
) -> str:
    """
    Generate HMAC-SHA256 signature for webhook payload.

    Args:
        payload: JSON payload as string
        secret: Webhook secret
        timestamp: Unix timestamp

    Returns:
        Signature hex string
    """
    # Format: timestamp.payload
    signed_content = f"{timestamp}.{payload}"

    signature = hmac.new(
        secret.encode("utf-8"),
        signed_content.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return signature

def verify_webhook_signature(
    payload: str,
    signature: str,
    secret: str,
    timestamp: int,
    tolerance_seconds: int = 300
) -> bool:
    """
    Verify webhook signature and timestamp.

    Args:
        payload: JSON payload as string
        signature: Provided signature header
        secret: Webhook secret
        timestamp: Unix timestamp from header
        tolerance_seconds: Allow timestamps within this many seconds (default: 5 min)

    Returns:
        True if signature valid and timestamp recent

    Raises:
        HTTPException: If validation fails
    """
    # Check timestamp is recent (prevent replay attacks)
    current_time = int(time.time())
    if abs(current_time - timestamp) > tolerance_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook timestamp too old (replay attack?)"
        )

    # Generate expected signature
    expected_signature = generate_webhook_signature(payload, secret, timestamp)

    # Compare signatures using constant-time comparison (prevent timing attacks)
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature"
        )

    return True
```

**Apply to webhook routes:**

```python
# src/cofounder_agent/routes/webhooks.py
import os
import json
from fastapi import Request
from services.webhook_security import verify_webhook_signature

@webhook_router.post("/api/webhooks/content-generated")
async def webhook_content_generated(request: Request):
    """
    Webhook endpoint for content generation completion.

    Expected headers:
    - X-Webhook-Signature: HMAC-SHA256 signature
    - X-Webhook-Timestamp: Unix timestamp
    """

    # Get webhook secret from environment
    webhook_secret = os.getenv("WEBHOOK_SECRET")
    if not webhook_secret:
        raise HTTPException(
            status_code=500,
            detail="Webhook secret not configured"
        )

    # Get signature and timestamp from headers
    signature = request.headers.get("X-Webhook-Signature")
    timestamp_str = request.headers.get("X-Webhook-Timestamp")

    if not signature or not timestamp_str:
        raise HTTPException(
            status_code=401,
            detail="Missing webhook headers"
        )

    try:
        timestamp = int(timestamp_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid timestamp format"
        )

    # Get request body
    body = await request.body()
    payload_str = body.decode("utf-8")

    # Verify signature
    verify_webhook_signature(
        payload_str,
        signature,
        webhook_secret,
        timestamp,
        tolerance_seconds=300
    )

    # Parse and process payload
    payload = json.loads(payload_str)

    # Process webhook...
    logger.info(f"✅ Valid webhook received: {payload.get('event_type')}")

    return {"message": "Webhook processed successfully"}
```

**Update .env files:**

```bash
# Generate random webhook secret
# python -c "import secrets; print(secrets.token_hex(32))"

WEBHOOK_SECRET=your_random_hex_secret_here_32_bytes
```

**Test webhook verification:**

```python
# src/cofounder_agent/tests/test_webhook_security.py
import json
import time
from services.webhook_security import generate_webhook_signature, verify_webhook_signature

def test_webhook_signature_valid():
    """Should accept valid signatures"""
    payload = json.dumps({"event": "test"})
    secret = "test_secret"
    timestamp = int(time.time())

    signature = generate_webhook_signature(payload, secret, timestamp)

    # Should not raise
    assert verify_webhook_signature(payload, signature, secret, timestamp) is True

def test_webhook_signature_invalid():
    """Should reject invalid signatures"""
    payload = json.dumps({"event": "test"})
    secret = "test_secret"
    timestamp = int(time.time())

    invalid_signature = "invalid_signature_string"

    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature(payload, invalid_signature, secret, timestamp)

    assert exc.value.status_code == 401

def test_webhook_timestamp_expired():
    """Should reject old timestamps (replay attack prevention)"""
    payload = json.dumps({"event": "test"})
    secret = "test_secret"
    old_timestamp = int(time.time()) - 600  # 10 minutes old

    signature = generate_webhook_signature(payload, secret, old_timestamp)

    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature(payload, signature, secret, old_timestamp)

    assert exc.value.status_code == 401
```

---

## Performance Optimization

### 1. Redis Caching Layer (High - 4 hours)

**Problem:** Expensive operations recalculated on every request (embeddings, model checks)

**Solution:**

```bash
pip install redis aioredis
```

```python
# src/cofounder_agent/services/cache_service.py
"""
Redis caching service for expensive operations
"""
import asyncio
import json
import logging
from typing import Optional, Any, Callable
from datetime import timedelta
import aioredis

logger = logging.getLogger(__name__)

class CacheService:
    """Async Redis cache for embedding and model availability caching"""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or "redis://localhost:6379"
        self.redis = None

    async def initialize(self):
        """Initialize Redis connection"""
        try:
            self.redis = await aioredis.create_redis_pool(self.redis_url)
            logger.info("✅ Redis cache initialized")
        except Exception as e:
            logger.warning(f"⚠️ Redis cache initialization failed: {e}")
            self.redis = None

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if not self.redis:
            return None

        try:
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.warning(f"Cache get failed: {e}")
            return None

    async def set(self, key: str, value: Any, ttl_seconds: int = 3600):
        """Set value in cache with TTL"""
        if not self.redis:
            return

        try:
            await self.redis.setex(
                key,
                ttl_seconds,
                json.dumps(value)
            )
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")

    async def delete(self, key: str):
        """Delete value from cache"""
        if not self.redis:
            return

        try:
            await self.redis.delete(key)
        except Exception as e:
            logger.warning(f"Cache delete failed: {e}")

    async def cached(
        self,
        key: str,
        ttl_seconds: int,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Get value from cache or compute using function.

        Args:
            key: Cache key
            ttl_seconds: Time-to-live in seconds
            func: Async function to call if cache miss
            *args, **kwargs: Arguments to pass to function

        Returns:
            Cached or freshly computed value
        """
        # Try cache first
        cached_value = await self.get(key)
        if cached_value is not None:
            logger.debug(f"Cache hit: {key}")
            return cached_value

        # Cache miss - compute value
        logger.debug(f"Cache miss: {key}")
        value = await func(*args, **kwargs)

        # Store in cache
        await self.set(key, value, ttl_seconds)

        return value

    async def close(self):
        """Close Redis connection"""
        if self.redis:
            self.redis.close()
            await self.redis.wait_closed()

# Global cache instance
_cache_service: Optional[CacheService] = None

def get_cache_service() -> CacheService:
    """Get global cache service"""
    return _cache_service

def set_cache_service(cache: CacheService):
    """Set global cache service"""
    global _cache_service
    _cache_service = cache
```

**Use caching in services:**

```python
# src/cofounder_agent/services/model_router.py
from services.cache_service import get_cache_service

async def check_model_availability(provider: str) -> bool:
    """
    Check if model provider is available.

    Results cached for 5 minutes to avoid repeated API calls.
    """
    cache = get_cache_service()
    cache_key = f"model_available:{provider}"

    # Try to get from cache
    if cache:
        cached_available = await cache.get(cache_key)
        if cached_available is not None:
            logger.debug(f"Using cached availability for {provider}")
            return cached_available

    # Check actual availability
    is_available = await _check_provider_health(provider)

    # Cache result for 5 minutes
    if cache:
        await cache.set(cache_key, is_available, ttl_seconds=300)

    return is_available
```

**Cache semantic search results:**

```python
# src/cofounder_agent/services/semantic_search.py
from services.cache_service import get_cache_service
import hashlib

async def semantic_search(query: str, top_k: int = 5) -> List[Dict]:
    """
    Semantic search with caching.

    Results for identical queries cached for 1 hour.
    """
    cache = get_cache_service()

    # Create cache key from query
    query_hash = hashlib.md5(query.encode()).hexdigest()
    cache_key = f"semantic_search:{query_hash}:{top_k}"

    # Use cached decorator
    results = await cache.cached(
        cache_key,
        ttl_seconds=3600,  # 1 hour
        func=_perform_semantic_search,
        query=query,
        top_k=top_k
    )

    return results

async def _perform_semantic_search(query: str, top_k: int) -> List[Dict]:
    """Actual semantic search implementation"""
    # Get embeddings for query
    query_embedding = await get_embedding_model().encode(query)

    # Search database
    results = await database_service.semantic_search(
        query_embedding,
        top_k=top_k
    )

    return results
```

**Initialize cache in main.py:**

```python
# src/cofounder_agent/main.py
from services.cache_service import CacheService, set_cache_service

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize cache"""

    # Initialize Redis cache
    cache_service = CacheService()
    await cache_service.initialize()
    set_cache_service(cache_service)

    logger.info("✅ Cache service initialized")

    yield

    # Cleanup
    await cache_service.close()
```

---

### 2. PostgreSQL LISTEN/NOTIFY (High - 6 hours)

**Problem:** Task polling every 5 seconds = 17,280 unnecessary queries/day

**Solution:**

```python
# src/cofounder_agent/services/task_notifier.py
"""
PostgreSQL LISTEN/NOTIFY for task event notifications.

Replaces polling with event-driven task processing.
Reduces overhead by 95%+ compared to 5-second polling.
"""

import asyncpg
import asyncio
import logging
from typing import Callable, List

logger = logging.getLogger(__name__)

class TaskNotifier:
    """PostgreSQL LISTEN/NOTIFY wrapper for task events"""

    def __init__(self, database_pool: asyncpg.Pool):
        self.pool = database_pool
        self.listener_task = None
        self.running = False
        self.listeners: List[Callable] = []

    async def subscribe(self, callback: Callable):
        """Subscribe to task events"""
        self.listeners.append(callback)
        logger.info(f"Task event subscriber registered (total: {len(self.listeners)})")

    async def start(self):
        """Start listening for task events"""
        if self.running:
            logger.warning("Task notifier already running")
            return

        self.running = True
        self.listener_task = asyncio.create_task(self._listen_loop())
        logger.info("✅ Task notifier started (listening for PostgreSQL events)")

    async def stop(self):
        """Stop listening for task events"""
        self.running = False
        if self.listener_task:
            self.listener_task.cancel()
            try:
                await self.listener_task
            except asyncio.CancelledError:
                pass
        logger.info("✅ Task notifier stopped")

    async def _listen_loop(self):
        """Listen for PostgreSQL NOTIFY events"""
        conn = None
        try:
            # Get dedicated connection for LISTEN
            conn = await self.pool.acquire()

            # Subscribe to task channels
            await conn.add_listener("task_created", self._on_task_event)
            await conn.add_listener("task_updated", self._on_task_event)
            await conn.add_listener("task_completed", self._on_task_event)

            logger.info("✅ Listening on task event channels")

            # Keep connection open and listen
            while self.running:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"❌ Task notifier error: {e}", exc_info=True)

        finally:
            if conn:
                await conn.remove_listener("task_created", self._on_task_event)
                await conn.remove_listener("task_updated", self._on_task_event)
                await conn.remove_listener("task_completed", self._on_task_event)
                await self.pool.release(conn)
            logger.info("Task notifier connection closed")

    async def _on_task_event(self, conn, pid, channel, payload):
        """Handle PostgreSQL NOTIFY event"""
        logger.debug(f"Task event: {channel}: {payload}")

        # Call all registered listeners
        for listener in self.listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(channel, payload)
                else:
                    listener(channel, payload)
            except Exception as e:
                logger.error(f"Error in task listener: {e}", exc_info=True)

    async def notify(self, channel: str, payload: str):
        """Send notification (for testing)"""
        async with self.pool.acquire() as conn:
            await conn.execute(f"NOTIFY {channel}, '{payload}'")
```

**Update task executor to use notifications:**

```python
# src/cofounder_agent/services/task_executor.py
from services.task_notifier import TaskNotifier

class TaskExecutor:
    """Background task executor with event-driven processing"""

    def __init__(self, database_service, orchestrator=None, critique_loop=None):
        # ... existing init ...
        self.notifier = TaskNotifier(database_service.pool)

    async def start(self):
        """Start task executor with event-driven processing"""
        if self.running:
            return

        self.running = True
        logger.info("🚀 Starting task executor (event-driven)...")

        # Subscribe to task events
        await self.notifier.subscribe(self._on_task_event)

        # Start listening for events
        await self.notifier.start()

        logger.info("✅ Task executor started")

    async def stop(self):
        """Stop task executor"""
        if not self.running:
            return

        self.running = False
        await self.notifier.stop()
        logger.info("✅ Task executor stopped")

    async def _on_task_event(self, channel: str, payload: str):
        """Handle task event notification"""
        import json

        try:
            event_data = json.loads(payload)

            if channel == "task_created":
                task_id = event_data.get("task_id")
                logger.info(f"📋 Task created: {task_id}")
                await self._process_task(task_id)

            elif channel == "task_updated":
                logger.debug(f"Task updated: {event_data}")

            elif channel == "task_completed":
                task_id = event_data.get("task_id")
                logger.info(f"✅ Task completed: {task_id}")

        except Exception as e:
            logger.error(f"Error processing task event: {e}", exc_info=True)
```

**Database migration to add NOTIFY triggers:**

```sql
-- src/cofounder_agent/migrations/add_task_notifications.sql

-- Trigger function to notify on task creation
CREATE OR REPLACE FUNCTION notify_task_created()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify(
        'task_created',
        json_build_object('task_id', NEW.id, 'created_at', NEW.created_at)::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger function to notify on task update
CREATE OR REPLACE FUNCTION notify_task_updated()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify(
        'task_updated',
        json_build_object('task_id', NEW.id, 'status', NEW.status)::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create triggers on tasks table
DROP TRIGGER IF EXISTS task_created_notify ON tasks;
CREATE TRIGGER task_created_notify
AFTER INSERT ON tasks
FOR EACH ROW
EXECUTE FUNCTION notify_task_created();

DROP TRIGGER IF EXISTS task_updated_notify ON tasks;
CREATE TRIGGER task_updated_notify
AFTER UPDATE ON tasks
FOR EACH ROW
WHEN (OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION notify_task_updated();
```

---

## Testing & Coverage

### 1. Add Test Coverage Reporting (Medium - 2 hours)

**Add to CI/CD Pipeline:**

```bash
# In your GitHub Actions workflow
cd src/cofounder_agent
python -m pytest tests/ --cov=. --cov-report=xml --cov-report=html
```

**Create pytest configuration:**

```ini
# src/cofounder_agent/pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts =
    -v
    --strict-markers
    --tb=short
    --cov=src/cofounder_agent
    --cov-report=term-missing:skip-covered
    --cov-report=html:htmlcov
    --cov-report=xml
    --cov-fail-under=80
asyncio_mode = auto

markers =
    unit: unit tests
    integration: integration tests
    api: API endpoint tests
    e2e: end-to-end tests
    performance: performance benchmarks
    security: security tests
```

**Update requirements.txt:**

```bash
pip install pytest-cov
```

---

### 2. Test Critical Components (High - 8 hours)

```python
# src/cofounder_agent/tests/test_orchestrator.py
import pytest
from orchestrator_logic import Orchestrator

@pytest.mark.asyncio
@pytest.mark.unit
async def test_orchestrator_initialization():
    """Orchestrator should initialize without errors"""
    orchestrator = Orchestrator()
    assert orchestrator is not None
    assert orchestrator.database_service is None  # Optional

@pytest.mark.asyncio
@pytest.mark.integration
async def test_orchestrator_process_command(database_service, orchestrator):
    """Orchestrator should process commands"""
    result = await orchestrator.process_command_async(
        "generate blog post about AI",
        context={"topic": "AI"}
    )
    assert result is not None
    assert "response" in result

# ... more tests for model router, content generation, etc.
```

---

## DevOps & Monitoring

### 1. Granular Health Check Endpoints (Medium - 3 hours)

```python
# src/cofounder_agent/services/health_check.py
"""
Granular health checks for Kubernetes and monitoring systems
"""
import asyncio
from typing import Dict, Any
from datetime import datetime

class HealthChecker:
    """Performs comprehensive health checks on all system components"""

    def __init__(self, database_service, orchestrator, task_executor, cache_service):
        self.db = database_service
        self.orchestrator = orchestrator
        self.task_executor = task_executor
        self.cache = cache_service

    async def liveness(self) -> Dict[str, Any]:
        """
        Liveness probe - is the application running?

        Used by: Kubernetes liveness probe, load balancer
        Returns: 200 if app is responsive
        """
        return {
            "status": "alive",
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def readiness(self) -> Dict[str, Any]:
        """
        Readiness probe - is the application ready to serve requests?

        Used by: Kubernetes readiness probe, load balancer
        Returns: 200 only if all dependencies are available
        """
        checks = {
            "database": await self._check_database(),
            "cache": await self._check_cache(),
            "orchestrator": self._check_orchestrator(),
        }

        all_ready = all(check.get("healthy") for check in checks.values())

        return {
            "status": "ready" if all_ready else "not_ready",
            "checks": checks,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def detailed(self) -> Dict[str, Any]:
        """
        Detailed health report - for monitoring dashboards

        Returns comprehensive status of all components
        """
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "components": {
                "database": await self._check_database(),
                "cache": await self._check_cache(),
                "orchestrator": self._check_orchestrator(),
                "task_executor": self._check_task_executor(),
            }
        }

    async def _check_database(self) -> Dict[str, Any]:
        """Check database connectivity"""
        if not self.db:
            return {"healthy": False, "reason": "not_initialized"}

        try:
            health = await self.db.health_check()
            return health
        except Exception as e:
            return {"healthy": False, "reason": str(e)}

    async def _check_cache(self) -> Dict[str, Any]:
        """Check cache connectivity"""
        if not self.cache:
            return {"healthy": False, "reason": "not_initialized"}

        try:
            # Try to set and get a test value
            await self.cache.set("health_check", "ok", ttl_seconds=10)
            value = await self.cache.get("health_check")

            return {
                "healthy": value == "ok",
                "type": "redis"
            }
        except Exception as e:
            return {"healthy": False, "reason": str(e)}

    def _check_orchestrator(self) -> Dict[str, Any]:
        """Check orchestrator status"""
        if not self.orchestrator:
            return {"healthy": False, "reason": "not_initialized"}

        return {
            "healthy": True,
            "agents_available": {
                "financial": self.orchestrator.financial_agent_available,
                "compliance": self.orchestrator.compliance_agent_available,
            }
        }

    def _check_task_executor(self) -> Dict[str, Any]:
        """Check task executor status"""
        if not self.task_executor:
            return {"healthy": False, "reason": "not_initialized"}

        return {
            "healthy": self.task_executor.running,
            "tasks_processed": self.task_executor.task_count,
            "success_count": self.task_executor.success_count,
            "error_count": self.task_executor.error_count,
        }
```

**Add endpoints to main.py:**

```python
# src/cofounder_agent/main.py

health_checker = None

@app.get("/health/live")
async def liveness_probe():
    """Kubernetes liveness probe"""
    return await health_checker.liveness()

@app.get("/health/ready")
async def readiness_probe():
    """Kubernetes readiness probe"""
    return await health_checker.readiness()

@app.get("/api/health")
async def detailed_health():
    """Detailed health report"""
    return await health_checker.detailed()
```

---

### 2. Prometheus Metrics (Medium - 3 hours)

```bash
pip install prometheus-client
```

```python
# src/cofounder_agent/services/metrics.py
"""
Prometheus metrics for monitoring
"""
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from typing import Optional
import time

# Metrics
task_created_counter = Counter(
    'tasks_created_total',
    'Total number of tasks created',
    ['task_type']
)

task_completed_counter = Counter(
    'tasks_completed_total',
    'Total number of tasks completed',
    ['task_type', 'status']
)

task_duration_histogram = Histogram(
    'task_duration_seconds',
    'Task execution duration in seconds',
    ['task_type', 'status']
)

api_request_histogram = Histogram(
    'api_request_duration_seconds',
    'API request duration in seconds',
    ['method', 'endpoint', 'status_code']
)

database_connection_gauge = Gauge(
    'database_connections_active',
    'Number of active database connections'
)

cache_hit_counter = Counter(
    'cache_hits_total',
    'Total number of cache hits'
)

cache_miss_counter = Counter(
    'cache_misses_total',
    'Total number of cache misses'
)

def record_task_created(task_type: str):
    """Record task creation"""
    task_created_counter.labels(task_type=task_type).inc()

def record_task_completed(task_type: str, status: str, duration: float):
    """Record task completion"""
    task_completed_counter.labels(task_type=task_type, status=status).inc()
    task_duration_histogram.labels(task_type=task_type, status=status).observe(duration)

def record_api_request(method: str, endpoint: str, status_code: int, duration: float):
    """Record API request"""
    api_request_histogram.labels(
        method=method,
        endpoint=endpoint,
        status_code=status_code
    ).observe(duration)
```

**Add metrics endpoint to main.py:**

```python
# src/cofounder_agent/main.py
from prometheus_client import generate_latest
from starlette.responses import Response

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(), media_type="text/plain")
```

---

## Implementation Checklist

### Week 1: Critical Security

- [ ] Fix CORS configuration (environment-based)
- [ ] Implement rate limiting on all endpoints
- [ ] Add webhook signature verification
- [ ] Add HTML sanitization for content
- [ ] Update .env files with proper values
- [ ] Add test coverage reporting
- [ ] Create security tests

### Week 2: Performance & Observability

- [ ] Set up Redis cache
- [ ] Implement caching for embeddings
- [ ] Cache model availability checks
- [ ] Replace task polling with LISTEN/NOTIFY
- [ ] Add granular health checks
- [ ] Expose Prometheus metrics
- [ ] Add GZIP compression middleware
- [ ] Add environment variable validation

### Week 3-4: Testing & Quality

- [ ] Write tests for orchestrator
- [ ] Write tests for model router
- [ ] Add E2E content pipeline test
- [ ] Add security tests (CORS, XSS, auth)
- [ ] Add load testing scenarios
- [ ] Set up CI/CD coverage gates
- [ ] Document API versioning strategy

### Week 5+: Features & Scalability

- [ ] Implement API versioning (v1, v2)
- [ ] Add WebSocket for real-time progress
- [ ] Implement workflow templates
- [ ] Add multi-tenancy support
- [ ] Add GDPR compliance features
- [ ] Remove dead code
- [ ] Expand documentation

---

## Success Metrics

After implementing these recommendations, measure:

| Metric                  | Target                 | Current → Target |
| ----------------------- | ---------------------- | ---------------- |
| Test Coverage           | >80%                   | Unknown → >80%   |
| API Response Time (P95) | <500ms                 | 2-3s → <500ms    |
| Cache Hit Rate          | >70%                   | 0% → >70%        |
| Task Polling Overhead   | -95%                   | 100% → 5%        |
| Security Issues         | 0 critical             | 3 → 0            |
| Startup Time            | <30s                   | 60-90s → <30s    |
| API Uptime SLA          | 99.9%                  | Unknown → 99.9%  |
| Code Coverage           | 100% on critical paths | Unknown → High   |

---

**Document Version:** 1.0  
**Last Updated:** December 6, 2025  
**Next Review:** January 6, 2026
