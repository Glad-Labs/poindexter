# Extended Code Audit - Additional Issues Found

**Date:** January 17, 2026  
**Scope:** Deep dive into services, OAuth, file handling, API clients  
**New Issues Found:** 18 additional issues  
**Total Issues (Phase 1 + Phase 2):** 33 total

---

## 🔴 CRITICAL ISSUES (Phase 2)

### 1. **Synchronous Requests in Async Context**

**File:** `src/cofounder_agent/services/cloudinary_cms_service.py:145, 164, 275, 299, 304`

**Issue:**

```python
# These are BLOCKING synchronous calls in async endpoints
response = requests.post(...)  # BLOCKING - blocks entire event loop
data = response.json()          # BLOCKING

response = requests.delete(...)  # BLOCKING
response = requests.get(...)     # BLOCKING
```

**Problems:**

- FastAPI endpoint can hang while requests library blocks
- Other requests to server will queue behind this
- No timeout specification on requests
- No error handling for connection errors

**Impact:** Critical - can hang the entire server

**Fix:**

```python
# Replace with async httpx
import httpx

async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.post(...)
    data = response.json()
```

**Affected Functions:**

- `upload_image()` - line 145
- `get_image_metadata()` - line 299
- `delete_image()` - line 275

---

### 2. **File Handle Leaks - Unmanaged File Opens**

**File:** `src/cofounder_agent/services/fine_tuning_service.py:89, 233, 303`

**Issue:**

```python
# File not closed if exception occurs
with open(modelfile_path, "w") as f:
    f.write(modelfile_content)

# But this is in a subprocess creation that could fail
process = await asyncio.create_subprocess_exec(...)
# If subprocess fails, file is orphaned

# Similar issues with dataset files
with open(dataset_path, "rb") as f:
    data = f.read()  # Could raise exception before close
```

**Problems:**

- File context managers work, BUT
- Large files could exhaust file descriptors if job fails
- No cleanup of temp files created during fine-tuning
- No tempfile cleanup on process failure
- `/tmp/Modelfile_*` files could accumulate

**Impact:** High - file descriptor exhaustion, disk space leak

**Fix:**

```python
import tempfile
import atexit

# Use temp directory with cleanup
with tempfile.TemporaryDirectory() as tmpdir:
    modelfile_path = os.path.join(tmpdir, f"Modelfile_{job_id}")
    with open(modelfile_path, "w") as f:
        f.write(modelfile_content)

    # Process creation here
    # Cleanup happens automatically when context exits
```

**Affected Functions:**

- `fine_tune_ollama()` - line 89
- `fine_tune_gemini()` - line 233
- Dataset handling - line 303

---

### 3. **Missing Session Cleanup - aiohttp Resource Leak**

**File:** `src/cofounder_agent/services/huggingface_client.py:57-60`

**Issue:**

```python
async def _ensure_session(self) -> aiohttp.ClientSession:
    """Ensure aiohttp session is created"""
    if self.session is None:
        self.session = aiohttp.ClientSession()  # Created but no lifecycle management
    return self.session
```

**Problems:**

- `close()` method exists but never called on shutdown
- If service is recreated, old session is abandoned
- No connection pooling timeout
- Session could outlive the event loop

**Impact:** High - connection resource exhaustion

**Fix:**

```python
# Add to main.py lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    hf_client = app.state.hf_client
    yield
    # Shutdown - cleanup
    if hf_client.session:
        await hf_client.close()

# OR add to service class
async def __aenter__(self):
    return self

async def __aexit__(self, *args):
    await self.close()
```

---

## 🟠 HIGH SEVERITY ISSUES (Phase 2)

### 4. **Missing JWT Token Validation - OAuth Services**

**File:** `src/cofounder_agent/services/github_oauth.py:99, 140`

**Issue:**

```python
# OAuth callbacks don't validate token expiration
data = response.json()  # Could be expired token
# Token is returned without exp claim validation
```

**Problems:**

- OAuth tokens from GitHub might have expiration
- No verification that token is still valid
- No refresh token handling
- Could grant access with expired credentials

**Impact:** High - security vulnerability

---

### 5. **No Async Context Manager for Database Connections**

**File:** `src/cofounder_agent/services/content_db.py:434`

**Issue:**

```python
except Exception:
    logger.debug("Cost tracking not available...")
    # Silently swallows all exceptions
    # Connection might not be properly closed
```

**Problems:**

- Bare `except Exception:` masks real errors
- Database connection might leak if query fails
- No retry logic for transient failures

**Impact:** High - could cause connection pool exhaustion

---

### 6. **Missing Timeout on Long-Running Tasks**

**File:** `src/cofounder_agent/services/task_executor.py`

**Issue:**

```python
# Background task runs indefinitely with no timeout
while self.running:
    # Poll every 5 seconds - no cleanup, no resource limits
    await asyncio.sleep(self.poll_interval)
```

**Problems:**

- Tasks can run indefinitely if hung
- No memory limits on task accumulation
- Background thread never stops gracefully
- Could cause memory leak on deployment

**Impact:** High - memory exhaustion on long deployments

---

### 7. **Missing Error Types in Content DB**

**File:** `src/cofounder_agent/services/content_db.py:449`

**Issue:**

```python
except Exception as e:  # Too broad
    logger.error(f"❌ Failed to get metrics: {e}")
```

**Problems:**

- Should catch specific DB exceptions
- Could hide programming errors
- Makes debugging difficult

**Impact:** High - poor error diagnostics

---

## 🟡 MEDIUM SEVERITY ISSUES (Phase 2)

### 8. **Response JSON Parsing Without Error Handling**

**File:** `src/cofounder_agent/services/image_service.py:498`

**Issue:**

```python
data = response.json()  # Could raise JSONDecodeError
# No try/except wrapping
```

**Problems:**

- Invalid JSON responses crash the handler
- No fallback if API returns non-JSON

**Fix:** Add try/except for json.JSONDecodeError

---

### 9. **Missing Input Validation in OAuth Handlers**

**File:** `src/cofounder_agent/services/github_oauth.py`

**Issue:**

```python
# GitHub OAuth callback doesn't validate state parameter against CSRF
data = response.json()
# Uses returned data without validation
```

**Problems:**

- CSRF tokens not validated
- State mismatch could indicate attack
- No rate limiting on token exchange

**Fix:** Validate state token matches session state

---

### 10. **Hardcoded Timeouts and No Configuration**

**File:** `src/cofounder_agent/services/huggingface_client.py:84, 136`

**Issue:**

```python
timeout=aiohttp.ClientTimeout(total=5)      # Hardcoded 5s
timeout=aiohttp.ClientTimeout(total=300)    # Hardcoded 5min
```

**Problems:**

- Different models need different timeouts
- No way to configure via environment
- Could fail for slow models

**Fix:** Make configurable via environment variables

---

### 11. **Missing Process Cleanup on Cancellation**

**File:** `src/cofounder_agent/services/fine_tuning_service.py:95`

**Issue:**

```python
process = await asyncio.create_subprocess_exec(...)
# If task is cancelled, subprocess still runs
```

**Problems:**

- Subprocess can continue after handler exits
- Zombie processes could accumulate
- Resource exhaustion

**Fix:** Add process cleanup on cancellation

---

### 12. **Model Loading Without GPU Memory Check**

**File:** `src/cofounder_agent/services/image_service.py:200+`

**Issue:**

```python
# SDXL pipeline loads without checking available VRAM
self.sdxl_pipe = StableDiffusionXLPipeline.from_pretrained(...)
# Could crash if not enough GPU memory
```

**Problems:**

- No VRAM check before loading
- Could crash entire service
- No fallback to CPU

**Fix:** Check GPU memory before loading

---

### 13. **Unguarded Model Router Fallback Chain**

**File:** `src/cofounder_agent/services/model_router.py`

**Issue:**

```python
# Routes through multiple providers but doesn't validate
# each provider's availability before calling
```

**Problems:**

- Could exhaust all API keys on retries
- No backoff strategy
- Rate limits not respected

**Fix:** Add provider health checks

---

### 14. **Missing Dependency Injection Validation**

**File:** `src/cofounder_agent/utils/route_utils.py:60+`

**Issue:**

```python
def get_database(self) -> Optional[Any]:
    """Get the database service"""
    return self._database_service  # Could be None

# Routes don't check if dependency is initialized
```

**Problems:**

- Routes might get None dependency
- Crashes at runtime instead of startup
- No validation errors

**Fix:** Add non-None assertion or factory validation

---

### 15. **Missing Metrics Endpoint Rate Limiting**

**File:** `src/cofounder_agent/services/content_db.py:410+`

**Issue:**

```python
# Metrics endpoint queries database every request
# No caching, no rate limiting
```

**Problems:**

- Could be exploited for DoS
- Expensive query runs on every request
- No pagination

**Fix:** Add response caching (60 seconds)

---

## 🟢 LOW SEVERITY ISSUES (Phase 2)

### 16. **Missing Service Initialization Logging**

**Issue:** Services don't log initialization status on startup

**Impact:** Harder to debug startup issues

---

### 17. **No Structured Logging Consistency**

**Issue:** Mix of structlog, logging, and print() statements

**Impact:** Harder to parse logs in production

---

### 18. **Missing OpenAPI Documentation for Some Endpoints**

**Issue:** Some async endpoints missing summary/description

**Impact:** API docs incomplete

---

## Summary of Phase 2 Findings

| Severity    | Count  | Category                               |
| ----------- | ------ | -------------------------------------- |
| 🔴 Critical | 3      | Blocking, Resource Leaks, Async Issues |
| 🟠 High     | 4      | Security, Connection, Timeouts         |
| 🟡 Medium   | 8      | Error Handling, Validation, Config     |
| 🟢 Low      | 3      | Logging, Documentation                 |
| **Total**   | **18** | **Additional Issues**                  |

## Cumulative Status

**Phase 1:** 15 issues fixed  
**Phase 2:** 18 issues found  
**Total:** 33 issues identified

## Priority Fixes (Phase 2)

**Immediate (Next 2 hours):**

1. Replace synchronous `requests` with `httpx` in Cloudinary service
2. Add proper file cleanup with tempfile
3. Add aiohttp session cleanup to shutdown lifecycle

**Within 24 hours:** 4. Add timeout configuration for all HTTP clients 5. Add process cleanup for subprocess tasks 6. Add GPU memory check before SDXL loading

**Within 1 week:** 7. Add metrics caching 8. Add provider health checks to model router 9. Improve error handling consistency across all services
