# Phase 3A: Dependency Updates Implementation

**Status:** Option A (Dependency Updates) Selected  
**Date:** February 22, 2026  
**Scope:** Safe, non-breaking dependency updates + Planning for major updates

---

## Current State Analysis

### NPM Dependency Situation
**Current:** 60 vulnerabilities (10 moderate, 48 high, 2 critical)

**Root Cause:** React-Scripts 5.0.1 (end-of-life) brings in outdated ecosystem:
- Jest 28.x (vulnerable, needs 29.x+)
- ESLint 8.x (vulnerable, needs 9.x+)
- Babel ecosystem (outdated)
- rimraf 3.x (vulnerable, needs 4.x+)

**Visualization:**
```
react-scripts 5.0.1 (EOL)
├── jest 28.x ← 48+ vulnerabilities
├── babel 7.x ← Build-related issues
├── eslint 8.x ← ESLint-related vulnerabilities
└── rimraf 3.x ← Deprecated
```

**To Fix:** Would require either:
1. react-scripts 6.x+ (doesn't exist in stable)
2. Migration to Vite (breaking change, but gains modern tooling)
3. Eject from CRA (complex, not recommended)

### Python Dependency Situation
**Status:** Poetry constraint conflict with pytest
- Issue: dependency-groups specifies `pytest (>=9.1.0,<10.0.0)` 
- But main dependencies have `pytest = ">=9.1.0"` (no upper bound)
- Result: Poetry can't resolve compatible versions

**To Fix:** Standardize pytest constraint across both configs

---

## Phase 3A: What CAN Be Done Now (Low Risk)

### Action 1: Fix Python Dependency Conflict ✅

**File:** `pyproject.toml`  
**Change:** Standardize pytest version constraint

Update `[tool.poetry.dependencies]` to match `[dependency-groups]`:

```toml
# Current:
pytest = ">=9.1.0"

# Change to:
pytest = ">=9.1.0,<10.0.0"
```

**Impact:**
- Allows poetry to resolve and update packages
- Enables verification of Python security status
- No breaking changes

---

### Action 2: Review Node Package Audit

**Safe Updates (Won't break react-scripts):**
```bash
# Update individual safe packages
npm update ajv                    # Utility package, safe
npm update axios                  # HTTP client, safe
npm update zustand                # State library, safe
npm update recharts               # Charts library, safe
npm update lucide-react           # Icons, safe
npm update firebase               # SDK, safe (if no breaking changes)
```

**Risky Updates (Affect react-scripts ecosystem):**
```bash
# These require react-scripts update first:
npm update eslint                 # ESLint - tied to react-scripts
npm update jest                   # Jest - tied to react-scripts
npm update rimraf                 # Build tool - tied to react-scripts
npm update babel                  # Build tool - tied to react-scripts
```

---

## Phase 3B: Major Update (Breaking Changes Required)

### Option B1: React-Scripts Update Strategy
**Decision Point:** What's the best path forward?

**Path A: Continue with react-scripts**
- Update 5.0.1 → 5.0.1 (latest 5.x)
- Problem: Still has vulnerabilities, still EOL
- Time: 30 minutes, low risk
- Benefit: Minimal security improvement

**Path B: Eject and Go Manual**
- Run `npm run eject` in oversight-hub
- Take full control of Webpack config
- Problem: High complexity, loss of CRA updates
- Time: 4-6 hours, high risk
- Benefit: Full control, can upgrade tools independently

**Path C: Migrate to Vite** ⭐ RECOMMENDED
- Completely replace Create-React-App with Vite
- Modern, fast build tool
- Brings latest ESLint, Jest, Babel automatically
- Time: 6-8 hours, medium risk (good docs available)
- Benefit: Modern tooling, better DX, fixes all vulnerabilities
- Plan: See Section "Vite Migration Plan" below

### Recommended Decision for User

**Until you choose B1, B2, or Vite:**
- ✅ Continue using current setup (it works)
- ✅ Make safe package updates (Action 2)
- ✅ Don't attempt major dependency updates
- ⚠️ Acknowledge: 48+ vulnerabilities remain due to react-scripts EOL

**Timeline Options:**
- **Next 2 weeks:** Small safe updates (Action 1+2), plan Vite migration
- **Next month:** Execute Vite migration (Phase 3B dedicated time)
- **Next sprint:** Complete modern tooling setup

---

## Action Items - Phase 3A

### Immediate (30 minutes)

**1. Fix pytest constraint in pyproject.toml:**

Change line 34 from:
```toml
pytest = ">=9.1.0"  # Testing framework
```

To:
```toml
pytest = ">=9.1.0,<10.0.0"  # Testing framework
```

Then run:
```bash
poetry lock
poetry install
```

**2. Verify Python environment:**
```bash
poetry show --outdated
npm run test:python  # Verify tests still pass
```

**3. Update safe Node packages:**
```bash
npm update ajv axios zustand recharts lucide-react
npm install
npm audit  # Check if count decreased
```

### Decision Point (1 hour discussion)

**Question:** React-Scripts Future?
- Keep as-is (acknowledge 48 vuln, but app works)
- Eject and customize (complex, time-intensive)
- Plan Vite migration (recommended, schedule for Phase 3B)

**Recommendation:** Plan Vite migration, continue with current setup until then

---

## Vite Migration Plan (Phase 3B)

### Why Vite?
- Modern, fast development server (HMR in <100ms vs CRA's seconds)
- Eliminates Create-React-App dependency constraints
- Built-in ESLint, Jest, Babel support
- Fixes all 48 ESLint/Jest/Babel vulnerabilities
- Better TypeScript support
- Smaller production bundles

### Migration Steps (6-8 hours total)

**Step 1: Create new Vite config** (1 hour)
```bash
cd web/oversight-hub
npm create vite@latest . -- --template react-ts
# Select React + TypeScript
```

**Step 2: Update configuration files** (1.5 hours)
- `vite.config.ts` - Adjust for project structure
- `tsconfig.json` - Align with Vite expectations
- Environment variables setup
- Port configuration (keep 3001)

**Step 3: Update entry point** (30 min)
- Convert `public/index.html` to root `index.html`
- Update `src/index.tsx` to Vite format
- Keep all React components as-is

**Step 4: Update npm scripts** (30 min)
```json
{
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest --run",
    "test:coverage": "vitest --run --coverage"
  }
}
```

**Step 5: Update dependencies** (1.5 hours)
```bash
# Remove:
npm uninstall react-scripts

# Add:
npm install --save-dev vite @vitejs/plugin-react
npm install --save-dev vitest @vitest/ui
npm update  # Update all remaining packages
```

**Step 6: Test thoroughly** (2 hours)
```bash
npm run dev        # Start development
npm run build      # Test production build
npm run test       # Run test suite
npm audit          # Verify vulnerability reduction
```

### Expected Outcome After Vite Migration
```
BEFORE (react-scripts):
- 60 vulnerabilities (10 moderate, 48 high, 2 critical)
- Build time: ~45 seconds
- HMR time: ~3 seconds
- Package size: ~500MB node_modules

AFTER (Vite):
- 0-5 vulnerabilities (mostly transitive unrelated stuff)
- Build time: ~5 seconds
- HMR time: ~100ms
- Package size: ~200MB node_modules
```

### Risk Assessment
- **Risk Level:** Medium (isolated to oversight-hub)
- **Rollback Path:** Keep node_modules backup, revert to react-scripts if needed
- **Testing Required:** Full UI test, API integration test
- **Breaking Changes:** Minimal (React components unchanged)

---

## Success Criteria - Phase 3A

### Completion Checklist
- [ ] Poetry pytest constraint fixed
- [ ] `poetry lock && poetry install` succeeds
- [ ] Safe npm packages updated
- [ ] Security decision made (keep current, Vite migration, or eject)
- [ ] Roadmap committed (if doing Vite)
- [ ] All services still start: `npm run dev` ✅

### Metrics
- [ ] Python tests still passing
- [ ] Node tests still passing  
- [ ] All three services start without errors
- [ ] Zero console errors on Oversight Hub
- [ ] Backend API endpoints responsive

---

## Not in Phase 3A Scope (Defer)

❌ **Will NOT fix in Phase 3A:**
- Type annotation cleanup (612+ pyright errors) → Phase 3B option
- Markdown linting (37+ errors) → Phase 3C
- Major npm vulnerabilities → Blocked by react-scripts EOL

✅ **ARE in Phase 3A scope:**
- Poetry constraint fix
- Safe package updates
- Planning/documentation
- Decision framework

---

## Next Steps After Phase 3A

**Option 1: Keep Current Setup (Recommended Short-term)**
- Run Phase 3A actions above
- Acknowledge: 48 vulnerabilities remain
- Plan Vite migration for next sprint
- App works fine, vulnerabilities don't affect functionality

**Option 2: Execute Vite Migration (Phase 3B)**
- Allocate 6-8 hours
- Follow Vite Migration Plan above
- Testing time: 2-3 hours
- Result: Modern tooling, zero react-scripts vulnerabilities

**Option 3: Eject and Customize (Not Recommended)**
- Complex webpack configuration management
- Loss of CRA updates and improvements
- Only do if Vite migration not possible for some reason

---

## Files to Update (Phase 3A)

**pyproject.toml** - Line 34
```diff
- pytest = ">=9.1.0"
+ pytest = ">=9.1.0,<10.0.0"
```

**No other files need changes for Phase 3A**

After decision on react-scripts future, may add:
- `vite.config.ts` (if Vite migration chosen)
- Updated scripts in `web/oversight-hub/package.json`
- Configuration files for Vite

---

## Timeline Summary

**Phase 3A (This session):** 1-2 hours
- Fix Poetry constraint ✅
- Safe npm updates ✅
- Planning ✅

**Phase 3B (If Vite chosen):** 6-8 hours
- Development time: 4 hours
- Testing: 2-3 hours
- Buffer: 1 hour

**Phase 3C (All others):** 
- Type annotations: 6-8 hours
- Markdown linting: 1-2 hours
- Optional cleanups

---

## Decision Needed from User

**Question:** After completing Phase 3A, should we:
1. Keep current setup + plan Vite for next sprint
2. Execute Vite migration now (6-8 hours)
3. Continue with other work (type annotations, etc.)

**Recommendation:** Option 1 (Keep current + plan Vite)
- Maintains momentum on feature work
- Acknowledges technical debt without blocking
- Clear path forward for when time available

---

*Phase 3A Implementation Plan - Ready to Execute*
