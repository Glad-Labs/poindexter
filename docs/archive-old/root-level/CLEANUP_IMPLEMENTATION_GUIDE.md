# Legacy Code Cleanup & Pipeline Gap Implementation Guide

**Status:** Ready for Execution  
**Priority:** HIGH (fixes breaking imports, removes dead code)  
**Estimated Time:** 2-3 hours  
**Risk Level:** Low (mostly deletions and comment cleanup)

---

## 🚀 Phase 1: Fix Breaking Imports (CRITICAL)

### Issue: Strapi MCP Server References

**Problem:** Code tries to import non-existent `strapi_server.py`

```python
# src/mcp/test_mcp.py:74
from src.mcp.servers.strapi_server import StrapiMCPServer  # ❌ FILE DOESN'T EXIST
```

**Impact:** If `/test_mcp()` or `/mcp_orchestrator()` are called, ImportError crashes

---

### Fix 1.1: Remove Strapi Server Test Function

**File:** `src/mcp/test_mcp.py`

**Current Code (Lines 69-97):**

```python
async def test_strapi_server():
    """Test the Strapi Server"""
    print("\n=== Testing Strapi Server ===")
    try:
        from src.mcp.servers.strapi_server import StrapiMCPServer

        server = StrapiMCPServer()
        stats = await server.health_check()

        if stats.get('status') == 'healthy':
            print("   ✅ Strapi connection successful")
            return True
        else:
            print(f"   ❌ Strapi connection failed: {stats.get('error')}")
            return False

    except Exception as e:
        print("\n✅ Strapi Server test completed")
        return True

    except Exception as e:
        print(f"❌ Strapi Server test failed: {e}")
        return False
```

**Action:** Delete entire function (lines 69-97)

**Also remove:** References to `test_strapi_server()` in main test function (around line 218)

---

### Fix 1.2: Remove Strapi Server from MCP Orchestrator

**File:** `src/mcp/mcp_orchestrator.py`

**Current Code (Lines 45-53):**

```python
# Register Strapi Server
try:
    from mcp.servers.strapi_server import StrapiMCPServer

    strapi_server = StrapiMCPServer()
    await self.mcp_manager.register_server("strapi-cms", strapi_server)
except Exception as e:
    # ... error handling
```

**Action:** Delete this try/except block entirely

---

### Fix 1.3: Remove Strapi Server from Client Manager

**File:** `src/mcp/client_manager.py`

**Current Code (Lines 339-347):**

```python
from .servers.strapi_server import StrapiMCPServer

# Register Strapi Server
strapi_server = StrapiMCPServer()
await manager.register_server("strapi-cms", strapi_server)
```

**Action:** Delete these lines

Also remove any references to:

- `STRAPI_TOOLS` attribute checks
- `STRAPI_RESOURCES` attribute checks

---

### Fix 1.4: Remove Strapi from client_manager.py Helper Methods

**File:** `src/mcp/client_manager.py`

**Search for and remove:**

```python
if hasattr(self.server_instance, 'STRAPI_TOOLS'):
    tools.extend(getattr(self.server_instance, 'STRAPI_TOOLS', []))

if hasattr(self.server_instance, 'STRAPI_RESOURCES'):
    resources.extend(getattr(self.server_instance, 'STRAPI_RESOURCES', []))
```

---

## 🗑️ Phase 2: Remove Unused Auth Files

### Issue: Duplicate Authentication Implementations

**Problem:** 3 separate auth modules, only 1 is used

**Files to DELETE:**

- ❌ `src/cofounder_agent/routes/auth.py`
- ❌ `src/cofounder_agent/routes/auth_routes.py`

**File to KEEP:**

- ✅ `src/cofounder_agent/routes/auth_unified.py` (currently used)

### Why:

- main.py only imports `auth_unified.py`
- auth.py and auth_routes.py are dead code
- auth_unified.py handles all auth: JWT, OAuth, GitHub

### Action Steps:

1. **Verify auth_unified.py has all needed endpoints:**
   - ✅ POST /api/auth/logout
   - ✅ GET /api/auth/me
   - ✅ GitHub OAuth callback

2. **Search for any imports of auth.py or auth_routes.py:**

   ```bash
   grep -r "from routes.auth import" src/
   grep -r "from routes.auth_routes import" src/
   grep -r "import auth_routes" src/
   ```

3. **Confirm they return nothing** (no other files import them)

4. **Delete the files:**
   ```bash
   rm src/cofounder_agent/routes/auth.py
   rm src/cofounder_agent/routes/auth_routes.py
   ```

---

## 🧹 Phase 3: Remove Unused Configuration

### Issue: Pub/Sub Config Never Used

**File:** `src/agents/content_agent/config.py`

**Lines 117-121 to DELETE:**

```python
# --- Google Cloud Pub/Sub Configuration ---
self.PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC", "agent-commands")
self.PUBSUB_SUBSCRIPTION = os.getenv(
    "PUBSUB_SUBSCRIPTION", "content-agent-subscription"
)
```

### Verification:

```bash
# Search for usage
grep -r "PUBSUB_TOPIC" src/
grep -r "PUBSUB_SUBSCRIPTION" src/
# Should return 0 results (only in config.py)
```

---

## 🧹 Phase 4: Remove Google Cloud Status Messages

### Issue: Status endpoint references non-existent services

**File:** `src/cofounder_agent/orchestrator_logic.py`

**Line 3 - UPDATE docstring:**

```python
# FROM:
Updated with PostgreSQL database and API-based command queue
(Firestore and Pub/Sub have been migrated to PostgreSQL and REST API endpoints)

# TO:
Updated with PostgreSQL database and REST API-based command queue
```

**Lines 322, 329-331 - DELETE:**

```python
# DELETE this line:
status_message += f"☁️  Google Cloud: Firestore {'✓'...}, Pub/Sub {'✓'...}

# DELETE these lines:
if 'firestore_health' in status_data:
    firestore_status = status_data['firestore_health'].get('status', 'unknown')
    status_message += f"🗄️  Firestore: {firestore_status}\n"
```

---

## 🧹 Phase 5: Update Main Docstring

**File:** `src/cofounder_agent/main.py`

**Line 5 - UPDATE docstring:**

```python
# FROM:
description="Central orchestrator for Glad Labs AI-driven business operations with Google Cloud integration",

# TO:
description="Central orchestrator for Glad Labs AI-driven business operations with PostgreSQL backend",
```

---

## ⚠️ Phase 6: Check Unused Route Files (Manual Review)

### Potentially Unused Files

**File 1:** `src/cofounder_agent/routes/workflows.py`

**Question:** Is this different from `workflow_history.py`?

```bash
# Check what's in each
wc -l src/cofounder_agent/routes/workflows.py
wc -l src/cofounder_agent/routes/workflow_history.py

# Check if workflows.py is imported anywhere
grep -r "from routes.workflows import" src/
```

**Decision:**

- If NOT imported → DELETE
- If similar to workflow_history → CONSOLIDATE

---

**File 2:** `src/cofounder_agent/routes/bulk_task_routes.py`

**Question:** What bulk operations exist?

```bash
# Check if imported
grep -r "bulk_task_routes" src/cofounder_agent/

# Check if endpoint exists in main.py
grep "bulk_task_routes" src/cofounder_agent/main.py
```

**Decision:**

- If NOT registered in main.py → DELETE
- If needed → Document use case

---

## ✅ Phase 7: Verify All Pipelines Still Work

### After Cleanup, Test:

```bash
# 1. Test config loading
python -c "
import sys
sys.path.insert(0, 'src')
from agents.content_agent.config import config
print('Config loads: OK')
"

# 2. Test auth module
python -c "
import sys
sys.path.insert(0, 'src')
from cofounder_agent.routes.auth_unified import router
print('Auth unified loads: OK')
"

# 3. Test content orchestrator
python -c "
import sys
sys.path.insert(0, 'src')
from cofounder_agent.services.content_orchestrator import ContentOrchestrator
print('Content orchestrator loads: OK')
"

# 4. Test MCP without Strapi
python -c "
import sys
sys.path.insert(0, 'src')
from mcp.client_manager import ClientManager
print('MCP client manager loads: OK')
"

# 5. Start the FastAPI app and check health
curl http://localhost:8000/api/health
# Expected: 200 OK with status info
```

---

## 🚀 Implementation Checklist

### Before Starting

- [ ] Create backup of current code (git commit)
- [ ] Verify you're on the feat/refine branch
- [ ] Have all cleanup recommendations reviewed

### Phase 1: Fix Breaking Imports

- [ ] Remove test_strapi_server() from test_mcp.py
- [ ] Remove Strapi registration from mcp_orchestrator.py
- [ ] Remove Strapi import from client_manager.py
- [ ] Remove STRAPI_TOOLS/STRAPI_RESOURCES checks
- [ ] Verify MCP client manager loads without errors

### Phase 2: Remove Auth Files

- [ ] Verify auth_unified.py is complete
- [ ] Search for any imports of deleted files
- [ ] Delete auth.py
- [ ] Delete auth_routes.py
- [ ] Test auth_unified imports

### Phase 3: Remove Pub/Sub Config

- [ ] Search for PUBSUB\_ usage
- [ ] Delete lines 117-121 from config.py
- [ ] Test config loads without errors

### Phase 4: Remove GCP Status Messages

- [ ] Update line 3 docstring
- [ ] Delete lines 322, 329-331
- [ ] Test orchestrator loads

### Phase 5: Update Docstrings

- [ ] Update main.py line 5
- [ ] Update any other GCP references

### Phase 6: Manual Review

- [ ] Decide: workflows.py - keep or delete?
- [ ] Decide: bulk_task_routes.py - keep or delete?
- [ ] Document decisions

### Phase 7: Test Everything

- [ ] Run all test commands above
- [ ] Test each pipeline manually
- [ ] Run test suite (pytest)

### Final Steps

- [ ] Git commit with message: `refactor: remove legacy code and strapi mcp references`
- [ ] Create PR for code review
- [ ] Test on staging before merge

---

## 📝 Git Workflow

```bash
# 1. Create feature branch
git checkout -b feat/cleanup-legacy-code

# 2. Make changes (follow checklist above)
# 3. Verify everything loads
# 4. Run tests
# 5. Commit changes
git add .
git commit -m "refactor: remove legacy Strapi MCP, duplicate auth, and GCP references

Removed:
- Strapi MCP server imports (non-existent file)
- auth.py and auth_routes.py (superseded by auth_unified.py)
- Pub/Sub configuration (unused)
- Google Cloud status messages (services removed)

Verified:
- Config loads without errors
- All active pipelines still functional
- No breaking changes to API
"

# 6. Push and create PR
git push origin feat/cleanup-legacy-code
```

---

## 🚨 Common Issues & Fixes

### Issue: "ModuleNotFoundError: No module named 'strapi_server'"

**Cause:** Strapi MCP references not fully removed

**Fix:**

```bash
# Find remaining references
grep -r "strapi_server" src/
grep -r "StrapiMCPServer" src/

# Delete the lines
```

---

### Issue: "ImportError: cannot import name 'router' from auth_routes"

**Cause:** Some code still trying to import deleted auth file

**Fix:**

```bash
# Find the import
grep -r "from routes.auth_routes import" src/

# Delete or update the import
```

---

### Issue: Content generation pipeline fails after cleanup

**Cause:** Accidentally deleted something critical

**Fix:**

```bash
# Rollback the commit
git revert <commit-hash>

# Review what was deleted
git show <commit-hash>

# Re-apply carefully
```

---

## 📊 Expected Results After Cleanup

### Code Quality Metrics

| Metric                         | Before | After |
| ------------------------------ | ------ | ----- |
| Unused files                   | 5+     | 0     |
| Duplicate auth implementations | 3      | 1     |
| Google Cloud references        | 10+    | 0     |
| Strapi MCP imports             | 3      | 0     |
| Lines of dead code             | ~200   | 0     |

### Functionality

| Feature            | Status                   |
| ------------------ | ------------------------ |
| Content generation | ✅ Still works           |
| Task management    | ✅ Still works           |
| Authentication     | ✅ Still works (unified) |
| CMS endpoints      | ✅ Still works           |
| Model routing      | ✅ Still works           |
| MCP integration    | ✅ Simplified            |

---

## 📚 Files Changed Summary

```
Deleted Files:
  - src/cofounder_agent/routes/auth.py (delete)
  - src/cofounder_agent/routes/auth_routes.py (delete)

Modified Files:
  - src/cofounder_agent/routes/workflows.py (possibly delete)
  - src/cofounder_agent/routes/bulk_task_routes.py (possibly delete)
  - src/mcp/test_mcp.py (remove Strapi test)
  - src/mcp/mcp_orchestrator.py (remove Strapi registration)
  - src/mcp/client_manager.py (remove Strapi initialization)
  - src/cofounder_agent/orchestrator_logic.py (remove GCP references)
  - src/agents/content_agent/config.py (remove Pub/Sub config)
  - src/cofounder_agent/main.py (update docstring)

Total Changes: ~8-10 files
Estimated Lines Removed: ~200-300 lines
```

---

**Status:** Ready to execute  
**Risk:** Low (mostly deletions)  
**Rollback:** Simple (git revert)  
**Testing:** Automated + manual verification

Ready to implement? Run the checklist above in order! 🚀
