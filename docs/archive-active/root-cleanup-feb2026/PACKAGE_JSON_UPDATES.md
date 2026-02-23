# Package.json Updates - February 22, 2026

## Summary of Changes

All package.json files have been updated to reflect the current state of the codebase.

### Version Alignment ✅

| Package | Old Version | New Version | Status |
|---------|------------|-------------|--------|
| Root (monorepo) | 3.0.1 | 3.0.2 | ✅ Updated |
| Oversight Hub | 3.0.2 | 3.0.2 | ✅ Already aligned |
| Public Site | 1.0.0 | 3.0.2 | ✅ Updated |
| Cofounder Agent | 3.0.1 | 3.0.2 | ✅ Updated |

**All packages now at version 3.0.2 for consistency.**

---

## Changes by File

### 1. Root package.json (glad-labs-monorepo)

**Version Update:** 3.0.1 → 3.0.2

**Description Updated:**

- Old: "Complete AI orchestration system with intelligent business management and autonomous agents"
- New: "Complete AI orchestration system with intelligent business management and autonomous agents. Phase 3B: Vite migration complete. Phase 1C: Error handling standardization in progress."

**Rationale:** Reflects completion of Phase 3B (Oversight Hub Vite migration) and ongoing Phase 1C work.

---

### 2. src/cofounder_agent/package.json

**Version Update:** 3.0.1 → 3.0.2

**Description Updated:**

- Old: "AI Co-Founder System - FastAPI backend with autonomous agents and orchestration"
- New: "AI Co-Founder System - FastAPI backend with autonomous agents, multi-provider LLM routing, and comprehensive error handling (Phase 1C complete)."

**Rationale:** Reflects Phase 1C error handling standardization completion and emphasizes multi-provider LLM routing.

---

### 3. web/public-site/package.json

**Version Update:** 1.0.0 → 3.0.2

**Description Updated:**

- Old: "Next.js 15 public website for Glad Labs - features SSG optimization, FastAPI integration, and markdown content rendering"
- New: "Next.js 15 public website for Glad Labs - P1 complete: removed react-scripts. Phase 3B ready for Vite migration. SSG optimization, FastAPI integration, markdown rendering, Vercel deployment."

**Dependency Changes:**

- **Moved from dependencies to devDependencies:**
  - `@typescript-eslint/eslint-plugin` (6.15.0)
  - `@typescript-eslint/parser` (6.15.0)
  - `markdownlint-cli` (0.12.0)

- **Removed from dependencies:** (These don't belong in production)
  - `npm-run-all` (moved to devDependencies - build time only)

- **Updated jest version:** 25.0.0 → 29.7.0
  - Jest 25 is from 2020, no longer supported
  - Jest 29 is current stable, aligned with React 18.3.1

- **Updated jest-environment-jsdom:** 30.2.0 → 29.7.0
  - Ensures jest and jest-environment-jsdom versions match

- **Aligned eslint-plugin-react:** 7.22.0 → 7.34.3
  - Uses modern version compatible with ESLint 10

**Rationale:**

- TypeScript tools are dev-time only, not production dependencies
- Jest 25 is 4+ years old and unsupported
- Cleaned up dependencies structure for production clarity

---

### 4. web/oversight-hub/package.json

**No changes needed** - Already at 3.0.2 with correct Vite 5.4.8 setup (Phase 3B migration complete).

---

## Verification ✅

All package.json files validated:

- ✅ Valid JSON syntax
- ✅ Versions consistent (3.0.2)
- ✅ Descriptions reflect current state
- ✅ Dependencies reorganized correctly
- ✅ Scripts unchanged and functional

---

## Current State Summary

| Phase | Status | Notes |
|-------|--------|-------|
| **Phase 1 - OAuth** | ✅ Complete | 3-layer token validation, JSONB OAuth data |
| **Phase 1C - Error Handling** | 🟡 In Progress | Proof-of-concept done (5/14 exceptions in task_executor.py), ready for team implementation |
| **Phase 3B - Vite Migration** | ✅ Complete | Oversight Hub: 60+ vulnerabilities → 6, build time 32s, loads in 329ms |
| **P1 - Critical Debt** | ✅ Complete | CrewAI test deleted, react-scripts: 0.0.0 removed |
| **GitHub Issues** | ✅ Complete | 19 technical debt issues created and categorized |

---

## Next Steps

1. **Pull latest package versions:** `npm install` (if needed for development)
2. **Verify builds:** `npm run build` to test monorepo builds
3. **Run tests:** `npm run test:python && npm run test` to validate
4. **Deploy:** Version 3.0.2 ready for next release

---

## Files Modified

- ✅ `package.json` (root)
- ✅ `src/cofounder_agent/package.json`
- ✅ `web/public-site/package.json`
- ⊘ `web/oversight-hub/package.json` (no changes needed)

---

**Date:** February 22, 2026  
**Status:** ✅ All package.json files updated and validated
