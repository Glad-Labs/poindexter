# Technical Debt Tracking - Glad Labs

**Last Updated:** March 5, 2026
**Repository:** `Glad-Labs/glad-labs-codebase` (branch: `dev`)

This tracker is aligned to:
- Current codebase scan (`TODO|FIXME|HACK|XXX` in `src/**/*.py` and frontend code files)
- Current open GitHub issues in `Glad-Labs/glad-labs-codebase`

## Current Snapshot

| Priority | Open Issues | Notes |
| --- | --- | --- |
| P1-Critical | 2 | Both marked completed in issue body, still open in GitHub |
| P2-High | 7 | Includes 1 newly created from code scan |
| P3-Medium | 8 | Includes 2 newly created from code scan |
| P4-Low | 2 | Newly added performance/security ops items |
| Total | 19 | Canonical active debt set in this tracker |

## Canonical Open Debt Issues

### P1-Critical

- [#28](https://github.com/Glad-Labs/glad-labs-codebase/issues/28) `[P1-CRITICAL] Remove non-functional CrewAI test file`  
  Status: Completed in code and issue body, but issue remains open.
- [#29](https://github.com/Glad-Labs/glad-labs-codebase/issues/29) `[P1-CRITICAL] Remove non-functional react-scripts dependency`  
  Status: Completed in code and issue body, but issue remains open.

### P2-High

- [#30](https://github.com/Glad-Labs/glad-labs-codebase/issues/30) Implement workflow pause/resume/cancel functionality
- [#31](https://github.com/Glad-Labs/glad-labs-codebase/issues/31) Complete GDPR data subject rights workflow
- [#32](https://github.com/Glad-Labs/glad-labs-codebase/issues/32) Add query performance monitoring and logging
- [#33](https://github.com/Glad-Labs/glad-labs-codebase/issues/33) Execute Phase 1C error handling uniformity
- [#34](https://github.com/Glad-Labs/glad-labs-codebase/issues/34) Standardize on Depends()-only DI pattern
- [#35](https://github.com/Glad-Labs/glad-labs-codebase/issues/35) Expand database integration test coverage
- [#44](https://github.com/Glad-Labs/glad-labs-codebase/issues/44) Wire publishing phases to database persistence

### P3-Medium

- [#36](https://github.com/Glad-Labs/glad-labs-codebase/issues/36) Add comprehensive type hints
- [#37](https://github.com/Glad-Labs/glad-labs-codebase/issues/37) Add pytest markers for test categorization
- [#38](https://github.com/Glad-Labs/glad-labs-codebase/issues/38) Add rate limiting middleware for DoS protection
- [#39](https://github.com/Glad-Labs/glad-labs-codebase/issues/39) Integrate webhook signature validation
- [#40](https://github.com/Glad-Labs/glad-labs-codebase/issues/40) Tune database connection pool for production
- [#43](https://github.com/Glad-Labs/glad-labs-codebase/issues/43) Implement training data capture in content phases
- [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45) Replace in-process workflow task queue with robust async queue
- [#20](https://github.com/Glad-Labs/glad-labs-codebase/issues/20) Add test coverage for public-site and oversight-hub

### P4-Low

- [#41](https://github.com/Glad-Labs/glad-labs-codebase/issues/41) Implement HTTP caching headers for performance
- [#42](https://github.com/Glad-Labs/glad-labs-codebase/issues/42) Implement API rate limiting for abuse prevention

## Newly Created From Codebase Scan (This Update)

- [#43](https://github.com/Glad-Labs/glad-labs-codebase/issues/43) `[P3-MEDIUM] Implement training data capture in content phases`
- [#44](https://github.com/Glad-Labs/glad-labs-codebase/issues/44) `[P2-HIGH] Wire publishing phases to database persistence`
- [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45) `[P3-MEDIUM] Replace in-process workflow task queue with robust async queue`

## Codebase-Verified Outstanding TODO Debt

Verified TODO markers still present:

- `src/cofounder_agent/routes/privacy_routes.py:114`
- `src/cofounder_agent/routes/privacy_routes.py:117`
- `src/cofounder_agent/routes/privacy_routes.py:120`
- `src/cofounder_agent/services/phases/content_phases.py:547`
- `src/cofounder_agent/services/phases/publishing_phases.py:152`
- `src/cofounder_agent/services/phases/publishing_phases.py:250`
- `src/cofounder_agent/services/workflow_execution_adapter.py:730`

Mappings:

- Privacy/GDPR TODOs -> [#31](https://github.com/Glad-Labs/glad-labs-codebase/issues/31)
- Training data capture TODO -> [#43](https://github.com/Glad-Labs/glad-labs-codebase/issues/43)
- Publishing DB TODOs -> [#44](https://github.com/Glad-Labs/glad-labs-codebase/issues/44)
- Async queue TODO -> [#45](https://github.com/Glad-Labs/glad-labs-codebase/issues/45)

## Legacy Issue Notes

There are older overlapping debt issues still open (`#3-#20` in several categories), and some newer replacement issues (`#28+`) cover the same topics with better scoping.

Recommended cleanup pass in GitHub:

1. Close superseded duplicates and link to the canonical issue.
2. Close issues already completed in code (`#28`, `#29`, and likely `#33` if verification confirms).
3. Keep this tracker aligned to the canonical list above.

## Next Operational Step

Use this tracker as the source-of-truth backlog and execute by priority:

1. P2: `#31`, `#32`, `#35`, `#44`
2. P3: `#36`, `#37`, `#38`, `#39`, `#40`, `#43`, `#45`
3. P4: `#41`, `#42`
