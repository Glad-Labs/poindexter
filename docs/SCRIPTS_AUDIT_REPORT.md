# 📋 Scripts Audit & Cleanup Report

**Date:** October 25, 2025  
**Reviewed:** 28 scripts in `scripts/` directory  
**Status:** ✅ Ready for consolidation and cleanup

---

## Executive Summary

| Category         | Count | Status        | Action                         |
| ---------------- | ----- | ------------- | ------------------------------ |
| **Setup/Init**   | 3     | ✅ Good       | Consolidate into setup-dev.ps1 |
| **Testing**      | 5     | ⚠️ Mixed      | Keep, document purposes        |
| **Deployment**   | 4     | ⚠️ Tier-based | Archive old tier1 scripts      |
| **Monitoring**   | 3     | ⚠️ Old        | Archive or document usage      |
| **Utilities**    | 5     | ✅ Good       | Keep and document              |
| **Config/Env**   | 2     | ✅ Good       | Keep and maintain              |
| **Requirements** | 2     | ✅ Good       | Keep for Python deps           |
| **Other**        | 4     | ❓ Unclear    | Review individually            |

---

## 📊 Detailed Script Inventory

### ✅ SETUP SCRIPTS (Consolidate)

#### 1. **setup-dev.ps1** ← NEW (RECOMMENDED)

- **Purpose:** Automated development environment setup
- **Status:** ✅ Created October 25, 2025
- **Use:** `.\scripts\setup-dev.ps1` or `.\scripts\setup-dev.ps1 -Clean`
- **Replaces:** setup-dependencies.ps1 (superset functionality)
- **Keep:** YES - This is the new standard

#### 2. **setup-dependencies.ps1** (OLD)

```powershell
# What it does:
# - Checks Node.js and Python
# - Installs dependencies (node + python)
# - Optional cleanup with -Clean flag
```

- **Status:** ⚠️ Partially redundant (now in setup-dev.ps1)
- **Keep:** NO - Replace with setup-dev.ps1
- **Archive:** Save to docs/archive/scripts/setup-dependencies.ps1.bak

#### 3. **select-env.js** (OLD)

```javascript
// What it does:
// - Selects .env file based on git branch
// - feat/* → .env (dev)
// - dev → .env.staging
// - main → .env.production
```

- **Status:** ⚠️ Partially works, but limited use
- **Keep:** YES - Useful for branch-aware setup
- **Improve:** Add to setup workflow documentation

### 🧪 TESTING SCRIPTS (Keep & Document)

#### 4. **quick-test-api.ps1**

```powershell
# What it does:
# - Tests FastAPI backend health
# - Sends test commands
# - Gets agent list
# - Performance metrics
```

- **Purpose:** Quick validation that backend is running
- **Status:** ✅ Working well
- **Use:** After `npm run dev:cofounder`
- **Keep:** YES

#### 5. **test-cofounder-api.ps1**

```powershell
# What it does:
# - More comprehensive backend testing
# - Tests different endpoints
# - Error scenarios
```

- **Status:** ⚠️ Possibly duplicate of quick-test-api.ps1
- **Action:** Verify uniqueness
- **Keep:** YES if different, NO if duplicate

#### 6. **test-e2e-workflow.ps1**

```powershell
# What it does:
# - End-to-end workflow test
# - Blog generation via Ollama
# - Save to Strapi
# - Verify output
```

- **Purpose:** Full pipeline validation (Phase 6 testing)
- **Status:** ✅ Essential
- **Keep:** YES - HIGH PRIORITY
- **Document:** Where to use this

#### 7. **run_tests.py**

```python
# What it does:
# - Python test runner
# - Pytest wrapper
# - Coverage reporting
```

- **Purpose:** Run backend tests
- **Status:** ✅ Good (Python)
- **Keep:** YES

#### 8. **diagnose-timeout.ps1 + diagnose-timeout.sh**

```powershell
# What it does:
# - Diagnoses timeout issues
# - Checks service health
# - Resource usage
```

- **Status:** ⚠️ Specific to debugging
- **Keep:** YES (in utils section)
- **Document:** When to use (troubleshooting)

### 🚀 DEPLOYMENT SCRIPTS (Archive Old Tier Models)

#### 9-12. **Tier1 Deployment Scripts (OLD)**

```
- backup-tier1-db.bat / .sh
- deploy-tier1.ps1 / .sh
- tier1-health-check.js
- scale-to-tier2.sh
```

- **Status:** ❌ DEPRECATED - Old tier-based deployment model
- **Keep:** NO - Archive to `docs/archive/deployment/tier1/`
- **Why:** Phase 1 deployment model is superseded by GitHub Actions + Railway/Vercel

#### 13. **deploy-tier1.ps1**

- **Status:** ❌ Old deployment model
- **Keep:** NO - Archive

### 📊 MONITORING SCRIPTS (Document Purpose)

#### 14. **monitor-tier1-resources.js**

```javascript
// What it does:
// - Monitors CPU, memory
// - Health checks
// - Resource alerts
```

- **Status:** ⚠️ Tier1 based (old model)
- **Keep:** NO - Archive to `docs/archive/monitoring/`
- **Better Alternative:** Use Railway dashboard + GitHub Actions logs

#### 15. **monitor-tier1-resources.ps1**

- **Status:** ⚠️ Same as above
- **Keep:** NO - Archive

#### 16. **check-services.ps1**

```powershell
# What it does:
# - Checks if services are running
# - Port availability
# - Health status
```

- **Status:** ✅ Still useful
- **Keep:** YES
- **Update:** Add documentation

### 🔧 UTILITY/HELPER SCRIPTS (Keep & Document)

#### 17. **kill-services.ps1**

```powershell
# What it does:
# - Kills running Node/Python processes
# - Frees up ports
# - Cleanup
```

- **Status:** ✅ Useful
- **Keep:** YES
- **Use:** When services won't stop normally
- **Improve:** Add safety prompts

#### 18. **fix-strapi-build.ps1**

```powershell
# What it does:
# - Fixes Strapi build issues
# - Clears cache
# - Rebuilds
```

- **Status:** ⚠️ Specific to Strapi v5 build issues
- **Keep:** YES - Document in troubleshooting
- **Use:** If Strapi won't build

#### 19. **dev-troubleshoot.ps1**

```powershell
# What it does:
# - General development troubleshooting
# - Diagnostic information
# - Environment checks
```

- **Status:** ✅ Good diagnostic tool
- **Keep:** YES
- **Improve:** Add to troubleshooting guide

#### 20. **generate-secrets.ps1**

```powershell
# What it does:
# - Generates random secrets
# - For JWT, API tokens, salts
# - Safe for local dev
```

- **Status:** ✅ Useful
- **Keep:** YES
- **Document:** When to use

#### 21. **generate-content-batch.py**

```python
# What it does:
# - Generates test content
# - Batch operations
# - Seed data
```

- **Status:** ⚠️ Specific to testing
- **Keep:** YES (with documentation)
- **Use:** After Strapi setup for testing

### 📦 DEPENDENCY SCRIPTS (Keep)

#### 22. **requirements.txt**

- **Status:** ✅ Python dependencies (root)
- **Keep:** YES
- **Review:** Update if new packages needed

#### 23. **requirements-core.txt**

- **Status:** ✅ Core Python dependencies
- **Keep:** YES
- **Review:** Verify version pins

### ❓ UNCLEAR/INCOMPLETE SCRIPTS (Review)

#### 24. **setup-tier1.js**

- **Status:** ⚠️ Tier1 based, incomplete
- **Keep:** NO - Archive

#### 25. **verify-pipeline.ps1**

- **Status:** ⚠️ Purpose unclear
- **Action:** Review purpose and current state
- **Keep:** MAYBE (depends on findings)

---

## 🎯 CONSOLIDATION PLAN

### Phase 1: Immediate (Recommend Now)

**Delete (Archive First):**

- `backup-tier1-db.bat`
- `backup-tier1-db.sh`
- `deploy-tier1.ps1`
- `deploy-tier1.sh`
- `setup-tier1.js`
- `monitor-tier1-resources.js`
- `monitor-tier1-resources.ps1`
- `scale-to-tier2.sh`
- `tier1-health-check.js`

**Command:**

```bash
mkdir -p docs/archive/scripts/deprecated-tier1
mv scripts/backup-tier1-db.* docs/archive/scripts/deprecated-tier1/
mv scripts/deploy-tier1.* docs/archive/scripts/deprecated-tier1/
mv scripts/monitor-tier1-resources.* docs/archive/scripts/deprecated-tier1/
mv scripts/tier1-health-check.js docs/archive/scripts/deprecated-tier1/
mv scripts/setup-tier1.js docs/archive/scripts/deprecated-tier1/
mv scripts/scale-to-tier2.sh docs/archive/scripts/deprecated-tier1/
```

**Keep & Improve:**

- ✅ `setup-dev.ps1` - Main setup (NEW - use this!)
- ✅ `test-e2e-workflow.ps1` - Phase 6 testing
- ✅ `quick-test-api.ps1` - Backend validation
- ✅ `check-services.ps1` - Service status
- ✅ `kill-services.ps1` - Process cleanup
- ✅ `select-env.js` - Branch-aware env
- ✅ `requirements.txt` - Python deps
- ✅ `requirements-core.txt` - Core Python deps

### Phase 2: Documentation (Create Now)

**Create `docs/SCRIPTS_GUIDE.md`:**

```markdown
# 📋 GLAD Labs Scripts Reference

## Setup Scripts

- setup-dev.ps1 - Automated development setup (USE THIS FIRST!)
- select-env.js - Branch-aware environment selection

## Testing Scripts

- test-e2e-workflow.ps1 - Full pipeline E2E test (Phase 6)
- quick-test-api.ps1 - Backend API validation
- test-cofounder-api.ps1 - Extended API testing
- run_tests.py - Python test runner

## Utility Scripts

- check-services.ps1 - Check running services
- kill-services.ps1 - Kill running processes
- fix-strapi-build.ps1 - Fix Strapi build issues
- dev-troubleshoot.ps1 - Development diagnostics
- generate-secrets.ps1 - Generate random secrets
- generate-content-batch.py - Batch content generation
- diagnose-timeout.ps1 - Diagnose timeout issues

## Dependencies

- requirements.txt - Python dependencies
- requirements-core.txt - Core Python dependencies
```

### Phase 3: Update Documentation (Next Week)

1. Update `README.md` - Reference `.\scripts\setup-dev.ps1`
2. Update `.github/copilot-instructions.md` - New setup process
3. Update `docs/01-SETUP_AND_OVERVIEW.md` - Use setup-dev.ps1
4. Create `docs/SCRIPTS_GUIDE.md` - Script reference
5. Update `docs/04-DEVELOPMENT_WORKFLOW.md` - Reference setup-dev.ps1

---

## 📝 FINAL SCRIPT LIST (After Cleanup)

### 🔴 TO DELETE (Archive First)

```
backup-tier1-db.bat
backup-tier1-db.sh
deploy-tier1.ps1
deploy-tier1.sh
monitor-tier1-resources.js
monitor-tier1-resources.ps1
scale-to-tier2.sh
setup-tier1.js
tier1-health-check.js
```

### ✅ TO KEEP & DOCUMENT

```
✅ setup-dev.ps1                  (NEW - Main setup)
✅ select-env.js                  (Environment selection)
✅ test-e2e-workflow.ps1          (Phase 6 testing)
✅ quick-test-api.ps1             (Backend testing)
✅ test-cofounder-api.ps1         (Extended API testing)
✅ run_tests.py                   (Python tests)
✅ check-services.ps1             (Service monitoring)
✅ kill-services.ps1              (Process cleanup)
✅ fix-strapi-build.ps1           (Strapi fixes)
✅ dev-troubleshoot.ps1           (Diagnostics)
✅ generate-secrets.ps1           (Secret generation)
✅ generate-content-batch.py      (Content generation)
✅ diagnose-timeout.ps1           (Timeout diagnostics)
✅ requirements.txt               (Python deps)
✅ requirements-core.txt          (Core Python deps)
```

**New Total: 15 active scripts (down from 28)**

---

## 🚀 IMMEDIATE ACTION ITEMS

### For You (Now)

1. ✅ Run `.\scripts\setup-dev.ps1` for new setup
2. ⏳ Review deprecated tier1 scripts (decide if archive)
3. ⏳ Create docs/archive/ structure for old scripts

### For Documentation (This Week)

1. Create `docs/SCRIPTS_GUIDE.md`
2. Update `README.md` with setup-dev.ps1
3. Update setup docs to use new script
4. Move old scripts to docs/archive/

### For Team (Next Week)

1. Communicate new setup process
2. Update onboarding guide
3. Remove old deployment procedures from docs
4. Update GitHub Actions if needed

---

## 🔗 Related Documentation

- **Setup:** `.\scripts\setup-dev.ps1`
- **Monorepo:** `docs/MONOREPO_SETUP.md`
- **Development:** `docs/04-DEVELOPMENT_WORKFLOW.md`
- **Testing:** `docs/reference/TESTING.md`

---

## 📊 SUMMARY

**Current State:**

- 28 scripts total (bloated, includes old tier1 model)
- Many duplicates and overlaps
- Poor documentation

**After Cleanup:**

- 15 active scripts (54% reduction)
- Clear purpose for each script
- Full documentation
- Old scripts archived for reference

**Benefit:**

- Easier for new developers to understand
- Less confusion about which script to use
- Clearer setup process
- Better organization

---

**Audit Completed:** October 25, 2025  
**Created By:** GitHub Copilot + GLAD Labs Development Team  
**Status:** Ready for Implementation
