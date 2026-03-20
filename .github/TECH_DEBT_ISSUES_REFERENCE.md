# GitHub Issues - Quick Reference

Copy-paste these into GitHub Issues manually if CLI is not available.

## P2 High Priority

### Issue #3: [P2] Implement workflow pause/resume/cancel functionality

```
Title: [P2] Implement workflow pause/resume/cancel functionality

Description:
Workflow control endpoints are incomplete. They return status updates but don't actually pause/resume execution.

Files Affected:
- src/cofounder_agent/routes/workflow_routes.py (L196, L258, L321)
- src/cofounder_agent/services/workflow_engine.py

Effort Estimate: 3-4 hours

Acceptance Criteria:
- [ ] POST pause endpoint actually stops workflow execution
- [ ] POST resume endpoint resumes from paused state
- [ ] POST cancel endpoint terminates workflow
- [ ] State persisted to PostgreSQL and restored on service restart

Labels: tech-debt:P2-high, features, workflows
```

---

### Issue #4: [P2] Complete GDPR data subject rights workflow

```
Title: [P2] Complete GDPR data subject rights workflow

Description:
GDPR requests not fully processed - missing database storage, email verification, and processing workflow.

Files Affected:
- src/cofounder_agent/routes/privacy_routes.py (L112-L118)

Effort Estimate: 4-5 hours

Acceptance Criteria:
- [ ] GDPR requests stored with verification status
- [ ] Automated email sent for verification
- [ ] Data export returns user data in standard format
- [ ] Deletion workflow respects 30-day deadline

Labels: tech-debt:P2-high, security, compliance
```

---

### Issue #5: [P2] Add query performance monitoring decorator

```
Title: [P2] Add query performance monitoring decorator

Description:
No performance monitoring on database queries - we're blind to slow queries in production.

Files Affected:
- src/cofounder_agent/services/database_service.py

Effort Estimate: 2-3 hours

Acceptance Criteria:
- [ ] Create @log_query_performance() decorator
- [ ] Apply to get_tasks(), list_content(), get_user_with_relationships(), aggregate_metrics(), search_writing_samples()
- [ ] Slow queries (>threshold) logged with context
- [ ] Query timing included in error logs
- [ ] Performance metrics available in admin dashboard

Labels: tech-debt:P2-high, performance, observability
```

---

### Issue #6: [P2] Execute Phase 1C error handling uniformity

```
Title: [P2] Execute Phase 1C error handling uniformity across 68 service files

Description:
Inconsistent exception handling across service layer. Some files use HTTPException with proper status codes, others use generic Exception or logger.exception(). Strategy documented in PHASE_1C_COMPLETE_IMPLEMENTATION.md with copy-paste templates ready for execution.

Files Affected:
- src/cofounder_agent/services/ (68 files)

Effort Estimate: 8.5 hours (parallelizable across team)

Acceptance Criteria:
- [ ] Standardize exception type annotations (312 generic exceptions)
- [ ] Add request ID to all error contexts
- [ ] Use proper HTTPException(status_code=..., detail=...) format
- [ ] Implement structured error logging
- [ ] All service errors have proper status codes
- [ ] All errors include request context for debugging
- [ ] Error logs include tracing information

Labels: tech-debt:P2-high, error-handling, code-quality
```

---

## P3 Medium Priority

### Issue #7: [P3] Fix 612 Pyright type annotation errors

```
Title: [P3] Fix 612 Pyright type annotation errors

Description:
Missing type annotations on public API functions, generic Dict instead of Dict[str, Any], untyped parameters reduce type safety and IDE assistance.

Files Affected:
- src/cofounder_agent/services/unified_orchestrator.py (1,146 lines)
- src/cofounder_agent/services/database_service.py (high complexity)
- Workflow/phase services

Effort Estimate: 20-30 hours (parallelizable)

Acceptance Criteria:
- [ ] Pyright error count < 100
- [ ] All public API functions typed
- [ ] Critical services fully annotated

Labels: tech-debt:P3-medium, code-quality, type-safety
```

---

### Issue #8: [P3] Expand E2E test suite (Playwright)

```
Title: [P3] Expand E2E test suite (Playwright)

Description:
E2E framework in place but needs comprehensive coverage for critical workflows.

Files Affected:
- playwright-tests/
- New test files as needed

Effort Estimate: 8-12 hours

Acceptance Criteria:
- [ ] Full workflow execution tests (5 templates)
- [ ] Approval queue workflow tests
- [ ] Agent orchestration end-to-end tests
- [ ] Error handling scenario tests
- [ ] Real-time WebSocket progress tests
- [ ] 40+ E2E tests passing
- [ ] All 5 workflow templates covered
- [ ] Cross-browser testing automated

Labels: tech-debt:P3-medium, testing, e2e
```

---

### Issue #9: [P3] Refactor monolithic services

```
Title: [P3] Refactor monolithic services

Description:
Split unified_orchestrator.py (1,146 lines) and workflow_execution_adapter.py (726 lines) into smaller, focused services.

Files Affected:
- src/cofounder_agent/services/unified_orchestrator.py
- src/cofounder_agent/services/workflow_execution_adapter.py

Effort Estimate: 6-8 hours

Refactoring Plan:
1. Split unified_orchestrator.py into:
   - request_router.py (intent parsing)
   - execution_engine.py (phase execution)
   - result_handler.py (result aggregation)
2. Extract async queue logic from workflow_execution_adapter.py
   - Current: asyncio.create_task() (in-process)
   - Future: Ready for Celery/Redis migration

Acceptance Criteria:
- [ ] Each service < 500 lines
- [ ] Single responsibility per service
- [ ] Test coverage maintained

Labels: tech-debt:P3-medium, refactoring, maintainability
```

---

### Issue #10: [P3] Fix intermittent integration test failures

```
Title: [P3] Fix intermittent integration test failures

Description:
Some integration tests marked as slow with intermittent failures due to async timing issues.

Files Affected:
- tests/integration/

Effort Estimate: 2-3 hours

Acceptance Criteria:
- [ ] Identify flaky tests (async timing issues)
- [ ] Add retry logic for transient failures
- [ ] Increase timeout values for slow operations
- [ ] Mock external service calls
- [ ] All integration tests pass consistently
- [ ] No random failures in CI/CD

Labels: tech-debt:P3-medium, testing, reliability
```

---

### Issue #11: [P3] Add rate limiting middleware

```
Title: [P3] Add rate limiting middleware

Description:
Security gap - no rate limiting on public endpoints leaves system vulnerable to DoS attacks.

Files Affected:
- src/cofounder_agent/main.py
- New middleware file as needed

Effort Estimate: 3-4 hours

Implementation:
1. Choose tool: python-ratelimit or FastAPI middleware
2. Apply to public endpoints: /api/tasks, /api/agents
3. Configure per IP: 100 requests/minute (configurable)
4. Configure per user: 1000 requests/minute (logged in)

Acceptance Criteria:
- [ ] Rate limit enforcement active
- [ ] 429 responses for exceeded limits
- [ ] Rate limit headers in responses
- [ ] Configurable via environment variables

Labels: tech-debt:P3-medium, security, middleware
```

---

### Issue #12: [P3] Integrate webhook validation

```
Title: [P3] Integrate webhook validation

Description:
Webhook validation code exists in middleware but not connected to routes - security gap for webhook spoofing.

Files Affected:
- src/cofounder_agent/middleware/webhook_security.py
- src/cofounder_agent/routes/webhooks.py

Effort Estimate: 2-3 hours

Acceptance Criteria:
- [ ] Connect webhook_security.py validation to routes
- [ ] Verify webhook signatures on incoming requests
- [ ] Add webhook origin validation
- [ ] Log validation failures
- [ ] Webhook validation active on all endpoints
- [ ] Spoofed webhooks rejected

Labels: tech-debt:P3-medium, security, webhooks
```

---

### Issue #13: [P3] Tune database connection pool

```
Title: [P3] Tune database connection pool

Description:
Connection pool configured with default size=10, should be 50-100 for production workloads.

Files Affected:
- src/cofounder_agent/services/database_service.py

Effort Estimate: 1 hour

Acceptance Criteria:
- [ ] Analyze connection usage patterns
- [ ] Update connection pool configuration
- [ ] Add monitoring for pool exhaustion
- [ ] Document tuning for production
- [ ] Connection pool sized for expected load
- [ ] No "connection pool exhausted" errors

Labels: tech-debt:P3-medium, performance, database
```

---

## P4 Low Priority

### Issue #14: [P4] Migrate public-site to modern build tooling

```
Title: [P4] Public Site build tooling optimization

Description:
Oversight Hub uses Create React App (CRA) with CRACO for dependency management and bundling. Public Site is Next.js which is already modern.
Note: Previous Vite migration of Oversight Hub was found to be over-engineered and reverted to CRA for maintainability and ecosystem stability.

Files Affected:
- web/public-site/

Priority: Low (not blocking, Next.js is already well-optimized)

Effort Estimate: Research only

Expected Benefits:
- Reduce vulnerabilities from 33+ to ~6
- Faster build times
- Better developer experience

Acceptance Criteria:
- [ ] Build succeeds in < 45 seconds
- [ ] Dev server launches in < 1 second
- [ ] npm audit shows < 10 vulnerabilities

Labels: tech-debt:P4-low, dependencies, frontend
```

---

### Issue #15: [P4] Set up database performance benchmarking

```
Title: [P4] Set up database performance benchmarking

Description:
Create baseline benchmarks for critical database operations to detect regressions.

Files Affected:
- tests/performance/benchmark_queries.py

Effort Estimate: 4-6 hours

Benchmark Targets:
- Simple SELECT: 5ms
- JOIN query: 50ms
- Full-text search: 100ms
- Aggregate (GROUP BY): 200ms
- Complex transaction: 500ms

Acceptance Criteria:
- [ ] Baseline benchmarks established
- [ ] Benchmark results tracked in CI/CD
- [ ] Regression alerts when queries exceed targets

Labels: tech-debt:P4-low, performance, testing
```

---

### Issue #16: [P4] Consolidate cost tracking services

```
Title: [P4] Consolidate cost tracking services

Description:
Cost tracking exists across multiple services but no unified reporting - need centralized cost attribution.

Files Affected:
- src/cofounder_agent/services/ (analytics)

Effort Estimate: 4-6 hours

Acceptance Criteria:
- [ ] Consolidate cost tracking from multiple services
- [ ] Create cost attribution per feature/agent
- [ ] Generate cost reports per day/week/month
- [ ] Dashboard visualization
- [ ] Cost attribution report available
- [ ] Per-feature cost breakdown working
- [ ] Cost trends visible in admin dashboard

Labels: tech-debt:P4-low, analytics, monitoring
```

---

### Issue #17: [P4] Clean up markdown linting errors

```
Title: [P4] Clean up markdown linting errors

Description:
533 markdown lint errors across docs - mostly formatting issues fixable with prettier.

Files Affected:
- docs/
- All .md files

Effort Estimate: 0.5 hours

Current Issues:
- Missing blank lines before headings
- Table formatting issues
- Line length violations

Acceptance Criteria:
- [ ] 0 markdown lint errors
- [ ] npm run lint:md passes

Labels: tech-debt:P4-low, documentation
```

---

### Issue #18: [P4] Set up performance benchmark suite

```
Title: [P4] Set up performance benchmark suite

Description:
Configure pytest-benchmark for tracking performance regressions over time.

Files Affected:
- tests/performance/

Effort Estimate: 4-6 hours

Required Setup:
1. Configure pytest-benchmark
2. Benchmark critical operations:
   - Agent task execution
   - Workflow phase transitions
   - LLM API calls
   - Database queries
3. CI/CD integration to track over time

Acceptance Criteria:
- [ ] Performance benchmarks run in CI/CD
- [ ] Results tracked and alerted on regression

Labels: tech-debt:P4-low, performance, testing
```

---

### Issue #19: [P4] Enhance service discovery advanced routing

```
Title: [P4] Enhance service discovery advanced routing

Description:
Basic service registry exists - add dynamic weighting, load balancing, and failover strategies.

Files Affected:
- src/cofounder_agent/services/service_registry.py

Effort Estimate: 3-4 hours

Potential Features:
- Dynamic service weighting
- Load balancing across agent instances
- Failover strategies

Acceptance Criteria:
- [ ] Service registry fully functional
- [ ] Automatic service discovery working
- [ ] Failover tested and working

Labels: tech-debt:P4-low, orchestration
```

---

## How to Create Issues

### Option 1: GitHub CLI (Automated)

```bash
bash .github/create-tech-debt-issues.sh
```

Requires:

- GitHub CLI installed (<https://cli.github.com>)
- Authentication: `gh auth login`
- jq installed (for JSON parsing)

### Option 2: Manual Creation

Copy each issue above into GitHub Issues individually.

### Option 3: GitHub Web UI

1. Go to your repository
2. Issues → New Issue
3. Copy title and body from above
4. Add labels from the issue

---

## Quick Status Check

Once created, view all technical debt issues:

```bash
gh issue list --label "tech-debt:*" --sort created
```

Or in GitHub web UI:

```
Issues → Filter by label "tech-debt:P2-high"
```
