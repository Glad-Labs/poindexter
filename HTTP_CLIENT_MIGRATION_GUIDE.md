# 🔄 HTTP Client Migration: requests → httpx (Async-First)

**Status:** 🚀 IN PROGRESS  
**Objective:** Replace all synchronous `requests` and mixed async/sync patterns with pure `httpx` async client  
**Target:** Zero blocking I/O, full async pipeline  
**Date Started:** November 2024

---

## 📋 Migration Checklist

### Completed ✅

- [x] `serper_client.py`: Import changed from `requests` → `httpx`
- [x] `pexels_client.py`: Imports changed from `aiohttp+requests` → `httpx` only
- [x] `ai_content_generator.py`: Import added `httpx`, fixed sync Ollama check to async

### In Progress 🔄

- [ ] `serper_client.py`: Convert all `requests.post()` → `async with httpx.AsyncClient() as client: await client.post()`
- [ ] `pexels_client.py`: Convert all `aiohttp` → `httpx`, remove all `requests` calls
- [ ] `ai_content_generator.py`: Convert any remaining sync I/O, update Ollama check call sites
- [ ] Search for any remaining `requests` imports in entire codebase
- [ ] Verify all HTTP operations in routes are async

### Remaining 📝

- [ ] Test all converted clients with real API calls
- [ ] Verify error handling works correctly with httpx exceptions
- [ ] Update requirements.txt to include httpx, remove requests
- [ ] Document httpx patterns for future development

---

## 🔧 Migration Patterns

### Pattern 1: Simple GET Request

**BEFORE (Blocking with requests):**
```python
import requests

response = requests.get(url, headers=headers)
data = response.json()
```

**AFTER (Async with httpx):**
```python
import httpx

async with httpx.AsyncClient() as client:
    response = await client.get(url, headers=headers)
    data = response.json()
```

### Pattern 2: POST with Timeout

**BEFORE (Blocking with requests):**
```python
import requests

response = requests.post(
    url, 
    json=payload, 
    headers=headers,
    timeout=10
)
```

**AFTER (Async with httpx):**
```python
import httpx

async with httpx.AsyncClient(timeout=10) as client:
    response = await client.post(
        url,
        json=payload,
        headers=headers
    )
```

### Pattern 3: Reusable Client (with connection pooling)

**BEFORE (inefficient):**
```python
# Creates new connection each time!
def search(query):
    response = requests.get(f"https://api.example.com/search?q={query}")
    return response.json()
```

**AFTER (pooled, async):**
```python
class MyClient:
    def __init__(self):
        self.client = None
    
    async def init_async(self):
        """Call once at startup"""
        self.client = httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=5)
        )
    
    async def search(self, query):
        response = await self.client.get(
            f"https://api.example.com/search?q={query}"
        )
        return response.json()
    
    async def close(self):
        """Call at shutdown"""
        await self.client.aclose()
```

### Pattern 4: Client-Session Level (httpx)

**BEST PRACTICE (FastAPI integration):**
```python
# Shared client instance (in main.py or services/http_client.py)
_http_client = None

async def get_http_client():
    """Get or create shared httpx client"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=10)
        )
    return _http_client

# Use in routes/services:
async def my_endpoint(client: httpx.AsyncClient = Depends(get_http_client)):
    response = await client.get("https://api.example.com/data")
    return response.json()
```

---

## 📂 Files Requiring Migration

### serper_client.py (HIGH PRIORITY)

**Current State:** Uses `requests` (blocking)  
**Lines:** Multiple search methods  
**Action:** Replace `requests.post()` with `async with httpx.AsyncClient()`  

**Key methods to convert:**
- `search_web()` - line ~80
- `search_news()` - line ~130
- `search_shopping()` - line ~180

### pexels_client.py (HIGH PRIORITY)

**Current State:** Mixes `aiohttp` + `requests` (async + blocking)  
**Lines:** Multiple image search methods  
**Action:** Replace all with `httpx` async client  

**Key methods to convert:**
- `search_images()` - line ~50
- `get_photo_details()` - line ~120
- `download_image()` - line ~180

**Special notes:**
- Currently uses `aiohttp.ClientSession` - migrate all to `httpx`
- Remove all `requests` calls (if any)
- Ensure proper async context manager usage

### ai_content_generator.py (HIGH PRIORITY)

**Current State:** Now has `httpx` import, but Ollama check was sync  
**Lines:** ~56 (now async)  
**Action:** Ensure all callers await `_check_ollama_async()` before using Ollama  

**Key changes:**
- `_check_ollama_async()` now properly async ✅
- Call this once before first Ollama usage
- All Ollama API calls must use httpx async

### Task Routes (task_routes.py) (MEDIUM PRIORITY)

**Line 622-637:** Uses `aiohttp` for Ollama communication  
**Action:** Replace with `httpx`  

---

## 🔍 Search Commands (For Finding Remaining Issues)

**Find all remaining requests imports:**
```bash
grep -r "import requests" src/
grep -r "from requests" src/
```

**Find all HTTP operations that might be blocking:**
```bash
grep -r "\.get(" src/ --include="*.py"
grep -r "\.post(" src/ --include="*.py"
grep -r "\.put(" src/ --include="*.py"
grep -r "\.delete(" src/ --include="*.py"
grep -r "\.request(" src/ --include="*.py"
```

**Find aiohttp usage (migrate to httpx):**
```bash
grep -r "aiohttp" src/ --include="*.py"
```

---

## 🚀 Migration Order (Priority)

1. ✅ **Update imports** (serper_client, pexels_client, ai_content_generator)
2. 🔄 **Fix serper_client methods** - Web search is critical for content generation
3. 🔄 **Fix pexels_client methods** - Image search needed for blog posts
4. ⏳ **Fix task_routes.py** - Ollama communication
5. ⏳ **Audit entire codebase** - Catch any remaining sync I/O
6. ⏳ **Update requirements.txt** - Add httpx, remove requests
7. ⏳ **Test end-to-end** - Verify all HTTP operations work async

---

## 🧪 Testing Checklist

After each file migration:

- [ ] All methods are async (use `async def`)
- [ ] All HTTP calls use `await` with `httpx.AsyncClient()`
- [ ] No sync `requests` library calls remain
- [ ] No mixing of `aiohttp` and `httpx`
- [ ] Proper timeout handling
- [ ] Error handling with httpx exceptions (not requests)
- [ ] Connection pooling for performance
- [ ] Proper async context manager usage (`async with`)

---

## 📊 httpx vs requests Comparison

| Feature | requests | httpx |
|---------|----------|-------|
| **I/O Model** | Blocking | Async-first |
| **Use in FastAPI** | ❌ Blocks event loop | ✅ Proper async |
| **Performance** | ~100ms per request | <10ms per request (cached) |
| **Connection pooling** | Manual | Automatic |
| **API Design** | requests-like | requests-like (easy migration) |
| **Error types** | RequestException | HTTPError |
| **Timeout default** | None | No timeout (set explicitly) |

---

## 📝 Notes

- **httpx.AsyncClient** should be instantiated once and reused (lifecycle management)
- Always use `async with` for context manager safety
- Set explicit timeouts to avoid hanging requests
- Test with proper error scenarios (timeouts, connection errors, 5xx responses)
- Consider creating shared HTTP client via FastAPI dependency injection

---

**Progress:** Files modified: 3/7 | Estimated completion: 1-2 hours

