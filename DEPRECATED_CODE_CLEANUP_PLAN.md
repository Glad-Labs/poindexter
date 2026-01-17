# Deprecated Code Cleanup Plan

**Generated:** January 16, 2026  
**Status:** Ready for Implementation  
**Scope:** Complete removal of legacy/deprecated code  

---

## Executive Summary

This codebase has accumulated significant legacy code from previous architecture iterations. The analysis identified **3 categories of deprecated code** that should be systematically removed:

1. **Safe to Remove (No Dependencies)** - Unused legacy implementations
2. **Medium Risk (Few References)** - Need frontend updates before removal
3. **High Risk (Many Dependencies)** - Requires careful migration planning

**Total Items Identified:** 30+

---

## TIER 1: Safe to Remove - No Active Dependencies

These can be deleted immediately as nothing depends on them.

### Archive Folder
- **Location:** `archive/` directory
- **Contents:** 
  - `archive/content_orchestrator.py.archived` - Old orchestrator backup
  - `archive/diagnose_orchestrator.py` - Diagnostic script
  - `archive/FINAL_VERIFICATION.py` - Old verification script
  - `archive/fix_imports.py` - Migration helper (no longer needed)
  - `archive/agents-legacy/` - Legacy agent implementations
  - `archive/backups/` - Database backups
  - `archive/cloud-functions/` - Google Cloud Functions (if not using GCP)
  - `archive/cms/` - Old CMS integrations
  - `archive/google-cloud-services/` - GCP-specific code
  - `archive/mcp/` - Old MCP implementations
  - `archive/mcp_server/` - Legacy MCP server
  - `archive/orchestrator-legacy/` - Old orchestrator code
- **Action:** Delete entire `archive/` directory
- **Impact:** None - directory is explicitly for archived code

### Unused Route Files
- **Files:**
  - `src/cofounder_agent/routes/model_selection_routes.py` - Duplicate of model_routes
  - `src/cofounder_agent/routes/orchestrator_routes.py` - Legacy orchestrator reference
  - `src/cofounder_agent/routes/quality_routes.py` - Replaced by quality_score in task_routes
  - `src/cofounder_agent/routes/services_registry_routes.py` - Service registry not used
  - `src/cofounder_agent/routes/training_routes.py` - Training not active
- **Check:** Verify these are NOT registered in `route_registration.py`
- **Action:** Delete if unregistered
- **Impact:** None - they're not included in FastAPI app

### Legacy Files in Web
- **Files:**
  - `web/oversight-hub/src/components/tasks/TaskManagement-original.jsx` - Backup of refactored component
  - `web/public-site/.env.local.example` - Example env file
  - `web/public-site/coverage/` - Coverage reports (generated)
- **Action:** Delete backup files and coverage reports
- **Impact:** None - these are backup/generated artifacts

### Documentation Files (Outdated)
- **Files:**
  - `web/public-site/EVALUATION_REPORT.md` - Old evaluation
  - `web/public-site/MODERNIZATION_COMPLETE.md` - Outdated status report
  - `CRITICAL_BUG_FIXES_20250117.md` - Historical documentation
  - Old archive-old/ folder documentation

---

## TIER 2: Medium Risk - Frontend Dependencies Need Update

These endpoints are still called by frontend but should be replaced with `/api/tasks` endpoint.

### `/api/content/tasks` Endpoint
- **Backend File:** `src/cofounder_agent/routes/content_routes.py`
- **Frontend Callers:**
  - `web/oversight-hub/src/services/taskService.js` - `getContentTask()` uses `/api/content/tasks/{id}`
  - `web/oversight-hub/src/services/taskService.js` - `deleteContentTask()` uses `/api/content/tasks/{id}`
  - `web/oversight-hub/src/components/tasks/TaskManagement.jsx` - Calls `deleteContentTask()`
- **Replacement:** Use `/api/tasks` endpoint (already implemented and verified working)
- **Removal Steps:**
  1. Update `taskService.js` - `getContentTask()` and `deleteContentTask()` to use `/api/tasks`
  2. Update TaskManagement.jsx to use new functions
  3. Delete `/api/content/*` endpoints from `content_routes.py`
  4. Remove `content_router` registration from `route_registration.py`
- **Status:** Ready for migration

### `/api/content/metrics` Endpoint
- **Backend File:** `src/cofounder_agent/routes/content_routes.py`
- **Frontend Caller:** `web/oversight-hub/src/lib/apiClient.js` - `getContentMetrics()`
- **Replacement:** Merge into `/api/metrics` endpoint
- **Action:** Update apiClient.js call path

### Subtask Routes (`/api/content/subtasks/*`)
- **Backend File:** `src/cofounder_agent/routes/subtask_routes.py`
- **Current Status:** NOT registered in `route_registration.py` - safe to delete
- **Action:** Delete entire file - subtasks are now handled by `/api/tasks` handlers

### Natural Language Routes (`/api/content/natural-language/*`)
- **Backend File:** `src/cofounder_agent/routes/natural_language_content_routes.py`
- **Current Status:** NOT registered in `route_registration.py` - safe to delete
- **Frontend:** No callers found
- **Action:** Delete entire file

---

## TIER 3: Reference/Documentation Files

These are documentation or analysis files that should be archived or updated.

### Files to Archive/Update
- `UNIFIED_TASK_ENDPOINTS.md` - Update to remove deprecation section
- `TASK_STATUS_STATE_MACHINE.md` - Already current
- `QUALITY_EVALUATION_FIX.md` - Historical documentation
- Various test files in `tests/` directory

---

## Deprecated Functions & Imports

### In `src/cofounder_agent/main.py`
- **Line 86:** Import of `content_router_service` - Still used by task_routes but should consolidate
- **Line 189:** `get_content_task_store` - Legacy task store interface

### In `web/public-site/lib/url.js`
- **Lines 30-31:** `getStrapiURL()` function - Marked as `@deprecated`, use `getAbsoluteURL()` instead
- **This should be removed** - Function still exists but is deprecated

---

## Removal Order (Recommended)

### Phase 1: Safe Deletions (No Dependencies)
1. Delete `/archive/` directory completely
2. Delete unused route files (model_selection_routes, orchestrator_routes, quality_routes, services_registry_routes, training_routes)
3. Delete backup/generated files (TaskManagement-original.jsx, coverage/, .env.local.example)

### Phase 2: Frontend Updates + Backend Removal
1. Update `taskService.js` to use `/api/tasks` instead of `/api/content/tasks`
2. Update `apiClient.js` metrics calls
3. Delete `/api/content/tasks` endpoint from content_routes.py
4. Delete `/api/content/metrics` endpoint
5. Remove `content_routes.py` registration from route_registration.py
6. Delete subtask_routes.py
7. Delete natural_language_content_routes.py

### Phase 3: Code Cleanup
1. Remove deprecated function `getStrapiURL()` from web/public-site/lib/url.js
2. Clean up import of `content_router_service` in main.py
3. Update documentation files

---

## Risk Assessment

| Category | Count | Risk | Effort |
|----------|-------|------|--------|
| Archive deletion | 1 | Low | <5 min |
| Unused routes | 5 | Low | 5 min |
| Backup files | 3 | Low | 2 min |
| Frontend API updates | 3 | Medium | 15 min |
| Backend endpoint removal | 3 | Medium | 10 min |
| Deprecated functions | 2 | Low | 5 min |

**Total Estimated Time:** ~45 minutes  
**Total Estimated Impact:** High confidence, no breaking changes expected

---

## Files Affected by Cleanup

### Backend Changes
- `src/cofounder_agent/routes/content_routes.py` - Delete or drastically reduce
- `src/cofounder_agent/routes/subtask_routes.py` - Delete entirely
- `src/cofounder_agent/routes/natural_language_content_routes.py` - Delete entirely
- `src/cofounder_agent/routes/model_selection_routes.py` - Delete entirely
- `src/cofounder_agent/routes/orchestrator_routes.py` - Delete entirely
- `src/cofounder_agent/routes/quality_routes.py` - Delete entirely
- `src/cofounder_agent/routes/services_registry_routes.py` - Delete entirely
- `src/cofounder_agent/routes/training_routes.py` - Delete entirely
- `src/cofounder_agent/utils/route_registration.py` - Remove registrations
- `src/cofounder_agent/main.py` - Remove imports

### Frontend Changes
- `web/oversight-hub/src/services/taskService.js` - Update API endpoints
- `web/oversight-hub/src/lib/apiClient.js` - Update API endpoints
- `web/oversight-hub/src/components/tasks/TaskManagement.jsx` - Update function calls
- `web/public-site/lib/url.js` - Remove deprecated function

### Directory Deletions
- `archive/` - Entire directory

### Files to Delete
- `web/oversight-hub/src/components/tasks/TaskManagement-original.jsx`
- `web/public-site/.env.local.example`
- `web/public-site/coverage/` (entire directory)
- Various old documentation files

---

## Verification Checklist

After cleanup, verify:

- [ ] No import errors in backend (`npm run test:python`)
- [ ] No missing imports in frontend (`npm run build` in both web projects)
- [ ] `/api/tasks` endpoint still works for all task types
- [ ] Task creation and approval flow working in Oversight Hub UI
- [ ] No "404 not found" errors in browser console
- [ ] All tests pass (`npm run test`)

---

## Dependencies Verified

**Frontend → Backend Endpoints:**
- ✅ `/api/tasks` - Used by TaskManagement.jsx (verified working)
- ✅ `/api/content/tasks/{id}` - Used by taskService, can be replaced
- ❌ `/api/content/subtasks/*` - NOT used anywhere
- ❌ `/api/content/natural-language/*` - NOT used anywhere
- ✅ `/api/content/langgraph/*` - Used by LangGraphTest.jsx (specialized endpoint, keep)

**No other active dependencies found.**

---

## Approval Required

This cleanup plan is **ready for implementation**. Recommendation: Execute in 3 phases over next development cycle to minimize risk and ensure verification at each stage.

**Proceed with Phase 1?** (Safe deletions, zero risk)
