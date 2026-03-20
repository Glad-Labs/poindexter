# Technical Debt Tracking - Glad Labs

**Last Updated:** March 9, 2026 (session 2)
**Repository:** `Glad-Labs/glad-labs-codebase` (branch: `dev`)

This tracker is aligned to:

- Current codebase scan (`TODO|FIXME|HACK|XXX` in `src/**/*.py` and frontend code files)
- Current open GitHub issues in `Glad-Labs/glad-labs-codebase`

## Current Snapshot

| Priority    | Open Issues | Notes                                                                                                              |
| ----------- | ----------- | ------------------------------------------------------------------------------------------------------------------ |
| P1-Critical | 0           | All critical issues resolved                                                                                       |
| P2-High     | 0           | All high-priority items completed                                                                                  |
| P3-Medium   | 5           | #7 Pyright, #11 monolith refactor, #13 E2E coverage, #45 async queue, #78 exception handlers (partial), #89 JSX→TS |
| P4-Low      | 0           | All P4 items completed                                                                                             |
| Total       | 6           | See open issues for current canonical set                                                                          |

## Canonical Open Debt Issues

### P1-Critical

_No open critical issues_ ✅

### P2-High

_No open high-priority issues_ ✅

### P3-Medium

- [#7](https://github.com/Glad-Labs/glad-labs-codebase/issues/7) Fix Pyright type annotation errors (344 remaining)
- [#11](https://github.com/Glad-Labs/glad-labs-codebase/issues/11) Refactor monolithic services for single responsibility
- [#13](https://github.com/Glad-Labs/glad-labs-codebase/issues/13) Expand E2E test coverage for workflow and capability systems
- [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45) Replace in-process workflow task queue with robust async queue (deferred — needs Redis/infra decision)
- [#78](https://github.com/Glad-Labs/glad-labs-codebase/issues/78) Replace broad route exception handlers — 18/190+ narrowed (agent_registry_routes, metrics_routes done; approval_routes, workflow_routes, task_routes remain)
- [#89](https://github.com/Glad-Labs/glad-labs-codebase/issues/89) Migrate JSX components to TypeScript for type safety

### P4-Low

_No open P4 issues_ ✅

## Known Systemic Risk

**Silent route registration failures:** `route_registration.py` wraps every router import in `try/except Exception`, silently swallowing import errors. This caused `GET /api/tasks` to return 404 when `task_routes.py` failed to load (#81). Any route file with a broken import will be silently skipped with no runtime indication. Consider logging failures at CRITICAL level or surfacing them via `/api/health`.

## Recently Closed (Completed)

**March 9, 2026 (session 2):**

- [#84](https://github.com/Glad-Labs/glad-labs-codebase/issues/84) `[Public Site] Replace console.log with structured logger` — audited; production code is CLEAN, closed as non-issue
- [#80](https://github.com/Glad-Labs/glad-labs-codebase/issues/80) `[P3-Medium] Eliminate SELECT * queries` — full audit; only users_db.py needed fixing (done); rest are full-row converters
- [#90](https://github.com/Glad-Labs/glad-labs-codebase/issues/90) `[Public Site] Add metadata exports to all route pages` — generateMetadata() added to category, tag, author; archive uses layout.tsx
- [#86](https://github.com/Glad-Labs/glad-labs-codebase/issues/86) `[Public Site] Add loading.jsx files` — 5 loading.jsx files created (app/, posts/[slug]/, archive/[page]/, category/[slug]/, tag/[slug]/)
- [#87](https://github.com/Glad-Labs/glad-labs-codebase/issues/87) `[Public Site] Improve TopNav accessibility` — skip link, aria-label on nav + logo, visible focus rings
- [#88](https://github.com/Glad-Labs/glad-labs-codebase/issues/88) `[Public Site] Add Web Vitals monitoring` — WebVitals component via useReportWebVitals, reports to GA4, dev console
- [#20](https://github.com/Glad-Labs/glad-labs-codebase/issues/20) `[P3-Medium] Frontend coverage gates` — thresholds added to vitest.config.ts + jest.config.cjs (50% global, 60% for critical paths)
- [#91](https://github.com/Glad-Labs/glad-labs-codebase/issues/91) `[Public Site] Configure Sentry error monitoring` — @sentry/nextjs installed, DSN-gated init, wired to error.jsx + lib/error-handling.js
- [#23](https://github.com/Glad-Labs/glad-labs-codebase/issues/23) `[P3-Medium] Add SWR caching to frontends` — swr@2.4.1 installed, shared fetcher configs, archive page converted
- [#14](https://github.com/Glad-Labs/glad-labs-codebase/issues/14) `[P4-Low] API performance benchmarking` — pytest-benchmark added, 8 endpoint benchmarks, npm run benchmark scripts

**March 9, 2026 (session 1):**

- [#81](https://github.com/Glad-Labs/glad-labs-codebase/issues/81) `[P1-Critical] GET /api/tasks 404` — `Optional[BackgroundTasks]` in task_routes.py prevented startup import; all /api/tasks endpoints were unavailable (fixed commit bc86aee)
- [#79](https://github.com/Glad-Labs/glad-labs-codebase/issues/79) `[P4-Low] Remove deprecated Strapi webhook code` — deleted webhooks.py, unregistered webhook_router, removed no-op \_register_route_services()
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

## Security Findings Tracked

The following frontend security findings have been tracked and resolved:

- Client-side token storage (`localStorage`) -> [#46](https://github.com/Glad-Labs/glad-labs-codebase/issues/46) ✅ CLOSED
- Hardcoded localhost endpoint fallbacks -> [#50](https://github.com/Glad-Labs/glad-labs-codebase/issues/50) ✅ CLOSED
- Missing strict env validation for API URLs -> [#47](https://github.com/Glad-Labs/glad-labs-codebase/issues/47) ✅ CLOSED
- Inconsistent OAuth state validation across callback paths -> [#48](https://github.com/Glad-Labs/glad-labs-codebase/issues/48) ✅ CLOSED
- Distributed token access instead of centralized auth client -> [#49](https://github.com/Glad-Labs/glad-labs-codebase/issues/49) ✅ CLOSED

## Codebase-Verified Outstanding TODO Debt

Verified TODO markers still present:

- `src/cofounder_agent/services/phases/content_phases.py:547`
- `src/cofounder_agent/services/workflow_execution_adapter.py:730`

Mappings:

- Training data capture TODO -> [#43](https://github.com/Glad-Labs/glad-labs-codebase/issues/43)
- Async queue TODO -> [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45)

## Next Operational Step

Use this tracker as the source-of-truth backlog and execute by priority:

1. P3 (remaining): continue #78 (approval_routes, workflow_routes, task_routes)
2. P3: #7 Pyright type errors — batch fix high-frequency patterns
3. P3: #13 E2E coverage expansion
4. P3: #89 JSX→TypeScript (large refactor, batch by component)
5. P3: #11 Monolith refactor (requires architectural planning)
6. Deferred: #45 async queue (Redis/infra decision needed)
