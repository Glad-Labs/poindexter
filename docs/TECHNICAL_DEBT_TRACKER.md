# Technical Debt Tracking - Glad Labs

**Last Updated:** March 6, 2026
**Repository:** `Glad-Labs/glad-labs-codebase` (branch: `dev`)

This tracker is aligned to:
- Current codebase scan (`TODO|FIXME|HACK|XXX` in `src/**/*.py` and frontend code files)
- Current open GitHub issues in `Glad-Labs/glad-labs-codebase`

## Current Snapshot

| Priority | Open Issues | Notes |
| --- | --- | --- |
| P1-Critical | 0 | All critical issues resolved |
| P2-High | 4 | Performance monitoring + security issues completed |
| P3-Medium | 10 | Includes OAuth/state consistency + auth centralization issues (plus 1 legacy open item not normalized to current labels) |
| P4-Low | 2 | Newly added performance/security ops items |
| Total | 16 | Canonical active debt set in this tracker |

## Canonical Open Debt Issues

### P1-Critical

*No open critical issues* ✅

## Recently Closed (Completed)

- [#46](https://github.com/Glad-Labs/glad-labs-codebase/issues/46) `[P1-CRITICAL][Security] Migrate auth tokens from localStorage to httpOnly secure cookies` (closed March 5, 2026)
- [#47](https://github.com/Glad-Labs/glad-labs-codebase/issues/47) `[P2-HIGH][Security] Add strict environment variable validation for API URL config` (closed March 5, 2026)
- [#50](https://github.com/Glad-Labs/glad-labs-codebase/issues/50) `[P2-HIGH][Security] Remove hardcoded localhost API endpoint fallbacks` (closed March 5, 2026)
- [#32](https://github.com/Glad-Labs/glad-labs-codebase/issues/32) `[P2-HIGH] Add query performance monitoring and logging` (closed March 6, 2026)
- [#28](https://github.com/Glad-Labs/glad-labs-codebase/issues/28) `[P1-CRITICAL] Remove non-functional CrewAI test file` (closed as completed)
- [#29](https://github.com/Glad-Labs/glad-labs-codebase/issues/29) `[P1-CRITICAL] Remove non-functional react-scripts dependency` (closed as completed)
- [#33](https://github.com/Glad-Labs/glad-labs-codebase/issues/33) `[P2-HIGH] Execute Phase 1C error handling uniformity` (closed as completed)
- [#30](https://github.com/Glad-Labs/glad-labs-codebase/issues/30) `[P2-HIGH] Implement workflow pause/resume/cancel functionality` (closed as completed)

### P2-High

- [#31](https://github.com/Glad-Labs/glad-labs-codebase/issues/31) Complete GDPR data subject rights workflow
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
2. Close issues already completed in code (`#28`, `#29`, `#33`).
3. Keep this tracker aligned to the canonical list above.

## Next Operational Step

Use this tracker as the source-of-truth backlog and execute by priority:

1. P2: `#31`, `#32`, `#35`, `#44`
2. P2 (Security): `#50`, `#47`
3. P1 (Security): `#46` (implemented; close in GitHub)
4. P3: `#36`, `#37`, `#38`, `#39`, `#40`, `#43`, `#45`
5. P3 (Security): `#48`, `#49`
6. P4: `#41`, `#42`
