# Technical Debt Tracking - Glad Labs

**Last Updated:** March 8, 2026
**Repository:** `Glad-Labs/glad-labs-codebase` (branch: `dev`)
**Last Codebase Scan:** March 8, 2026 - 116 service modules, 56 test files, 0 frontend tests

This tracker is aligned to:

- Current codebase scan (`TODO|FIXME|HACK|XXX` in `src/**/*.py` and frontend code files)
- Current open GitHub issues in `Glad-Labs/glad-labs-codebase`
- Code quality metrics (exception handling, query optimization, test coverage)

## Current Snapshot (March 8, 2026 Scan)

| Priority    | Open Issues | Notes                                                                           |
| ----------- | ----------- | ------------------------------------------------------------------------------- |
| P1-Critical | 0           | All critical issues resolved                                                    |
| P2-High     | 0           | All high-priority workflow/security/debt items completed                        |
| P3-Medium   | 12          | Includes frontend tests, database optimization, exception types standardization |
| P4-Low      | 3           | Performance ops and optional enhancements                                       |
| Total       | 15          | Canonical active debt set in this tracker                                       |

### Codebase Metric Snapshot

| Metric                              | Value       | Status     | Notes                                                                  |
| ----------------------------------- | ----------- | ---------- | ---------------------------------------------------------------------- |
| Backend Service Modules             | 116         | ✅ Healthy | Well-organized services layer                                          |
| Backend Test Files                  | 56          | ✅ Good    | 57/57 tests passing (Phase 2B complete)                                |
| Frontend Test Files (Oversight Hub) | 0           | ⚠️ Missing | React admin dashboard needs test coverage                              |
| Frontend Test Files (Public Site)   | 0           | ⚠️ Missing | Next.js website needs test coverage                                    |
| TODO Markers in Code                | 1           | ✅ Low     | Async queue implementation (workflow_execution_adapter.py:730)         |
| Generic "except Exception" Handlers | 30+         | ⚠️ Medium  | Need specific exception types per Python standards                     |
| SELECT \* FROM Statements           | 10+         | ⚠️ Medium  | Should select specific columns for performance                         |
| localStorage Usage in Oversight Hub | 2 instances | ℹ️ Info    | LayoutWrapper.jsx (legacy use for chat height; archive files archived) |

## Canonical Open Debt Issues

### P1-Critical

_No open critical issues_ ✅

## Recently Closed (Completed)

- [#46](https://github.com/Glad-Labs/glad-labs-codebase/issues/46) `[P1-CRITICAL][Security] Migrate auth tokens from localStorage to httpOnly secure cookies` (closed March 5, 2026)
- [#47](https://github.com/Glad-Labs/glad-labs-codebase/issues/47) `[P2-HIGH][Security] Add strict environment variable validation for API URL config` (closed March 5, 2026)
- [#50](https://github.com/Glad-Labs/glad-labs-codebase/issues/50) `[P2-HIGH][Security] Remove hardcoded localhost API endpoint fallbacks` (closed March 5, 2026)
- [#32](https://github.com/Glad-Labs/glad-labs-codebase/issues/32) `[P2-HIGH] Add query performance monitoring and logging` (closed March 6, 2026)
- [#31](https://github.com/Glad-Labs/glad-labs-codebase/issues/31) `[P2-HIGH] Complete GDPR data subject rights workflow` (closed March 6, 2026)
- [#34](https://github.com/Glad-Labs/glad-labs-codebase/issues/34) `[P2-HIGH] Standardize on Depends()-only DI pattern` (closed March 6, 2026; administrative normalization)
- [#35](https://github.com/Glad-Labs/glad-labs-codebase/issues/35) `[P2-HIGH] Expand database integration test coverage` (closed March 6, 2026; 68 tests added/passing)
- [#44](https://github.com/Glad-Labs/glad-labs-codebase/issues/44) `[P2-HIGH] Wire publishing phases to database persistence` (closed March 6, 2026; DB create/publish persistence wired with unit coverage)
- [#28](https://github.com/Glad-Labs/glad-labs-codebase/issues/28) `[P1-CRITICAL] Remove non-functional CrewAI test file` (closed as completed)
- [#29](https://github.com/Glad-Labs/glad-labs-codebase/issues/29) `[P1-CRITICAL] Remove non-functional react-scripts dependency` (closed as completed)
- [#33](https://github.com/Glad-Labs/glad-labs-codebase/issues/33) `[P2-HIGH] Execute Phase 1C error handling uniformity` (closed as completed)
- [#30](https://github.com/Glad-Labs/glad-labs-codebase/issues/30) `[P2-HIGH] Implement workflow pause/resume/cancel functionality` (closed as completed)

### P2-High

_No open high-priority issues_ ✅

### P3-Medium

- [#36](https://github.com/Glad-Labs/glad-labs-codebase/issues/36) Add comprehensive type hints to service functions
  - **Scope:** Services layer (116 modules)
  - **Effort:** 20-30 hours
  - **Benefit:** Improved IDE support, better error detection
- [#37](https://github.com/Glad-Labs/glad-labs-codebase/issues/37) Standardize exception handling (specific exception types)
  - **Scope:** 30+ routes with generic `except Exception` handlers
  - **Effort:** 8-10 hours
  - **Files:** analytics_routes.py, agent_registry_routes.py, agents_routes.py, chat_routes.py, auth_unified.py, migrations/
  - **Note:** Phase 1C partial completion; need specific exception types instead of generic Exception
  - **Status:** In Progress
- [#38](https://github.com/Glad-Labs/glad-labs-codebase/issues/38) Add rate limiting middleware for DoS protection
  - **Scope:** FastAPI middleware layer
  - **Effort:** 4-6 hours
  - **Risk:** High (security)
  - **Recommendation:** Implement before production deployment
- [#39](https://github.com/Glad-Labs/glad-labs-codebase/issues/39) Integrate webhook signature validation
  - **Scope:** webhooks.py route
  - **Effort:** 3-4 hours
  - **Risk:** High (security)
  - **Recommendation:** Validate HMAC signatures from external services
- [#40](https://github.com/Glad-Labs/glad-labs-codebase/issues/40) Optimize database connection pool for production
  - **Current:** Small pool for dev; needs tuning for load
  - **Effort:** 3-5 hours
  - **Scope:** database_service.py, .env.local configuration
  - **Recommendation:** Test with production-like loads; adjust min/max pool size
- [#43](https://github.com/Glad-Labs/glad-labs-codebase/issues/43) Implement training data capture in content phases
  - **Location:** src/cofounder_agent/services/phases/content_phases.py:547
  - **Effort:** 6-8 hours
  - **Benefit:** Enables fine-tuning and quality analysis
- [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45) Replace in-process workflow task queue with robust async queue
  - **TODO Location:** workflow_execution_adapter.py:730
  - **Current:** Uses asyncio.create_task() for in-process queuing
  - **Recommendation:** Migrate to Celery/RQ/Arq for distributed execution
  - **Effort:** 12-16 hours
  - **Migration Path:** Install Celery → Create Redis broker → Wire task wrappers
- [#20](https://github.com/Glad-Labs/glad-labs-codebase/issues/20) Add test coverage for frontend applications
  - **Scope:** 2 applications
    - Oversight Hub (React 18, Material-UI admin dashboard) — 0 tests
    - Public Site (Next.js 15, TailwindCSS website) — 0 tests
  - **Current Status:** No test files found
  - **Effort:** 25-35 hours (full coverage)
  - **Recommendation:** Start with Vitest for oversight-hub, Jest for public-site
  - **Priority:** Critical for release cycle (testing, CI/CD integration)
- [#48](https://github.com/Glad-Labs/glad-labs-codebase/issues/48) Standardize OAuth state validation across all callback handlers
  - **Status:** Partially complete (authClient.validateAndConsumeOAuthState is centralized)
  - **Effort:** 2-3 hours to complete remaining callback paths
  - **Scope:** GitHub OAuth callback validation uniformity
- [#51](https://github.com/Glad-Labs/glad-labs-codebase/issues/51) Optimize database queries (SELECT \* → specific columns)
  - **Scope:** 10+ SELECT \* FROM statements across codebase
  - **Files:** admin_db.py, custom_workflows_service.py, route_utils.py, postgres_cms_client.py
  - **Effort:** 4-6 hours
  - **Benefit:** Reduced bandwidth, faster network round-trips, smaller memory footprint
  - **Recommendation:** Audit queries; replace \* with explicit column lists
- [#49](https://github.com/Glad-Labs/glad-labs-codebase/issues/49) Centralize frontend token access through a single auth client
  - **Status:** Partially complete (authClient.js exists)
  - **Effort:** 2-3 hours to fully route all token access through auth client
  - **Scope:** Ensure all component token lookups use authClient, disable direct localStorage calls

### P4-Low

- [#41](https://github.com/Glad-Labs/glad-labs-codebase/issues/41) Implement HTTP caching headers for performance
  - **Scope:** FastAPI response headers (Cache-Control, ETag, etc.)
  - **Effort:** 2-3 hours
  - **Benefit:** Reduced server load, faster client response times
  - **Recommendation:** Cache static assets, workflow templates, reference data
- [#42](https://github.com/Glad-Labs/glad-labs-codebase/issues/42) Implement API rate limiting for abuse prevention
  - **Scope:** Global rate limiting middleware (per-IP or per-user)
  - **Effort:** 3-4 hours
  - **Status:** Lower priority than DoS protection (P3-#38)

## Newly Created From Codebase Scan (This Update)

- [#43](https://github.com/Glad-Labs/glad-labs-codebase/issues/43) `[P3-MEDIUM] Implement training data capture in content phases`
- [#44](https://github.com/Glad-Labs/glad-labs-codebase/issues/44) `[P2-HIGH] Wire publishing phases to database persistence`
- [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45) `[P3-MEDIUM] Replace in-process workflow task queue with robust async queue`

## Newly Created Security Tracking Issues (This Update)

- [#46](https://github.com/Glad-Labs/glad-labs-codebase/issues/46) `[P1-CRITICAL][Security] Migrate auth tokens from localStorage to httpOnly secure cookies`
- [#47](https://github.com/Glad-Labs/glad-labs-codebase/issues/47) `[P2-HIGH][Security] Add strict environment variable validation for API URL config`
- [#48](https://github.com/Glad-Labs/glad-labs-codebase/issues/48) `[P3-MEDIUM][Security] Standardize OAuth state validation across all callback handlers`
- [#49](https://github.com/Glad-Labs/glad-labs-codebase/issues/49) `[P3-MEDIUM][Security] Centralize frontend token access through a single auth client`
- [#50](https://github.com/Glad-Labs/glad-labs-codebase/issues/50) `[P2-HIGH][Security] Remove hardcoded localhost API endpoint fallbacks`

## Security Findings Tracked

The following frontend security findings have been tracked and resolved:

- Client-side token storage (`localStorage`) -> [#46](https://github.com/Glad-Labs/glad-labs-codebase/issues/46) ✅ CLOSED
- Hardcoded localhost endpoint fallbacks -> [#50](https://github.com/Glad-Labs/glad-labs-codebase/issues/50) ✅ CLOSED
- Missing strict env validation for API URLs -> [#47](https://github.com/Glad-Labs/glad-labs-codebase/issues/47) ✅ CLOSED
- Inconsistent OAuth state validation across callback paths -> [#48](https://github.com/Glad-Labs/glad-labs-codebase/issues/48) (open)
- Distributed token access instead of centralized auth client -> [#49](https://github.com/Glad-Labs/glad-labs-codebase/issues/49) (open)

## Codebase-Verified Outstanding Debt (March 8 Scan)

### TODO Markers Found

Only **1 verified TODO** in production code:

- `src/cofounder_agent/services/workflow_execution_adapter.py:730` — Async task queue implementation
  - **Mapped to:** [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45)
  - **Context:** `# TODO: Phase 2 - Implement robust async task queue (Celery, RQ, or Arq)`
  - **Current:** In-process asyncio.create_task() works for single instance but lacks persistence/distribution

### Code Quality Issues Found

1. **Generic Exception Handlers** (30+ instances)
   - **Files:** routes/analytics_routes.py, routes/agent_registry_routes.py, routes/agents_routes.py, routes/chat_routes.py, routes/auth_unified.py, migrations/
   - **Issue:** All use `except Exception as e:` instead of specific exception types
   - **Status:** Partially standardized in Phase 1C; need to complete remaining handlers
   - **Mapped to:** [#37](https://github.com/Glad-Labs/glad-labs-codebase/issues/37)

2. **SELECT \* FROM Statements** (10+ instances)
   - **Files:** admin_db.py, custom_workflows_service.py, route_utils.py, postgres_cms_client.py
   - **Issue:** Inefficient database queries; should select specific columns
   - **Mapped to:** [#51](https://github.com/Glad-Labs/glad-labs-codebase/issues/51) (NEW)
   - **Examples:**
     - `admin_db.py:136` — `SELECT * FROM cost_logs`
     - `admin_db.py:235` — `SELECT * FROM settings WHERE key = $1`
     - `custom_workflows_service.py:946` — `SELECT * FROM workflow_executions`

3. **Frontend Test Coverage** (CRITICAL GAP)
   - **Oversight Hub:** 0 test files (React admin dashboard — 116+ components)
   - **Public Site:** 0 test files (Next.js website)
   - **Recommendation:** Critical for CI/CD and quality gates
   - **Mapped to:** [#20](https://github.com/Glad-Labs/glad-labs-codebase/issues/20)

## Legacy Issue Notes

There are older overlapping debt issues still open (`#3-#20` in several categories), and some newer replacement issues (`#28+`) cover the same topics with better scoping.

Recommended cleanup pass in GitHub:

1. Close superseded duplicates and link to the canonical issue.
2. Close issues already completed in code (`#28`, `#29`, `#33`).
3. Keep this tracker aligned to the canonical list above.

## Next Operational Step

Use this tracker as the source-of-truth backlog and execute by priority:

### Immediate (Before Next Release)

1. **P3 Frontend Tests** [#20](https://github.com/Glad-Labs/glad-labs-codebase/issues/20) — Critical for CI/CD
   - Oversight Hub: 5-10 component tests minimum
   - Public Site: 3-5 page tests minimum

2. **P3 Exception Standardization** [#37](https://github.com/Glad-Labs/glad-labs-codebase/issues/37) — Code quality
   - Replace generic `except Exception` with specific types
   - Complete Phase 1C standardization

3. **P3 Security Middleware** [#38](https://github.com/Glad-Labs/glad-labs-codebase/issues/38) — DoS protection
   - Add rate limiting before production deployment

### Phase 3A (Next Sprint)

1. **P3 Async Queue** [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45) — Production readiness
2. **P3 Database Optimization** [#51](https://github.com/Glad-Labs/glad-labs-codebase/issues/51) — Performance
3. **P3 Type Hints** [#36](https://github.com/Glad-Labs/glad-labs-codebase/issues/36) — Code quality

### Phase 3B (Backlog)

1. P3 Remaining: Webhook validation [#39](https://github.com/Glad-Labs/glad-labs-codebase/issues/39), Pool tuning [#40](https://github.com/Glad-Labs/glad-labs-codebase/issues/40), Training data [#43](https://github.com/Glad-Labs/glad-labs-codebase/issues/43)
2. P4 All: HTTP caching [#41](https://github.com/Glad-Labs/glad-labs-codebase/issues/41), API rate limiting [#42](https://github.com/Glad-Labs/glad-labs-codebase/issues/42)
