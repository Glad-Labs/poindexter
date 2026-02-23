# Dependency Update & Security Fix Plan

**Status:** Phase 2C Technical Debt - Dependency Remediation  
**Date:** February 22, 2026  
**Priority:** HIGH (71 vulnerabilities found)

## Executive Summary

Current vulnerability scan results: **71 vulnerabilities** (7 moderate, 59 high, 5 critical)

### Root Causes
1. **React-Scripts 5.0.1** (Oversight Hub) - Brings in outdated Jest, ESLint, Babel, Rimraf ecosystem
2. **yaml-config 0.3.0** (transitive) - Contains critical underscore.js RCE vulnerability
3. **ESLint/Rimraf** - Minimatch dependency chain issues
4. **Deprecated build tools** - Jest, Babel, and related packages need major version bumps

---

## Vulnerability Breakdown

### Critical (5)
- `underscore` (1.3.2-1.12.0) - Arbitrary Code Execution in yaml-config dependency tree
- Result: **NO FIX** without removing yaml-config

### High (59)
- ESLint ecosystem (minimatch chain)
- Jest ecosystem (Rimraf-related)
- Build tools (ESLint 9.x, Jest 19.x needed)
- React-Scripts inherited dependencies

### Moderate (7)
- `tunnel-agent` (fixable via npm audit fix)
- `qs` (fixable via npm audit fix)
- Others (fixable via npm audit fix)

---

## Implementation Strategy

### Phase A: Quick Wins (Safe, Non-Breaking) ✅
**Estimated Time:** 30 minutes
**Risk Level:** LOW
**Status:** EXECUTABLE NOW

These fixes are auto-fixable by npm without breaking changes:

```bash
# 1. Remove vulnerable transitive dependencies (npm audit fix handles)
npm audit fix --audit-level=none

# 2. Update safe packages in main package.json
npm update @types/node
npm update @typescript-eslint/eslint-plugin @typescript-eslint/parser
npm update typescript
npm update prettier
npm update playwright
```

**Expected Result:**
- Reduces vulnerabilities from 71 → ~30-40 (removes easy fixes)
- Removes `tunnel-agent` and `qs` vulnerabilities
- No breaking changes to build process

**Affected Files:**
- `package.json` - Updated versions recorded
- `package-lock.json` - Regenerated with resolved transitive deps

---

### Phase B: React-Scripts Major Upgrade (Medium Risk)
**Estimated Time:** 2-3 hours
**Risk Level:** MEDIUM (requires testing)
**Status:** REQUIRES TESTING BEFORE COMMIT

**Current State:** react-scripts 5.0.1 (released 2023)  
**Target State:** react-scripts 5.0.1 → Evaluate feasibility of 6.x or migrate to Vite

**Option B1: Update react-scripts to latest 5.x**
```bash
cd web/oversight-hub
npm update react-scripts@5
npm audit --json > audit-after-update.json
```

**Option B2: Migrate from react-scripts to Vite** (Recommended Long-term)
- Eliminates react-scripts' dependency chain entirely
- Faster builds, better DX
- Requires refactoring `web/oversight-hub` tsconfig and build config
- **Time estimate:** 4-6 hours

**Recommended:** Start with Option B1 (safer), plan Option B2 for Phase 3

**Tests to Run (Post-Phase 3B Vite Migration):**
```bash
# After Vite migration - run Oversight Hub with Vite:
npm run dev --workspace=web/oversight-hub
# Check: Vite dev server starts, UI loads at http://localhost:5173, no console errors
npm test --workspace=web/oversight-hub
# Check: Vitest passes (replaces Jest)
npm run build --workspace=web/oversight-hub
# Check: Vite build succeeds, dist/ directory created
```

---

### Phase C: Remove/Replace yaml-config (High Impact)
**Estimated Time:** 1-2 hours
**Risk Level:** MEDIUM (find what depends on it)
**Status:** INVESTIGATION REQUIRED

**Problem:** `yaml-config` (0.3.0) contains critical RCE vulnerability in underscore  
**Solution:** Remove yaml-config and find alternative

**Investigation Steps:**
1. Check what brings in `yaml-config`:
   ```bash
   npm ls yaml-config
   ```
2. Determine if it's actually used:
   ```bash
   grep -r "yaml-config\|require.*config.*yaml" src/ scripts/ --include="*.js"
   ```
3. If not used: Remove it from package-lock.json via dependency update
4. If used: Replace with maintained alternative (js-yaml or node-yaml)

**Expected Outcome:**
- Removes 5 critical vulnerabilities
- Reduces total from ~30-40 → ~25-35 after Phase B

---

### Phase D: ESLint/Jest Chain Resolution (Comprehensive)
**Estimated Time:** 3-4 hours
**Risk Level:** HIGH (many interdependencies)
**Status:** REQUIRES TEST COVERAGE

**Current Issue:** Multiple npm packages depend on outdated versions:
- ESLint 9.x needed (currently in deps), but inherited packages use 8.x
- Jest 19.x would be needed (breaking change)
- Rimraf 6.x+ needed for git cleanup tooling

**Strategy:**
1. Update ESLint to 10.x (if compatible with TypeScript)
2. Update lint dependencies
3. Re-run audit to identify remaining issues

```bash
npm update eslint @typescript-eslint/parser @typescript-eslint/eslint-plugin
npm update rimraf
npm audit
```

**Risk:** ESLint major version changes can affect linting rules  
**Mitigation:** Run `npm run lint` after updates to verify rules still work

---

## Execution Order (Recommended)

```
Phase A (Quick Wins)              ← Execute now, low risk
    ↓
Phase B (React-Scripts)           ← Execute next, medium risk but isolated to oversight-hub
    ↓
Phase C (yaml-config removal)     ← Execute after B, high impact
    ↓
Phase D (ESLint/Jest chain)       ← Final cleanup, lower priority
```

**Timeline Estimate:**
- Phase A: 30 min (30 min total)
- Phase B: 2 hours (2.5 hours total)
- Phase C: 1 hour (3.5 hours total)
- Phase D: 2-3 hours (5.5-6.5 hours total)

---

## Testing Checklist

### After Each Phase, Run:
- [ ] `npm audit` - Check vulnerability count decreased
- [ ] `npm run dev:backend` - Ensure Python backend still runs
- [ ] `npm run dev:public` - Next.js public site starts without errors
- [ ] `npm run dev:oversight` - React Oversight Hub starts on port 3001
- [ ] `npm run build --workspaces` - Production build succeeds
- [ ] `npm run test:python` - Python tests still pass
- [ ] `npm run test` - Node/React tests still pass

### Critical Success Criteria:
✅ All three services start (`npm run dev`)  
✅ No console errors on any service  
✅ Oversight Hub loads and is responsive  
✅ Public Site renders correctly  
✅ Backend API endpoints respond  
✅ Test suite passes (as much as possible)

---

## Remaining Known Issues (Won't-Fix by npm alone)

### Deprecation Warnings (Non-Critical)
1. `cross-env` may warn about Node version compatibility
2. ESLint plugins may emit deprecation warnings during lint
3. Create-React-App scripts may show NODE_OPTIONS warnings

**Mitigation:** These don't affect functionality, just developer experience.

### Post-Update Attention Needed
1. Review any new linting errors introduced
2. Test all three services together (`npm run dev`)
3. Manual QA of Oversight Hub UI functionality
4. Check API integration between frontend and backend

---

## Success Metrics

| Metric | Before | Target | After |
|--------|--------|--------|-------|
| Total Vulnerabilities | 71 | <20 | TBD |
| Critical | 5 | 0 | TBD |
| High | 59 | <10 | TBD |
| Moderate | 7 | <10 | TBD |
| Build Success | ✅ | ✅ | TBD |
| Tests Pass | ✅ | ✅ | TBD |

---

## Rollback Plan

If any phase causes breaking changes:

1. **Immediate Rollback:**
   ```bash
   git checkout package-lock.json
   npm install
   npm run dev  # Test if services restart
   ```

2. **Document:** Record which versions caused issues, update plan for future iterations

3. **Alternative Approaches:**
   - Phase B: Revert react-scripts update, plan Vite migration instead
   - Phase C: If yaml-config can't be removed, patch/vendor the problematic code
   - Phase D: Revert individual package updates, target fewer dependencies

---

## Next Steps

1. **Execute Phase A** (Quick wins - can do now)
   ```bash
   npm audit fix --audit-level=none
   npm update @types/node @typescript-eslint/eslint-plugin @typescript-eslint/parser typescript prettier playwright
   npm install
   ```

2. **Test Phase A results:**
   ```bash
   npm run dev  # Should start all services
   npm audit    # Should show reduced vulnerability count
   ```

3. **If Phase A succeeds**, proceed to Phase B with testing
4. **Document results** in sprint/session notes for knowledge base
5. **Plan Phase 2** work: Vite migration or continued react-scripts updates

---

## Resources & References

- [npm audit documentation](https://docs.npmjs.com/cli/v8/commands/npm-audit)
- [react-scripts migration guide](https://github.com/facebook/create-react-app/blob/main/CHANGELOG.md)
- [Vite migration from react-scripts](https://vitejs.dev/guide/migration.html)
- [ESLint 10.x migration](https://eslint.org/docs/latest/use/migrate-to-10.0.0)
- [Jest 29.x → latest migration](https://jestjs.io/docs/29-migrate-to-latest)

---

## Questions & Decisions Needed

1. **React-Scripts:** Should we commit to fixing react-scripts or plan Vite migration now?
   - Current: Haven't decided
   - Recommendation: Phase B1 (update), Phase 3 (plan Vite)

2. **yaml-config:** Is this actually used in the project?
   - Current: Investigation needed
   - Recommendation: `npm ls yaml-config` to find source

3. **Breaking changes tolerance:** How much breaking change is acceptable?
   - Current: Assume low tolerance (incremental updates preferred)
   - Recommendation: Test thoroughly after each phase

---

## Appendix: Detailed Vulnerability List

### Critical (5 vulnerabilities)

**underscore (1.3.2 - 1.12.0)**
- Location: yaml-config bundle
- CVE: GHSA-cf4h-3jhx-xvhq
- Issue: Arbitrary Code Execution
- Fix: Remove yaml-config dependency
- Severity: CRITICAL

### High (59 vulnerabilities)

**ESLint dependency chain** (20+ high)
- minimatch vulnerability affects: @eslint/config-array, @eslint/eslintrc, @humanwhocodes/config-array
- Fix: Update ESLint and related packages to 10.x
- Impact: Linting process

**Jest/React-Scripts chain** (30+ high)
- @jest/* packages affected by rimraf, babel-plugin-istanbul
- jest-config, jest-runner, jest-runtime, jest-snapshot dependencies
- Fix: Update react-scripts to 5.0.1+ or migrate to Vite
- Impact: Build & test processes

**Other high:**
- glob (affects @jest/reporters)
- qs (prototype pollution)
- braces (ReDoS)
- postcss (CSS parsing)

### Moderate (7 vulnerabilities)

**tunnel-agent** - Memory exposure  
**qs** - DoS via memory exhaustion  
**underscore.string** - ReDoS  
**Others** - Various moderate issues

---

## Document History

- **v1.0** (Feb 22, 2026): Initial plan created during Phase 2C technical debt cleanup
- Created as part of comprehensive technical debt remediation
- Follows completion of 21 critical TODOs and type annotation fixes
