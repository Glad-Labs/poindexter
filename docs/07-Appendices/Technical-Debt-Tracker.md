# Technical Debt Tracking - Glad Labs

**Last Updated:** March 9, 2026
**Repository:** `Glad-Labs/glad-labs-codebase` (branch: `dev`)

This tracker is aligned to:

- Current codebase scan (`TODO|FIXME|HACK|XXX` in `src/**/*.py` and frontend code files)
- Current open GitHub issues in `Glad-Labs/glad-labs-codebase`

## Current Snapshot

| Priority    | Open Issues | Notes                                                                                                                                     |
| ----------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| P1-Critical | 0           | All critical issues resolved (most recent: #81 GET /api/tasks 404, fixed March 9 2026)                                                   |
| P2-High     | 0           | All high-priority items completed                                                                                                         |
| P3-Medium   | 8           | #7 Pyright, #11 monolith refactor, #13 E2E coverage, #20 frontend gates, #23 SWR caching, #45 async queue, #78 exception handlers, #80 SELECT * |
| P4-Low      | 2           | #14 benchmarking, #79 closed (Strapi removed March 9)                                                                                    |
| Total       | 10          | See open issues for current canonical set                                                                                                 |

## Canonical Open Debt Issues

### P1-Critical

_No open critical issues_ ✅

## Known Systemic Risk

**Silent route registration failures:** `route_registration.py` wraps every router import in `try/except Exception`, silently swallowing import errors. This caused `GET /api/tasks` to return 404 when `task_routes.py` failed to load (#81). Any route file with a broken import will be silently skipped with no runtime indication. Consider logging failures at CRITICAL level or surfacing them via `/api/health`.

## Recently Closed (Completed)

**March 9, 2026:**
- [#81](https://github.com/Glad-Labs/glad-labs-codebase/issues/81) `[P1-Critical] GET /api/tasks 404` — `Optional[BackgroundTasks]` in task_routes.py prevented startup import; all /api/tasks endpoints were unavailable (fixed commit bc86aee)
- [#79](https://github.com/Glad-Labs/glad-labs-codebase/issues/79) `[P4-Low] Remove deprecated Strapi webhook code` — deleted webhooks.py, unregistered webhook_router, removed no-op _register_route_services()
- [#77](https://github.com/Glad-Labs/glad-labs-codebase/issues/77) `[P3-Medium] Consolidate root-level markdown files` — 13 .md files moved into docs/ hierarchy
- [#75](https://github.com/Glad-Labs/glad-labs-codebase/issues/75) `[P3-Medium] Fix google.genai import pattern` — already resolved with try/except SDK fallback
- [#74](https://github.com/Glad-Labs/glad-labs-codebase/issues/74) `[P3-Medium] Fix invalid DatabaseService method calls` — fixed get_session()/get_connection_pool() usage, update_workflow_status(), duplicate route
- [#73](https://github.com/Glad-Labs/glad-labs-codebase/issues/73) `[P3-Medium] Fix SQLAlchemy string API misuse` — rewrote capability_tasks_service.py to use asyncpg
- [#72](https://github.com/Glad-Labs/glad-labs-codebase/issues/72) `[P3-Medium] Optimize SELECT * queries` — audited 17+ occurrences; fixed users_db.py; closed as mostly necessary full-row fetches

**March 6, 2026:**
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

- [#36](https://github.com/Glad-Labs/glad-labs-codebase/issues/36) Add comprehensive type hints
- [#37](https://github.com/Glad-Labs/glad-labs-codebase/issues/37) Add pytest markers for test categorization
- [#38](https://github.com/Glad-Labs/glad-labs-codebase/issues/38) Add rate limiting middleware for DoS protection
- [#39](https://github.com/Glad-Labs/glad-labs-codebase/issues/39) Integrate webhook signature validation
- [#40](https://github.com/Glad-Labs/glad-labs-codebase/issues/40) Tune database connection pool for production
- [#43](https://github.com/Glad-Labs/glad-labs-codebase/issues/43) Implement training data capture in content phases
- [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45) Replace in-process workflow task queue with robust async queue
- [#20](https://github.com/Glad-Labs/glad-labs-codebase/issues/20) Add test coverage for public-site and oversight-hub
- [#48](https://github.com/Glad-Labs/glad-labs-codebase/issues/48) Standardize OAuth state validation across all callback handlers
- [#49](https://github.com/Glad-Labs/glad-labs-codebase/issues/49) Centralize frontend token access through a single auth client

### P4-Low

- [#41](https://github.com/Glad-Labs/glad-labs-codebase/issues/41) Implement HTTP caching headers for performance
- [#42](https://github.com/Glad-Labs/glad-labs-codebase/issues/42) Implement API rate limiting for abuse prevention

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

## Codebase-Verified Outstanding TODO Debt

Verified TODO markers still present:

- `src/cofounder_agent/services/phases/content_phases.py:547`
- `src/cofounder_agent/services/workflow_execution_adapter.py:730`

Mappings:

- Training data capture TODO -> [#43](https://github.com/Glad-Labs/glad-labs-codebase/issues/43)
- Publishing DB TODOs -> [#44](https://github.com/Glad-Labs/glad-labs-codebase/issues/44)
- Async queue TODO -> [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45)

## Legacy Issue Notes

There are older overlapping debt issues still open (`#3-#20` in several categories), and some newer replacement issues (`#28+`) cover the same topics with better scoping.

Recommended cleanup pass in GitHub:

1. Close superseded duplicates and link to the canonical issue.
2. Close issues already completed in code (`#28`, `#29`, `#33`).
3. Keep this tracker aligned to the canonical list above.

## Next Operational Step

Use this tracker as the source-of-truth backlog and execute by priority:

1. P3: `#36`, `#37`, `#38`, `#39`, `#40`, `#43`, `#45`
2. P3 (Security): `#48`, `#49`
3. P4: `#41`, `#42`
