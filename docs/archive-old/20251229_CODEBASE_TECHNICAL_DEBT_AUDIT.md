# Codebase Technical Debt & Stubbed Code Audit

**Date:** December 27, 2025  
**Scope:** Full repository scan for TODOs, mock data, stubs, and incomplete implementations  
**Status:** Comprehensive audit completed

---

## Executive Summary

The codebase contains **significant technical debt** concentrated in three areas:

1. **Mock Data & Stub Implementations** (15 locations) - Development placeholders not completed
2. **Database Schema Issues** (4 critical locations) - Query methods don't exist, using mock data fallbacks
3. **Unimplemented Features** (12 locations) - Endpoints and services defined but not functional
4. **Placeholder Calculations** (6 locations) - Using default/hardcoded values instead of real computation

**Total Issues:** 37 critical/high priority items  
**Estimated Effort:** 40-60 hours to resolve  
**Risk Level:** Medium (affects analytics, training, and export features)

---

## Critical Issues (Must Fix)

### 1. Analytics Routes: KPI Endpoint Mock Data

**File:** [src/cofounder_agent/routes/analytics_routes.py](src/cofounder_agent/routes/analytics_routes.py#L130-L138)  
**Severity:** 🔴 CRITICAL  
**Issue:** The KPI metrics endpoint is using empty mock data instead of querying the database.

```python
# Line 130-138
# Use mock data for now - database query causing issues
# TODO: Fix database schema and implement proper query
logger.debug(f"  ℹ️  Using mock task data for analytics")
tasks = []  # Empty list - will be populated with mock data below
```

**Impact:**

- Executive Dashboard shows 0 metrics (no task data)
- KPI calculations return empty results
- Cannot track system performance metrics

**Root Cause:** `DatabaseService` doesn't have a `.query()` method. Original code called `await db.query(...)` which failed.

**Fix Required:**

- Implement proper task query in DatabaseService using SQLAlchemy ORM
- Replace mock data with real database queries
- Consider if the tasks should come from `tasks` table or `content_tasks` table

**Effort:** 3-4 hours

---

### 2. Constraint Utilities: Placeholder Implementations (3 functions)

**File:** [src/cofounder_agent/utils/constraint_utils.py](src/cofounder_agent/utils/constraint_utils.py#L410-L470)  
**Severity:** 🔴 CRITICAL  
**Functions:**

| Function                              | Lines    | Issue                                            | Impact                         |
| ------------------------------------- | -------- | ------------------------------------------------ | ------------------------------ |
| `expand_content_to_word_count()`      | 410-437  | Always returns content unchanged; doesn't expand | Word count constraints ignored |
| `analyze_style_consistency()`         | 450-470+ | Placeholder scoring (0.8 hardcoded)              | Style validation not working   |
| `score_accuracy()` in quality_service | 410-417  | Returns generic scores based on citations        | Cannot validate fact accuracy  |

**Code Example:**

```python
# Line 435: Just logs and returns unchanged content
logger.warning("NOTE: Actual expansion requires LLM call - implement with model_router")
return content  # DOES NOT EXPAND!
```

**Fix Required:**

- Call `model_router` to generate expanded content using LLM
- Implement proper style analysis using NLP or LLM embeddings
- Add fact-checking logic (or API integration)

**Effort:** 5-6 hours per function

---

### 3. Orchestrator Routes: 4 Unimplemented Endpoints

**File:** [src/cofounder_agent/routes/orchestrator_routes.py](src/cofounder_agent/routes/orchestrator_routes.py#L230-L325)  
**Severity:** 🔴 CRITICAL  
**Endpoints:**

| Endpoint                | Route                                               | Status               | TODOs                                                      |
| ----------------------- | --------------------------------------------------- | -------------------- | ---------------------------------------------------------- |
| Export Training Data    | `POST /api/orchestrator/training-data/export`       | Returns empty        | Implement data filtering, format conversion, download URL  |
| Upload Fine-Tuned Model | `POST /api/orchestrator/training-data/upload-model` | Returns success stub | Implement model registration, validation, database storage |
| Get Learning Patterns   | `GET /api/orchestrator/learning-patterns`           | Returns empty arrays | Extract patterns from history, calculate success rate      |
| MCP Tool Discovery      | `POST /api/orchestrator/mcp/discover`               | Line 348 TODO        | Implement tool enumeration and registration                |

**Code Examples:**

```python
# Line 235-243: Training export returns empty data
# TODO: Implement training data export from database
return {
    "count": 0,  # TODO: Implement
    "download_url": "/api/orchestrator/training-data/download/latest",
}

# Line 243, 302: Success rate hardcoded to 0.0
"success_rate": 0.0,  # TODO
```

**Fix Required:**

- Implement database queries for each endpoint
- Add validation and error handling
- Wire up file download/upload mechanisms
- Connect to actual orchestrator execution history

**Effort:** 8-10 hours total

---

## High Priority Issues

### 4. Quality Service: LLM-Based Evaluation Not Implemented

**File:** [src/cofounder_agent/services/quality_service.py](src/cofounder_agent/services/quality_service.py#L350-L390)  
**Severity:** 🟠 HIGH  
**Functions:**

- `_evaluate_llm_based()` - Line 360: TODO comment, falls back to pattern-based
- `_evaluate_hybrid()` - Line 384: Combines pattern+LLM but never uses LLM result

```python
# Line 360
# TODO: Implement LLM-based evaluation
# This would use model_router to call an LLM for scoring
# Fallback to pattern-based for now
return await self._evaluate_pattern_based(content, context)

# Line 384: LLM result computed but ignored
if self.model_router:
    llm_assessment = await self._evaluate_llm_based(content, context)
    # TODO: Combine assessments with weighting (e.g., 60/40 split)
# Returns only pattern-based!
```

**Impact:**

- Quality evaluation relies on heuristics only
- Cannot leverage LLM for semantic quality assessment
- Hybrid evaluation doesn't work

**Fix Required:**

- Implement LLM prompt for quality scoring
- Parse LLM response and convert to QualityAssessment
- Implement 60/40 weighting between pattern and LLM
- Test both fallback modes

**Effort:** 4-5 hours

---

### 5. Fine-Tuning Service: Placeholder Google Gemini Implementation

**File:** [src/cofounder_agent/services/fine_tuning_service.py](src/cofounder_agent/services/fine_tuning_service.py#L170-L195)  
**Severity:** 🟠 HIGH  
**Issue:**

```python
# Line 179
operation = genai.types.Operation()
# Note: Actual fine-tuning depends on Google's API
# This is a placeholder for the actual implementation

self.jobs[job_id] = {
    "operation": None,  # Would store actual operation - NEVER COMPLETED
}
```

**Impact:**

- Gemini fine-tuning appears to work but doesn't
- Jobs are tracked but never completed
- No way to monitor fine-tuning progress

**Fix Required:**

- Implement actual Google Gemini fine-tuning API calls
- Add job monitoring and status updates
- Store operation object and handle completion callbacks

**Effort:** 6-8 hours

---

## Medium Priority Issues

### 6. Email Publisher: Newsletter Service Placeholder

**File:** [src/cofounder_agent/services/email_publisher.py](src/cofounder_agent/services/email_publisher.py#L160-L175)  
**Severity:** 🟡 MEDIUM  
**Issue:**

```python
# Line 170
Note: This is a placeholder that would integrate with newsletter services
like ConvertKit, Substack, or custom mailing list databases
```

**Impact:**

- Newsletter sending not actually implemented
- Returns success but doesn't send emails

**Fix Required:**

- Choose and integrate with newsletter service (ConvertKit, SendGrid, etc.)
- Implement subscriber list management
- Add email template handling

**Effort:** 5-6 hours

---

### 7. Settings Routes: Multiple Mock Implementations

**File:** [src/cofounder_agent/routes/settings_routes.py](src/cofounder_agent/routes/settings_routes.py)  
**Severity:** 🟡 MEDIUM  
**Locations:** 7 endpoints (lines 127, 194, 273, 326, 359, 411, 490, 551, 596, 660, 703)

```python
# Line 127: Get Settings
# Mock implementation for testing
mock_settings = [...]
return SettingsList(items=mock_settings)

# Line 551-555: Settings History
# Return empty history list (or mock with a few entries)
```

**Issue:** Every settings endpoint is mocked with hardcoded return values.

**Impact:**

- Settings are not persisted
- Cannot save/load application configuration
- Each restart loses all configuration

**Fix Required:**

- Implement database persistence using DatabaseService
- Create settings schema table if not exists
- Wire up CRUD operations

**Effort:** 6-8 hours

---

### 8. Authentication: Mock Token Generation for Development

**File:** [src/cofounder_agent/routes/auth_unified.py](src/cofounder_agent/routes/auth_unified.py#L51-L85)  
**Severity:** 🟡 MEDIUM  
**Issue:**

```python
# Line 51-54: Handle mock auth codes
if code.startswith("mock_auth_code_"):
    logger.info("Mock auth code detected, returning mock token")
    return "mock_github_token_dev"

# Line 84: Handle mock tokens
if access_token == "mock_github_token_dev":
    logger.info("Mock token detected, returning mock user data")
```

**Status:** ⚠️ Development only (acceptable for Tier 1 local development)  
**Risk:** Should never reach production

**Mitigation:**

- Add environment checks to prevent mock auth in production
- Add warning logs if mock auth detected in non-dev environments

**Effort:** 1-2 hours

---

### 9. Image Service: Placeholder Image Optimization

**File:** [src/cofounder_agent/services/image_service.py](src/cofounder_agent/services/image_service.py#L751-L757)  
**Severity:** 🟡 MEDIUM  
**Issue:**

```python
# Line 751-757
# Placeholder for future image optimization
logger.info(f"Image optimization placeholder for {image_url}")
return {
    "url": image_url,
    "note": "Image optimization not yet implemented",
}
```

**Impact:**

- Images are not optimized for web (file size, format)
- Affects page load performance

**Fix Required:**

- Implement image compression (Sharp, Pillow, or service)
- Add WebP conversion
- Cache optimized versions

**Effort:** 4-5 hours

---

## Low Priority Issues

### 10. Constraint Display: Placeholder Expansion

**File:** [src/cofounder_agent/utils/constraint_utils.py](src/cofounder_agent/utils/constraint_utils.py#L414, #L435)  
**Severity:** 🟢 LOW  
**Note:** "This is a placeholder. Real implementation would call LLM to expand."

---

### 11. Frontend: Mock Dashboard Data

**File:** [web/oversight-hub/src/components/pages/ExecutiveDashboard.jsx](web/oversight-hub/src/components/pages/ExecutiveDashboard.jsx#L54-L64)  
**Severity:** 🟢 LOW  
**Issue:**

```javascript
// Line 54-55
// Set mock data for development
setDashboardData(getMockDashboardData());

// Line 64-75
const getMockDashboardData = () => ({...})
```

**Status:** Acceptable for development but should be removed before production.

---

### 12. Frontend: Token Refresh Not Implemented

**File:** [web/oversight-hub/src/services/cofounderAgentClient.js](web/oversight-hub/src/services/cofounderAgentClient.js#L157)  
**Severity:** 🟢 LOW  
**Issue:**

```javascript
// Line 157
'⚠️ Token refresh not implemented - auth flow should prevent 401s';
```

**Status:** Current auth design prevents 401s, so not critical.

---

## Database Schema Issues

### 13. Task Query Method Missing from DatabaseService

**File:** Unknown (DatabaseService implementation)  
**Severity:** 🔴 CRITICAL  
**Issue:** Code calls `db.query()` but method doesn't exist.

**Locations:**

- [analytics_routes.py](src/cofounder_agent/routes/analytics_routes.py#L134) - Uses mock data instead
- [orchestrator_routes.py](src/cofounder_agent/routes/orchestrator_routes.py#L262+) - Would need query

**Fix Required:**

- Add `query()` method to DatabaseService or use ORM methods
- Define proper return types for task queries
- Implement proper async database access

**Effort:** 2-3 hours

---

### 14. Task Status Tracking Not Implemented

**File:** Multiple (content_routes.py, task_routes.py)  
**Severity:** 🔴 CRITICAL  
**Issue:** Tasks don't properly track status transitions.

**Status Fields Missing:**

- created → queued → processing → completed/failed lifecycle
- No database persistence of task status
- No way to query tasks by status

**Fix Required:**

- Define task status schema
- Add status update methods
- Implement query filters by status

**Effort:** 3-4 hours

---

## Hardcoded/Placeholder Values

### 15. Image Fallback Handler: Placeholder URLs

**File:** [src/cofounder_agent/services/image_fallback_handler.py](src/cofounder_agent/services/image_fallback_handler.py#L316, #L322)  
**Severity:** 🟢 LOW  
**Using:** `https://via.placeholder.com/1200x800?text={prompt}`

**Status:** Acceptable fallback but should be replaced with real generation for production.

---

### 16. Cost Calculation: Hardcoded Placeholders

**File:** [src/cofounder_agent/main.py](src/cofounder_agent/main.py#L1074, #L1086)  
**Severity:** 🟠 HIGH  
**Code:**

```python
# Line 1074: Blog post cost
cost = 0.03  # Placeholder

# Line 1086: Image generation cost
cost = 0.02  # Placeholder
```

**Impact:** Cost tracking inaccurate, cannot charge users correctly.

**Fix Required:**

- Calculate based on actual model API costs
- Use model_router to get real pricing
- Track actual token usage

**Effort:** 2-3 hours

---

## Frontend Deprecation Warnings

### 17. MUI Grid v1→v2 Migration - Remaining Components

**File:** Multiple React components  
**Severity:** 🟡 MEDIUM  
**Remaining Issues:**

- [ConstraintComplianceDisplay.jsx](web/oversight-hub/src/components/ConstraintComplianceDisplay.jsx) - Multiple Grid components with deprecated props
- [BlogPostCreator.jsx](web/oversight-hub/src/components/tasks/BlogPostCreator.jsx) - Multiple Grid components

**Status:** ModelSelectionPanel.jsx already fixed (12/27)

**Fix Required:**

- Convert deprecated Grid props: `item xs={12} sm={6} md={4}` → `size={{xs: 12, sm: 6, md: 4}}`
- Test each component after migration

**Effort:** 2-3 hours

---

## Summary Table

| Category                 | Count | Severity    | Effort | Status        |
| ------------------------ | ----- | ----------- | ------ | ------------- |
| Mock Data Returns        | 7     | 🔴 Critical | 15-20h | Not Started   |
| Unimplemented Features   | 4     | 🔴 Critical | 15-20h | Not Started   |
| Database Issues          | 2     | 🔴 Critical | 5-7h   | In Progress\* |
| Placeholder Calculations | 2     | 🟠 High     | 5-8h   | Not Started   |
| Service Stubs            | 4     | 🟠 High     | 15-20h | Not Started   |
| Frontend Cleanup         | 3     | 🟡 Medium   | 5-7h   | Partial       |
| Dev-Only Code            | 2     | 🟢 Low      | 1-2h   | Acceptable    |

**Total Effort:** 61-84 hours

---

## Recommended Fix Priority

### Phase 1: Critical Infrastructure (16-20 hours)

1. Fix analytics_routes.py KPI mock data → implement real database queries
2. Implement DatabaseService.query() method
3. Fix task status tracking persistence
4. Fix cost calculation placeholders

### Phase 2: Core Features (20-25 hours)

5. Implement orchestrator endpoints (training export, model upload, learning patterns)
6. Implement LLM-based quality evaluation
7. Implement settings persistence

### Phase 3: Polish & Completeness (15-20 hours)

8. Implement email/newsletter publishing
9. Implement fine-tuning completion tracking
10. Complete MUI Grid v1→v2 migration
11. Implement constraint expansion with LLM

### Phase 4: Optimization (10-14 hours)

12. Implement image optimization
13. Add production safety checks for mock auth
14. Performance optimization for analytics queries

---

## Files With Most Issues

| File                                                                          | Issues | Severity |
| ----------------------------------------------------------------------------- | ------ | -------- |
| [orchestrator_routes.py](src/cofounder_agent/routes/orchestrator_routes.py)   | 4      | 🔴🔴     |
| [analytics_routes.py](src/cofounder_agent/routes/analytics_routes.py)         | 1      | 🔴       |
| [settings_routes.py](src/cofounder_agent/routes/settings_routes.py)           | 7      | 🟡       |
| [quality_service.py](src/cofounder_agent/services/quality_service.py)         | 2      | 🟠       |
| [constraint_utils.py](src/cofounder_agent/utils/constraint_utils.py)          | 3      | 🔴       |
| [fine_tuning_service.py](src/cofounder_agent/services/fine_tuning_service.py) | 1      | 🟠       |

---

## Recommendations

### Immediate Actions (Next Sprint)

1. ✅ Remove analytics_routes.py mock data - this blocks dashboard
2. ✅ Fix DatabaseService to support task queries
3. ✅ Implement task status persistence
4. ✅ Add production guard for mock authentication

### Next Actions (2 Sprints)

5. Implement orchestrator endpoints
6. Add LLM-based quality evaluation
7. Implement settings persistence

### Backlog (Future)

8. Email publishing integration
9. Fine-tuning completion tracking
10. Image optimization
11. Advanced constraint features

---

## Notes

- **Mock Data vs Stubs:** Most TODOs are stub implementations (return empty/placeholder data) rather than actual mock objects for testing
- **Development-Only Code:** Some placeholders (auth tokens, frontend mock data) are acceptable for Tier 1 local development
- **Database Schema:** Critical blocker - need to define task/content_task schema before analytics queries work
- **LLM Integration:** Several features require model_router integration that's partially implemented

---

_Report Generated: 2025-12-27_  
_Audit Scope: Complete codebase including src/, tests/, and web/_  
_Excluded: node_modules/, archive/, build outputs_
