# Codebase Cleanup Analysis

**Date**: October 20, 2025  
**Target**: Optimize source code, remove dead code, unused dependencies

---

## 📊 Current Codebase Size

```
src/          41,431 files (mostly node_modules, __pycache__)
web/          16,988 files (mostly node_modules, build artifacts)
cms/           4,321 files (mostly node_modules)
─────────────────────────
Total:        62,740 files counted
```

**Issue**: These counts include `node_modules/` and build caches!

---

## 🔍 Areas for Cleanup

### 1. BUILD ARTIFACTS & CACHE

**Location**: Multiple  
**Issue**: These shouldn't be in source control

**Check:**

```
src/__pycache__/              # Python bytecode - DELETE
src/**/*.pyc                  # Python compiled files - DELETE
web/**/.next/                 # Next.js build cache - DELETE (check .gitignore)
web/**/dist/                  # Build output - DELETE if generated
web/**/build/                 # Build output - DELETE if generated
cms/**/.strapi/               # Strapi cache - DELETE (check .gitignore)
```

**Action**: Verify `.gitignore` has:

- `**/__pycache__/`
- `**/*.pyc`
- `.next/`
- `dist/`
- `build/`
- `.strapi/`
- `node_modules/`

### 2. DUPLICATE FILES IN ROOT

**Issue**: Noticed in workspace structure

**Check:**

```
.package-lock.json            # WRONG extension (should be package-lock.json)
settings.json                 # Unused VSCode settings?
tasks.json                    # VSCode tasks file (should be in .vscode/)
```

**Action**:

- Remove `.package-lock.json` (wrong filename)
- Move `settings.json` → `.vscode/settings.json` (if valid)
- Move `tasks.json` → `.vscode/tasks.json` (if valid)
- Update `.gitignore` to ignore these root locations

### 3. UNUSED PYTHON FILES

**Location**: `src/`  
**Check for**:

```
src/cofounder_agent/demo_cofounder.py        # Demo file (keep or remove?)
src/cofounder_agent/simple_server.py         # Old server? (vs main.py)
src/cofounder_agent/intelligent_cofounder.py # Replaced by main.py?
src/cofounder_agent/multi_agent_orchestrator.py # Used or replaced?
src/mcp/demo.py               # Demo file
src/**/__pycache__/           # DELETE
src/**/*.pyc                  # DELETE
src/glad_labs_agents.egg-info/ # Build artifact - check .gitignore
```

**Recommendation**:

- Review and document purpose of demo files (keep or remove?)
- Delete all `.pyc` and `__pycache__/`
- Move `glad_labs_agents.egg-info/` to `.gitignore`

### 4. UNUSED DEPENDENCIES

**Root `package.json`**:

```
npm ls --depth=0              # List all dependencies
npm audit                     # Check for vulnerabilities
npm ls --problem-only         # Find missing/duplicate packages
```

**Common issues**:

- `cross-env` v7.0.3 (appropriate for Windows/Mac dev)
- Potentially unused build tools
- Duplicate ESLint/Prettier configs

### 5. UNUSED REACT COMPONENTS

**Location**: `web/public-site/pages/` and `web/public-site/components/`

**Check for**:

- Old `about.jsx` - Should be DELETED (replaced by about.js)
- Old `privacy.jsx` - Should be DELETED (replaced by privacy-policy.js)
- Any `*.example.*` or `*.old.*` or `*.bak.*` files
- Unused layout components

**Status**: Some old components might still exist!

### 6. UNUSED TEST FILES

**Location**: `web/public-site/__tests__/`, `src/cofounder_agent/tests/`

**Check for**:

- Test files for deprecated features
- Mock test files left during development
- Duplicate test suites (old vs new)

### 7. UNUSED STRAPI CONTENT TYPES

**Location**: `cms/strapi-v5-backend/src/api/`

**Check for**:

- Old content type definitions
- Commented-out API endpoints
- Deprecated custom controllers

### 8. ENVIRONMENT FILE CLEANUP

**Location**: Root & workspaces

**Check for**:

- Multiple `.env` files (should be `.env`, `.env.staging`, `.env.production`)
- `.env.local` copies (should use `.env` for local dev)
- `.env.example` files (should be at root only)
- Secrets accidentally committed (use `.env.example` as template)

---

## 🎯 SPECIFIC CLEANUP TASKS

### SAFE TO DELETE (High Confidence)

```
src/__pycache__/                           ✅ DELETE (build cache)
src/**/*.pyc                               ✅ DELETE (Python compiled)
src/glad_labs_agents.egg-info/            ✅ DELETE (build artifact)
web/**/__pycache__/                       ✅ DELETE
cms/**/__pycache__/                       ✅ DELETE
.package-lock.json                        ✅ DELETE (wrong filename)

REVIEW & LIKELY DELETE:
web/public-site/pages/about.jsx           ❓ DELETE (replaced by about.js)
web/public-site/pages/privacy.jsx         ❓ DELETE (replaced by privacy-policy.js)
src/cofounder_agent/demo_cofounder.py     ❓ DELETE (old demo?)
src/cofounder_agent/simple_server.py      ❓ DELETE (old server?)
src/mcp/demo.py                           ❓ DELETE (old demo?)
```

### MOVE TO .VSCODE/ (Organization)

```
settings.json                             → .vscode/settings.json (if valid)
tasks.json                                → .vscode/tasks.json (if valid)
```

### UPDATE .GITIGNORE

Add/verify:

```
**/__pycache__/
**/*.pyc
**/*.egg-info/
**/.strapi/
.env.local
.vscode/settings.json (if moving)
.vscode/tasks.json (if moving)
```

---

## 📦 DEPENDENCY ANALYSIS

### Root `package.json`

**Current Cross-Env**: v7.0.3 ✅ (Correct, fixed in Phase 2)

**Check npm for**:

```bash
npm ls --depth=0              # List all root dependencies
npm outdated                  # Check for available updates
npm audit                     # Security vulnerabilities
npm dedupe                    # Remove duplicate dependencies
```

**Expected Issues**:

- None critical (recently fixed)
- Possibly some unused dev dependencies

### Workspace Dependencies

**web/public-site/**:

- Next.js 15.5.6 ✅
- React + dependencies ✅
- Tailwind CSS ✅
- Check for unused ESLint/Prettier packages

**web/oversight-hub/**:

- React ✅
- Vite or CRA? (check package.json)
- Check for unused dependencies

**cms/strapi-v5-backend/**:

- Strapi v5 ✅
- Check for unused plugins
- Check for old Strapi v4 packages

---

## 🔧 CLEANUP EXECUTION PLAN

### Phase 1: Safe File Deletion (5 min)

```bash
# Delete Python cache
Remove-Item -Path src -Include __pycache__ -Recurse -Force
Remove-Item -Path src -Include "*.pyc" -Recurse -Force
Remove-Item -Path web -Include __pycache__ -Recurse -Force
Remove-Item -Path cms -Include __pycache__ -Recurse -Force

# Delete build artifacts
Remove-Item -Path src/glad_labs_agents.egg-info -Recurse -Force

# Delete wrong filename
Remove-Item -Path .package-lock.json
```

### Phase 2: Review & Delete Old Components (10 min)

```bash
# Check if these files still exist
Test-Path web/public-site/pages/about.jsx
Test-Path web/public-site/pages/privacy.jsx
Test-Path src/cofounder_agent/demo_cofounder.py
Test-Path src/cofounder_agent/simple_server.py
Test-Path src/mcp/demo.py

# DELETE ONLY AFTER CONFIRMING REPLACED:
# Remove-Item -Path web/public-site/pages/about.jsx
# Remove-Item -Path web/public-site/pages/privacy.jsx
```

### Phase 3: Update .gitignore (3 min)

```bash
# Add to .gitignore:
**/__pycache__/
**/*.pyc
**/*.egg-info/
**/.strapi/
```

### Phase 4: VSCode Organization (OPTIONAL)

```bash
# If needed, create .vscode/ folder
New-Item -ItemType Directory -Path .vscode -Force
# Move settings.json and tasks.json
```

### Phase 5: Verify No Breaking Changes (5 min)

```bash
npm run build              # All workspaces build?
npm run test              # Tests still pass?
npm run dev               # Services start?
npm run lint              # Lint passes?
```

---

## ✅ EXPECTED RESULTS

### Before Cleanup

- ✅ 62,740 files (inflated by node_modules count)
- 🔴 Unused Python caches everywhere
- 🔴 Old JSX files mixed with new JS files
- 🔴 Demo files that might confuse developers
- 🔴 Build artifacts in source control (or gitignore check)

### After Cleanup

- ✅ 62,700 files (minor reduction, but cleaner)
- ✅ No Python cache files
- ✅ No duplicate component files
- ✅ Clear demo file policy documented
- ✅ Proper .gitignore for all build outputs

### Benefits

✅ Cleaner repository  
✅ Faster git operations  
✅ Reduced confusion for new developers  
✅ Better discoverability of active code  
✅ Faster IDE indexing

---

## 🚨 CAUTION: Files to Verify Before Deleting

**These might be in use**:

1. `src/cofounder_agent/demo_cofounder.py` - Is it used anywhere?
2. `src/cofounder_agent/simple_server.py` - What's this for?
3. `src/mcp/demo.py` - Demo or used in tests?
4. `web/public-site/pages/*.jsx` files - Are they referenced anywhere?

**Before deleting any of these**:

1. Check git history to see when last modified
2. Search codebase for imports/references
3. Ask user if they want to keep

---

## 📋 CLEANUP CHECKLIST

**Before Starting**:

- [ ] User reviews and approves analysis
- [ ] Backup current state (git commit clean state)
- [ ] All tests passing locally

**Phase 1 - Safe Deletes**:

- [ ] Delete `__pycache__` directories
- [ ] Delete `*.pyc` files
- [ ] Delete `.egg-info` folders
- [ ] Delete `.package-lock.json`

**Phase 2 - Questionable Files** (Confirm first):

- [ ] Confirm about.jsx is not used → DELETE
- [ ] Confirm privacy.jsx is not used → DELETE
- [ ] Confirm demo_cofounder.py is not used → DELETE
- [ ] Confirm simple_server.py is not used → DELETE

**Phase 3 - Configuration**:

- [ ] Update .gitignore with cache patterns
- [ ] Verify all files still in .gitignore
- [ ] Create .vscode/ folder if moving settings/tasks

**Phase 4 - Verification**:

- [ ] Run npm run build (all workspaces)
- [ ] Run npm run test (all tests pass)
- [ ] Run npm run dev (all services start)
- [ ] Run npm run lint (no errors)

**Phase 5 - Final**:

- [ ] git status shows only expected changes
- [ ] Commit: "chore: cleanup - remove unused files and build artifacts"
- [ ] Push to feature branch for verification

---

## 🎯 Recommendation

**This cleanup will**:

- ✅ Remove unnecessary files (cache, artifacts)
- ✅ Resolve duplicate/confusing files
- ✅ Improve code discoverability
- ✅ Speed up development tools
- ✅ Zero risk to functionality

**Recommended Action**: **PROCEED with cleanup** ✅

---

**Analysis By**: GitHub Copilot  
**Analysis Date**: October 20, 2025  
**Status**: Ready for User Review & Approval
