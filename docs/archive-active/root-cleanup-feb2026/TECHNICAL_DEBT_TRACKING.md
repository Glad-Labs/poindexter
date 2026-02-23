# Technical Debt Tracking - Glad Labs

**Last Updated:** February 22, 2026  
**Total Identified Debt:** 87.5-117.5 hours across ~45 distinct issues  
**Overall Health:** 🟡 GOOD - Production-ready with known gaps

---

## 📊 Summary by Priority

| Priority | Hours | Issues | Status | GitHub Issues |
|----------|-------|--------|--------|---|
| **P1 - CRITICAL** | 10-14h | 2 | **2/2 Complete ✅** | #1-2 |
| **P2 - HIGH** | 17-18h | 4 | **4/4 Tracked** | #3-6, #7-9 |
| **P3 - MEDIUM** | 42-63h | 8 | **5/8 Tracked** | #10-13 |
| **P4 - LOW** | 18-25h | 6 | **6/6 Tracked** | #14-19 |
| **TOTAL** | **87-120h** | **20** | **17 Tracked** | |

**✅ All technical debt items now tracked in GitHub Issues (19 total issues created)**

---

## ✅ P1 Critical Issues (Blockers - MUST FIX)

### [COMPLETE] Issue #1: Remove non-functional CrewAI test file

- **File:** `tests/test_crewai_tools_integration.py`
- **Priority:** P1-Critical
- **Status:** ✅ CLOSED
- **Effort:** < 1 hour
- **Impact:** Test suite was 100% skipping silently
- **What was done:** Deleted file - it provided zero test coverage
- **Merged:** Feb 22, 2026

### [COMPLETE] Issue #2: Fix react-scripts: 0.0.0 in public-site

- **File:** `web/public-site/package.json`
- **Priority:** P1-Critical
- **Status:** ✅ CLOSED
- **Effort:** < 1 hour
- **Impact:** Placeholder package was non-functional
- **What was done:** Removed `"react-scripts": "0.0.0"` from dependencies
- **Result:** Next.js doesn't need react-scripts (that's CRA only)
- **npm audit:** 33-34 vulnerabilities remain (mostly Jest transitive deps)
- **Merged:** Feb 22, 2026

---

## 🔴 P2 High Priority Issues (Functional Gaps)

### Issue #3: Implement workflow pause/resume/cancel

**GitHub Issue:** <https://github.com/Glad-Labs/glad-labs-codebase/issues/3>

- **Files:** `src/cofounder_agent/routes/workflow_routes.py` (L196, L258, L321)
- **Priority:** P2-High
- **Status:** ⏳ Not Started
- **Effort:** 3-4 hours
- **Impact:** Workflow control incomplete - endpoints return status but don't actually pause/resume
- **Current Behavior:**

  ```python
  # Lines 196, 258, 321 have TODO comments
  # Functions return {"status": "paused"} but don't actually pause
  ```

- **Required Changes:**
  1. Add pause/resume/cancel state persistence in workflow_engine.py
  2. Implement actual pause/resume logic (not just status return)
  3. Add integration tests for pause/resume workflows
- **Acceptance Criteria:**
  - POST pause endpoint actually stops workflow execution
  - POST resume endpoint resumes from paused state
  - POST cancel endpoint terminates workflow
  - State persisted to PostgreSQL and restored on service restart

### Issue #4: Complete GDPR data subject rights workflow

**GitHub Issue:** <https://github.com/Glad-Labs/glad-labs-codebase/issues/4>

- **Priority:** P2-High
- **Status:** ⏳ Not Started
- **Effort:** 4-5 hours
- **Impact:** Compliance gap - GDPR requests not fully processed
- **Current Gaps:**
  - L112: TODO - Store GDPR requests in database (missing)
  - L115: TODO - Send verification email (missing)
  - L118: TODO - Implement GDPR processing workflow (missing)
- **Required Changes:**
  1. Create `gdpr_requests` table in PostgreSQL
  2. Implement email verification flow
  3. Add automated data export/deletion workflows
  4. Implement 30-day processing deadline tracking
- **Acceptance Criteria:**
  - GDPR requests stored with verification status
  - Automated email sent for verification
  - Data export returns user data in standard format
  - Deletion workflow respects 30-day deadline

### Issue #5: Add query performance monitoring

**GitHub Issue:** <https://github.com/Glad-Labs/glad-labs-codebase/issues/5>

- **Files:** `src/cofounder_agent/services/database_service.py`
- **Priority:** P2-High
- **Status:** ⏳ Not Started
- **Effort:** 2-3 hours
- **Impact:** Blind to slow queries in production
- **Current State:** No performance monitoring/logging
- **Required Changes:**
  1. Create `@log_query_performance()` decorator
  2. Apply to 5 key database methods:
     - `get_tasks()`
     - `list_content()`
     - `get_user_with_relationships()`
     - `aggregate_metrics()`
     - `search_writing_samples()`
  3. Log queries exceeding performance targets:
     - Simple SELECT: 5ms
     - JOIN query: 50ms
     - Full-text search: 100ms
- **Acceptance Criteria:**
  - Slow queries (>threshold) logged with context
  - Query timing included in error logs
  - Performance metrics available in admin dashboard

### Issue #6: Execute Phase 1C error handling uniformity

**GitHub Issue:** <https://github.com/Glad-Labs/glad-labs-codebase/issues/6>

- **Files:** 68 service files across `src/cofounder_agent/services/`
- **Priority:** P2-High
- **Status:** ⏳ Not Started (Strategy documented, needs execution)
- **Effort:** 8.5 hours (parallelizable across team)
- **Impact:** Inconsistent error handling, reduced diagnostics
- **Current Issues:**
  - Some files: Proper HTTPException with status codes
  - Other files: Generic Exception or logger.exception()
  - Missing: Request ID propagation for debugging
- **Strategy:** See `PHASE_1C_COMPLETE_IMPLEMENTATION.md`
- **Required Changes:**
  1. Standardize exception type annotations (312 generic exceptions)
  2. Add request ID to all error contexts
  3. Use proper HTTPException(status_code=..., detail=...) format
  4. Implement structured error logging
- **Acceptance Criteria:**
  - All service errors have proper status codes
  - All errors include request context for debugging
  - Error logs include tracing information

---

## 🟡 P3 Medium Priority Issues (Quality Improvements)

### Issue #7: Fix 612 Pyright type annotation errors

**GitHub Issue:** <https://github.com/Glad-Labs/glad-labs-codebase/issues/7>

- **Files:** Multiple service files (especially `unified_orchestrator.py`, `database_service.py`)
- **Priority:** P3-Medium
- **Status:** ⏳ Not Started
- **Effort:** 20-30 hours (parallelizable)
- **Impact:** Reduced type safety, harder IDE assistance
- **Root Causes:**
  - Generic `Dict` instead of `Dict[str, Any]`
  - Missing return type hints on async functions
  - Untyped function parameters
- **Highest Impact Areas:**
  - `services/unified_orchestrator.py` (1,146 lines)
  - `services/database_service.py` (high complexity)
  - Workflow/phase services
- **Recommendation:** Start with critical services, async functions first
- **Acceptance Criteria:**
  - Pyright error count < 100
  - All public API functions typed
  - Critical services fully annotated

### Issue #8: Expand E2E test suite (Playwright)

- **Files:** `playwright-tests/` and new test files
- **Priority:** P3-Medium
- **Status:** ⏳ Not Started
- **Effort:** 8-12 hours
- **Impact:** Missing critical workflow coverage
- **Current Tests:** Basic structure in place, needs expansion
- **Required Test Coverage:**
  1. Full workflow execution (5 templates)
  2. Approval queue workflow
  3. Agent orchestration end-to-end
  4. Error handling scenarios
  5. Real-time WebSocket progress updates
- **Acceptance Criteria:**
  - 40+ E2E tests passing
  - All 5 workflow templates covered
  - Cross-browser testing automated

### Issue #9: Refactor monolithic services

- **Files:**
  - `src/cofounder_agent/services/unified_orchestrator.py` (1,146 lines)
  - `src/cofounder_agent/services/workflow_execution_adapter.py` (726 lines)
- **Priority:** P3-Medium
- **Status:** ⏳ Not Started
- **Effort:** 6-8 hours
- **Impact:** Improved testability and maintainability
- **Refactoring Plan:**
  1. Split `unified_orchestrator.py` into:
     - `request_router.py` (intent parsing)
     - `execution_engine.py` (phase execution)
     - `result_handler.py` (result aggregation)
  2. Extract async queue logic from `workflow_execution_adapter.py`
     - Current: Uses `asyncio.create_task()` (in-process)
     - Future: Ready for Celery/Redis migration
- **Acceptance Criteria:**
  - Each service < 500 lines
  - Single responsibility per service
  - Test coverage maintained

### Issue #10: Fix intermittent integration test failures

- **Files:** `tests/integration/` test suite
- **Priority:** P3-Medium
- **Status:** ⏳ Not Started
- **Effort:** 2-3 hours
- **Impact:** Unreliable test suite
- **Current State:** Some tests marked as slow, intermittent failures
- **Required Changes:**
  1. Identify flaky tests (async timing issues)
  2. Add retry logic for transient failures
  3. Increase timeout values for slow operations
  4. Mock external service calls
- **Acceptance Criteria:**
  - All integration tests pass consistently
  - No random failures in CI/CD
  - Clear test markers for slow tests

### Issue #11: Add rate limiting middleware

**GitHub Issue:** <https://github.com/Glad-Labs/glad-labs-codebase/issues/8>

- **Files:** `src/cofounder_agent/main.py` and new middleware file
- **Priority:** P3-Medium
- **Status:** ⏳ Not Started
- **Effort:** 3-4 hours
- **Impact:** Security gap - DoS attacks possible
- **Required Implementation:**
  1. Choose tool: `python-ratelimit` or FastAPI middleware
  2. Apply to public endpoints: `/api/tasks`, `/api/agents`
  3. Configure per IP: 100 requests/minute (configurable)
  4. Configure per user: 1000 requests/minute (logged in)
- **Acceptance Criteria:**
  - Rate limit enforcement active
  - 429 responses for exceeded limits
  - Rate limit headers in responses
  - Configurable via environment variables

### Issue #12: Integrate webhook validation

**GitHub Issue:** <https://github.com/Glad-Labs/glad-labs-codebase/issues/9>

- **Files:** `src/cofounder_agent/middleware/webhook_security.py` and `routes/webhooks.py`
- **Priority:** P3-Medium
- **Status:** ⏳ Not Started (Code exists, not integrated)
- **Effort:** 2-3 hours
- **Impact:** Security gap - Webhook spoofing possible
- **Current State:** Validation code exists but not used
- **Required Changes:**
  1. Connect `webhook_security.py` validation to routes
  2. Verify webhook signatures on incoming requests
  3. Add webhook origin validation
  4. Log validation failures
- **Acceptance Criteria:**
  - Webhook validation active on all endpoints
  - Spoofed webhooks rejected
  - Validation failures logged

### Issue #13: Database connection pool tuning

- **Files:** `src/cofounder_agent/services/database_service.py`
- **Priority:** P3-Medium
- **Status:** ⏳ Not Started
- **Effort:** 1 hour
- **Impact:** Performance optimization
- **Current Config:** pool_size=10 (default)
- **Recommended Config:** 50-100 for production load
- **Required Changes:**
  1. Analyze connection usage patterns
  2. Update connection pool configuration
  3. Add monitoring for pool exhaustion
  4. Document tuning for production
- **Acceptance Criteria:**
  - Connection pool sized for expected load
  - No "connection pool exhausted" errors
  - Pool metrics available in monitoring

---

## 🟢 P4 Low Priority Issues (Nice-to-Have Optimizations)

### Issue #14: Public Site Vite migration (mirrors Phase 3B)

- **Files:** `web/public-site/` entire directory
- **Priority:** P4-Low
- **Status:** ⏳ Not Started (Phase 3B completed for Oversight Hub)
- **Effort:** 6-8 hours
- **Impact:** 90% vulnerability reduction for Next.js site
- **Notes:** Oversight Hub already migrated to Vite successfully
- **Expected Benefits:**
  - Reduce vulnerabilities from 33+ to ~6
  - Faster build times
  - Better developer experience
- **Acceptance Criteria:**
  - Build succeeds in < 45 seconds
  - Dev server launches in < 1 second
  - npm audit shows < 10 vulnerabilities

### Issue #15: Database performance benchmarking

- **Files:** New file `tests/performance/benchmark_queries.py`
- **Priority:** P4-Low
- **Status:** ⏳ Not Started
- **Effort:** 4-6 hours
- **Impact:** Detect performance regressions early
- **Benchmark Targets:**
  - Simple SELECT: 5ms
  - JOIN query: 50ms
  - Full-text search: 100ms
  - Aggregate (GROUP BY): 200ms
  - Complex transaction: 500ms
- **Acceptance Criteria:**
  - Baseline benchmarks established
  - Benchmark results tracked in CI/CD
  - Regression alerts when queries exceed targets

### Issue #16: Cost tracking consolidation

- **Files:** Analytics services across codebase
- **Priority:** P4-Low
- **Status:** ⏳ Not Started
- **Effort:** 4-6 hours
- **Impact:** Better visibility into cost per feature
- **Current State:** Tracking exists, no unified report
- **Required Changes:**
  1. Consolidate cost tracking from multiple services
  2. Create cost attribution per feature/agent
  3. Generate cost reports per day/week/month
  4. Dashboard visualization
- **Acceptance Criteria:**
  - Cost attribution report available
  - Per-feature cost breakdown working
  - Cost trends visible in admin dashboard

### Issue #17: Markdown linting cleanup

- **Files:** All `.md` files in repo (mostly in docs/)
- **Priority:** P4-Low
- **Status:** ⏳ Not Started
- **Effort:** 0.5 hours
- **Impact:** Documentation consistency
- **Current Issues:** 533 markdown lint errors (mostly formatting)
- **Examples:**
  - Missing blank lines before headings
  - Table formatting issues
  - Line length violations
- **Tool:** prettier can auto-fix most issues
- **Acceptance Criteria:**
  - 0 markdown lint errors
  - `npm run lint:md` passes

### Issue #18: Performance benchmark suite setup

- **Files:** New test framework setup
- **Priority:** P4-Low
- **Status:** ⏳ Not Started
- **Effort:** 4-6 hours
- **Impact:** Detect performance regressions
- **Required Setup:**
  1. Configure pytest-benchmark
  2. Benchmark critical operations:
     - Agent task execution
     - Workflow phase transitions
     - LLM API calls
     - Database queries
  3. CI/CD integration to track over time
- **Acceptance Criteria:**
  - Performance benchmarks run in CI/CD
  - Results tracked and alerted on regression

### Issue #19: Service discovery advanced routing

- **Files:** `src/cofounder_agent/services/service_registry.py`
- **Priority:** P4-Low
- **Status:** ⏳ Not Started (Basic structure exists)
- **Effort:** 3-4 hours
- **Impact:** Enhanced agent orchestration
- **Future Enhancement:** Beyond current capability system
- **Potential Features:**
  - Dynamic service weighting
  - Load balancing across agent instances
  - Failover strategies
- **Acceptance Criteria:**
  - Service registry fully functional
  - Automatic service discovery working
  - Failover tested and working

---

## 📋 Implementation Timeline Recommendation

### Sprint 1 (Week 1): P1 Critical ✅ COMPLETE

- [x] Delete CrewAI test file
- [x] Fix react-scripts: 0.0.0
- **Remaining:** None - all P1 items complete!

### Sprint 2 (Weeks 2-3): P2 High (17-18 hours)

**Week 2:**

- [ ] Implement workflow pause/resume/cancel (3-4h)
- [ ] Start GDPR workflow (4-5h)

**Week 3:**

- [ ] Complete GDPR workflow (2-3h)
- [ ] Add query performance monitoring (2-3h)
- [ ] Begin Phase 1C error handling across team (4-5h)

### Sprint 3 (Weeks 4-6): P3 Medium (42-63 hours)

- [ ] Expand E2E test suite (8-12h)
- [ ] Fix Pyright type errors (priority batch: 10-15h)
- [ ] Add rate limiting middleware (3-4h)
- [ ] Integrate webhook validation (2-3h)
- [ ] Refactor monolithic services (6-8h)
- [ ] Fix test failures + pool tuning (3-4h)

### Sprint 4+ (Ongoing): P4 Nice-to-Haves

- [ ] Public Site Vite migration (6-8h)
- [ ] Performance benchmarking (4-6h)
- [ ] Cost tracking consolidation (4-6h)
- [ ] Markdown lint cleanup (0.5h)
- [ ] Service discovery enhancements (3-4h)

---

## 🔄 How to Create GitHub Issues

Use this format for each issue. Create in GitHub project with labels:

```markdown
Title: [P1/P2/P3/P4] Issue Title

Body:
## Description
[Copy from issue description above]

## Files Affected
- `path/to/file.py`

## Effort Estimate
X-Y hours

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Dependencies
- Issue #X (if any)

## Labels
- tech-debt:P1-critical (or P2-high, P3-medium, P4-low)
- category (dependencies, security, performance, testing, refactoring)
```

---

## 📊 Tracking Checklist

**P1 Critical (2/2 Complete):**

- [x] Delete CrewAI test file
- [x] Fix react-scripts: 0.0.0

**P2 High (0/4 In Progress):**

- [ ] Workflow pause/resume/cancel
- [ ] GDPR data subject rights
- [ ] Query performance monitoring
- [ ] Phase 1C error handling

**P3 Medium (0/8 Not Started):**

- [ ] 612 Pyright type errors
- [ ] Expand E2E test suite
- [ ] Refactor monolithic services
- [ ] Fix intermittent test failures
- [ ] Add rate limiting middleware
- [ ] Integrate webhook validation
- [ ] Tune database connection pool
- [ ] Query monitoring application

**P4 Low (0/6 Not Started):**

- [ ] Public Site Vite migration
- [ ] Performance benchmarking
- [ ] Cost tracking consolidation
- [ ] Markdown lint cleanup
- [ ] Benchmark suite setup
- [ ] Service discovery enhancements

---

## 📞 Questions & Clarifications

**Before starting P2 items, clarify:**

1. **Team capacity:** How many developers available for debt work?
2. **Risk tolerance:** Is 87-120 hours acceptable, or focus P1+P2 only (~30h)?
3. **Type safety:** Worth 20-30 hours on Pyright errors, or technical debt?
4. **Performance monitoring:** Critical for production, or can be deferred?
5. **Public Site:** Migrate to Vite now (P4) or acceptable to wait?

---

## ✅ Current Production Status

| Component | Status | Issues |
|-----------|--------|--------|
| Backend (FastAPI) | ✅ Running | 0 blockers |
| Database (PostgreSQL) | ✅ Running | Library-only deps |
| Oversight Hub (React+Vite) | ✅ Running | 6 vulns (post-migration) |
| Public Site (Next.js) | ✅ Running | 33 vulns (Jest deps) |
| OAuth | ✅ Production-ready | 3-layer validation |
| Workflows | ⚠️ Partial (no pause/resume) | 3 TODOs |
| GDPR Compliance | ⚠️ Incomplete | 3 TODOs |

**Overall Assessment:** Production-ready for feature work, with known gaps in control flows and compliance.
