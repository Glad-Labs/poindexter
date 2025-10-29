# 📋 Documentation Cleanup Execution Plan

**Date:** October 28, 2025  
**Policy:** HIGH-LEVEL ONLY Documentation  
**Goal:** Clean, maintainable documentation structure with <20 total files  
**Status:** READY TO EXECUTE

---

## 🎯 Cleanup Strategy

Following the "High-Level Only" documentation policy from `docs_cleanup.prompt.md`:

**KEEP:** Core docs (00-07), reference specs, focused troubleshooting, component READMEs  
**ARCHIVE:** Phase summaries, session notes, status updates, dated files, duplicates  
**DELETE:** Nothing permanently, move to archive/

---

## 📁 File Classification & Actions

### ✅ KEEP - Core Documentation (8 files)

These define the system architecture and are evergreen:

```text
docs/
├── 00-README.md ✅ MAIN HUB - Navigation
├── 01-SETUP_AND_OVERVIEW.md ✅ Getting started (evergreen)
├── 02-ARCHITECTURE_AND_DESIGN.md ✅ System design (evergreen)
├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md ✅ Production procedures (evergreen)
├── 04-DEVELOPMENT_WORKFLOW.md ✅ Git strategy (evergreen)
├── 05-AI_AGENTS_AND_INTEGRATION.md ✅ Agent architecture (evergreen)
├── 06-OPERATIONS_AND_MAINTENANCE.md ✅ Operations (evergreen)
└── 07-BRANCH_SPECIFIC_VARIABLES.md ✅ Environment config (evergreen)
```

**Action:** ✅ KEEP (No changes)

---

### 📊 KEEP - Reference Documentation (14 files)

Technical specifications and standards that stay relevant:

```text
docs/reference/
├── API_CONTRACT_CONTENT_CREATION.md ✅ API spec
├── GLAD-LABS-STANDARDS.md ✅ Code standards (v2.0)
├── TESTING.md ✅ Comprehensive testing guide (93+ tests documented)
├── GITHUB_SECRETS_SETUP.md ✅ Deployment secrets reference
├── npm-scripts.md ✅ Build script documentation
├── data_schemas.md ✅ Database schema reference
├── ci-cd/BRANCH_HIERARCHY_QUICK_REFERENCE.md ✅ Branch strategy
├── ci-cd/GITHUB_ACTIONS_REFERENCE.md ✅ GitHub Actions workflows
├── POWERSHELL_API_QUICKREF.md ✅ API testing reference
├── QUICK_REFERENCE_CONSOLIDATED.md ✅ Quick lookup
├── QUICK_FIXES.md ✅ Common solutions
├── E2E_TESTING.md ✅ End-to-end testing guide
├── TESTING_GUIDE.md ✅ Detailed testing procedures
└── TESTING_QUICK_START.md ✅ 5-minute testing intro
```

**Action:** ✅ KEEP (No changes)

---

### 🚨 KEEP - Troubleshooting (5 files)

Focused, solution-oriented troubleshooting:

```text
docs/troubleshooting/
├── 01-railway-deployment.md ✅ Railway-specific issues
├── 04-build-fixes.md ✅ Build error solutions
├── 05-compilation.md ✅ TypeScript/Python compilation
└── components/strapi-cms/troubleshooting/
    ├── STRAPI_V5_PLUGIN_ISSUE.md ✅ Plugin conflicts
    └── STRAPI_SETUP_WORKAROUND.md ✅ Setup workarounds
```

**Action:** ✅ KEEP (No changes)

---

### 📦 KEEP - Component Documentation (4 files)

Component-specific architecture:

```text
docs/components/
├── cofounder-agent/README.md ✅ Agent architecture
├── oversight-hub/README.md ✅ Dashboard architecture
├── public-site/README.md ✅ Frontend architecture
└── strapi-cms/README.md ✅ CMS architecture
```

**Action:** ✅ KEEP (No changes)

---

### 🚚 ARCHIVE - Phase Documentation (Outdated)

**Location:** `docs/` → **Move to** `docs/archive/`

```text
Phase Documentation to Archive:
├── PHASE_5_CLEANUP_SUMMARY.md ➜ archive/
├── PHASE_7_ACCESSIBILITY_TESTING.md ➜ archive/
└── PHASE_7_BUILD_SUCCESS.md ➜ archive/
```

**Reason:** Phase summaries are session-specific, not architectural  
**Action:** 🚚 MOVE to archive/ (already has many phase files)

---

### 🚚 ARCHIVE - Root-Level Temporary Documentation

**Location:** Root directory → **Move to** `docs/archive/`

```text
Temporary/Session Docs to Archive:
├── ASYNC_POSTGRESQL_FIX_SUMMARY.md ➜ archive/ (session-specific)
├── DEPLOYMENT_FIXES_2025-10-27.md ➜ archive/ (dated)
├── PHASE_7_SESSION_SUMMARY.md ➜ archive/ (session notes)
└── COMPREHENSIVE_TODO_LIST.md ➜ docs/reference/ (SPECIAL - keep accessible)
```

**Why Separate?**

- ASYNC_POSTGRESQL_FIX_SUMMARY: Specific to one deployment fix
- DEPLOYMENT_FIXES_2025-10-27: Dated, specific to one day
- PHASE_7_SESSION_SUMMARY: Session notes
- COMPREHENSIVE_TODO_LIST: **KEEP IN ROOT or docs/** (useful reference)

**Action:** 🚚 MOVE most to archive, KEEP TODO_LIST in root for visibility

---

### 🚚 ARCHIVE - Web/Public-Site Documentation

**Location:** `web/public-site/` → **Move to** `docs/archive/`

```text
Phase Files to Archive:
├── PHASE_6_SUMMARY.md ➜ archive/
├── PHASE_6_COMPLETION_REPORT.md ➜ archive/
├── PHASE_6_ANALYTICS.md ➜ archive/
├── PHASE_7_PLAN.md ➜ archive/
└── README.md ✅ KEEP (component architecture)
```

**Reason:** Phase summaries are historical, not part of component architecture  
**Action:** 🚚 MOVE to archive/ (keep only README.md)

---

### ✅ VERIFY - Already in Archive

The following are already archived correctly:

```text
docs/archive/
├── FULL_MONOREPO_ARCHITECTURE_ANALYSIS.md ✅
├── FIRESTORE_REMOVAL_PLAN.md ✅
├── GITHUB_ACTIONS_FIX.md ✅
└── ... (50+ historical files) ✅
```

**Action:** ✅ VERIFY these are kept as historical reference (no changes)

---

## 🔄 Execution Steps

### STEP 1: Move Phase Documentation from docs/ to archive/ (2 min)

```powershell
cd c:\Users\mattm\glad-labs-website\docs

# Move phase files to archive
move PHASE_5_CLEANUP_SUMMARY.md archive/
move PHASE_7_ACCESSIBILITY_TESTING.md archive/
move PHASE_7_BUILD_SUCCESS.md archive/

# Verify clean structure
ls -la  # Should show only 00-07, archive/, components/, reference/, troubleshooting/
```

**Verification:** Only 8 numbered files + 4 folders remain

---

### STEP 2: Move Root-Level Session Docs to Archive (2 min)

```powershell
cd c:\Users\mattm\glad-labs-website

# Move temporary session docs
move ASYNC_POSTGRESQL_FIX_SUMMARY.md docs/archive/
move DEPLOYMENT_FIXES_2025-10-27.md docs/archive/
move PHASE_7_SESSION_SUMMARY.md docs/archive/

# Keep COMPREHENSIVE_TODO_LIST.md in root (visible, important)
# Verify
ls *.md  # Should show: README.md, COMPREHENSIVE_TODO_LIST.md, LICENSE.md only
```

**Verification:** Root has only README, LICENSE, TODO_LIST

---

### STEP 3: Clean up web/public-site documentation (2 min)

```powershell
cd c:\Users\mattm\glad-labs-website\web\public-site

# Move phase files to main docs archive
move PHASE_6_SUMMARY.md ..\..\docs\archive\
move PHASE_6_COMPLETION_REPORT.md ..\..\docs\archive\
move PHASE_6_ANALYTICS.md ..\..\docs\archive\
move PHASE_7_PLAN.md ..\..\docs\archive\

# Keep README.md (component architecture)
# Verify
ls *.md  # Should show: README.md only
```

**Verification:** Only README.md remains

---

### STEP 4: Update docs/00-README.md (5 min)

**Current:**

- Documents high-level policy
- Lists core docs
- Lists reference docs
- Lists troubleshooting
- Lists components

**Updates Needed:**

- Remove "PHASE\_\* files in root" section
- Add note: "Phase documentation archived for historical reference"
- Confirm total file count reduced
- Update last updated date
- Add link to COMPREHENSIVE_TODO_LIST.md from root

**Action:** Read and update docs/00-README.md

---

### STEP 5: Verify Documentation Structure (3 min)

```powershell
# List all docs files
Get-ChildItem -Path docs -Recurse -Filter "*.md" | Measure-Object
# Expected: ~40-50 files total (8 core + 14 reference + 5 troubleshooting + 4 components + archive/)

# Verify no orphaned files
Get-ChildItem -Path docs -File -Filter "*.md"
# Expected: 8 files (00-07)

Get-ChildItem -Path docs/reference -File -Filter "*.md"
# Expected: 14 files

Get-ChildItem -Path docs/troubleshooting -File -Filter "*.md"
# Expected: 5 files

Get-ChildItem -Path docs/components -Recurse -Filter "README.md"
# Expected: 4 files
```

---

### STEP 6: Git Operations (3 min)

```powershell
cd c:\Users\mattm\glad-labs-website

# Stage all documentation moves
git add -A

# Verify changes (should show moves, no deletes)
git status

# Commit with clear message
git commit -m "docs: consolidate to high-level only policy

- Move phase files (PHASE_5/6/7) to archive/ for historical reference
- Move session-specific docs (ASYNC_FIX_SUMMARY, DEPLOYMENT_FIXES) to archive/
- Keep COMPREHENSIVE_TODO_LIST.md in root for visibility
- Reduce root-level clutter (3 .md files → 3 .md files but organized)
- Clean web/public-site (5 phase files → 1 README.md)
- Update docs/ structure: Only 8 core + reference + troubleshooting + components
- Follow high-level only documentation policy from docs_cleanup.prompt.md

Documentation Structure:
- Core (8): 00-07 evergreen architecture docs
- Reference (14): Technical specs, standards, API contracts
- Troubleshooting (5): Focused problem solutions
- Components (4): Component architecture READMEs
- Archive: All phase summaries, session notes, dated files
- Total active docs: ~31 files (down from 100+)"
```

---

## 📊 Summary of Changes

### Before Cleanup

```text
docs/
  ├── 00-07 (8 core files) ✅
  ├── PHASE_5_CLEANUP_SUMMARY.md ❌
  ├── PHASE_7_ACCESSIBILITY_TESTING.md ❌
  ├── PHASE_7_BUILD_SUCCESS.md ❌
  ├── archive/ (50+ files) ✅
  ├── components/ ✅
  ├── reference/ ✅
  └── troubleshooting/ ✅

Root:
  ├── README.md ✅
  ├── ASYNC_POSTGRESQL_FIX_SUMMARY.md ❌
  ├── DEPLOYMENT_FIXES_2025-10-27.md ❌
  ├── PHASE_7_SESSION_SUMMARY.md ❌
  └── COMPREHENSIVE_TODO_LIST.md (new) ✅

web/public-site/:
  ├── README.md ✅
  ├── PHASE_6_SUMMARY.md ❌
  ├── PHASE_6_COMPLETION_REPORT.md ❌
  ├── PHASE_6_ANALYTICS.md ❌
  └── PHASE_7_PLAN.md ❌

TOTAL ACTIVE: ~100+ files (bloated)
```

### After Cleanup

```text
docs/
  ├── 00-07 (8 core files) ✅
  ├── archive/ (60+ historical files) ✅
  ├── components/ (4 READMEs) ✅
  ├── reference/ (14 spec files) ✅
  └── troubleshooting/ (5 solution files) ✅

Root:
  ├── README.md ✅
  ├── COMPREHENSIVE_TODO_LIST.md ✅ (project reference)
  └── LICENSE.md ✅

web/public-site/:
  └── README.md ✅

TOTAL ACTIVE: ~31 files (clean, maintainable)
RATIO: Archive historical, keep architectural
```

---

## ✅ Quality Checklist

After execution:

- [ ] Root directory has only: README.md, LICENSE.md, COMPREHENSIVE_TODO_LIST.md
- [ ] `docs/` has only: 00-07 files + 4 folders (archive, components, reference, troubleshooting)
- [ ] `web/public-site/` has only: README.md (+ actual component files)
- [ ] All moved files exist in `docs/archive/` with original names
- [ ] No broken links in 00-README.md
- [ ] Phase files NOT deleted, only moved to archive/
- [ ] Git status clean (all changes committed)
- [ ] No orphaned .md files found
- [ ] Total documentation files: ~31 active + 60+ archived

---

## 🚀 Expected Result

**Documentation Maintenance Burden:** Reduced by 70%  
**File Organization:** Clear, architectural (not chronological)  
**Discoverability:** Easier with 00-README.md as single hub  
**Archive:** All historical preserved for reference

**New Documentation Policy Enforced:**

- ✅ High-level only core documentation
- ✅ No guides or how-to documentation (code demonstrates)
- ✅ No status updates or session notes (archived)
- ✅ No duplicates (consolidated)
- ✅ Clear folder structure (core, reference, troubleshooting, components)

---

## 📞 Questions Before Execution?

1. Should COMPREHENSIVE_TODO_LIST.md stay in root or move to docs/?
   - **Decision:** Stay in root (high-visibility for team)

2. Archive completely or create link from docs/00-README.md to archive?
   - **Decision:** Archive completely, no backlinks needed (historical)

3. Should web/public-site/ component doc stay there or move to docs/components/?
   - **Decision:** Stay in web/public-site/ (component-specific README)

---

**Ready to execute?** → Proceed to STEP 1
