# 🧹 Root Folder Cleanup Plan

**Status:** Ready for Execution  
**Date:** November 14, 2025  
**Goal:** Restore root folder to clean, professional state  
**Current State:** ~80 root-level files (messy)  
**Target State:** ~15 root-level files (clean)

---

## 📊 Current Root Folder Analysis

### 🔴 Problem Files (92 files to organize/archive)

**Phase Files** (Keep only most recent, archive others):

```
PHASE_5_*.md (10 files)           → Archive 8, keep 1 summary
PHASE_6_ROADMAP.md                → Keep (current)
PHASE_4_COMPLETE.md               → Archive
```

**Status/Audit Files** (These are technical debt):

```
API_REFACTORING_COMPLETE.md
AUDIT_CLEANUP_ACTIONS_COMPLETE.md
BACKEND_FIX_COMPLETE.md
BUTTON_REPOSITIONING_COMPLETE.md
CLEANUP_AND_AUDIT_SUMMARY.md
COMPLETE_SYSTEM_FIX_OVERVIEW.md
COMPLETE_SYSTEM_STATUS_PHASE_4.md
COMPREHENSIVE_CODEBASE_AUDIT_REPORT.md
CONTENT_PIPELINE_AUDIT.md
DATABASE_MIGRATION_PLAN.md
DATABASE_SCHEMA_FIX_COMPLETE.md
DECISION_AND_NEXT_STEPS.md
ENDPOINT_VERIFICATION_TEST.md
EXECUTIVE_SUMMARY.md
FASTAPI_CMS_IMPLEMENTATION_*.md (5 files)
FINAL_*.md (4 files)
FRONTEND_*.md (2 files)
IMPLEMENTATION_CHECKLIST.md
MIGRATION_STRAPI_TO_FASTAPI.md
OVERSIGHT_HUB_UI_CHANGES*.md (2 files)
QA_READY.md
ROOT_CAUSE_ANALYSIS_AND_REAL_FIX.md
SESSION_SUMMARY_*.md (3 files)
STEP_3_*.md (2 files)
STRAPI_*.md (12 files)
TASK_*.md (3 files)
TESTING_CHECKLIST.md
```

**Other Files** (Can stay):

```
README.md                    → Keep (root project overview)
LICENSE / LICENSE.md         → Keep (legal)
package.json                 → Keep (Node workspace root)
pyproject.toml               → Keep (Python config)
postcss.config.js            → Keep (Tailwind config)
.env.example                 → Keep (env template)
docker-compose.yml           → Keep (local dev)
railway.json                 → Keep (deployment)
vercel.json                  → Keep (deployment)
Procfile                     → Keep (deployment)
.npmrc / .prettierrc.json    → Keep (tooling)
.markdownlint.json           → Keep (documentation standards)
.gitignore                   → Keep (git config)
```

**Leftover Scripts** (Can be archived or deleted):

```
start_strapi.sh              → Archive (Strapi removed)
```

---

## 🎯 Cleanup Strategy

### Phase 1: Archive Old Status Files (40 minutes)

Create `archive/session-status-logs/` folder:

```bash
# Move all "COMPLETE" and "STATUS" files to archive
PHASE_5_STEP_*.md          → archive/phase-5-steps/
SESSION_SUMMARY_*.md       → archive/session-logs/
PHASE_4_*.md               → archive/phase-4-archive/
STRAPI_*.md                → archive/strapi-migration-docs/
```

**Why?** These are historical records of debugging/implementation. Keep for reference but clean root.

### Phase 2: Consolidate Into Documentation (30 minutes)

Files to **migrate into docs/**:

- `DECISION_AND_NEXT_STEPS.md` → `docs/decisions/`
- `FINAL_STATUS_SUMMARY.md` → `docs/project-status/` (update monthly)
- `PHASE_6_ROADMAP.md` → `docs/roadmap/`

### Phase 3: Delete Obsolete Files (10 minutes)

These should be **permanently deleted** (not archived):

```
API_REFACTORING_COMPLETE.md              (outdated - merged into docs)
AUDIT_CLEANUP_ACTIONS_COMPLETE.md        (temporary audit file)
BACKEND_FIX_COMPLETE.md                  (implementation detail)
BUTTON_REPOSITIONING_COMPLETE.md         (UI fix, not ongoing)
CLEANUP_AND_AUDIT_SUMMARY.md             (session summary)
COMPLETE_SYSTEM_FIX_OVERVIEW.md          (archived approach)
COMPREHENSIVE_CODEBASE_AUDIT_REPORT.md   (outdated audit)
CONTENT_PIPELINE_AUDIT.md                (phase-specific)
DATABASE_MIGRATION_PLAN.md               (completed - docs cover this)
ENDPOINT_VERIFICATION_TEST.md            (testing artifact)
EXECUTIVE_SUMMARY.md                     (old status)
FINAL_DELIVERABLES_SUMMARY.md            (old status)
FINAL_FIX_VERIFICATION.md                (temporary)
FRONTEND_BACKEND_CONNECTION_COMPLETE.md  (implementation done)
FRONTEND_FIX_SUMMARY.md                  (fix artifact)
IMPLEMENTATION_CHECKLIST.md              (completed)
MIGRATION_STRAPI_TO_FASTAPI.md           (historical - docs cover)
OVERSIGHT_HUB_UI_CHANGES_COMPLETE.md     (completed)
ROOT_CAUSE_ANALYSIS_AND_REAL_FIX.md      (debugging artifact)
STEP_3_INTEGRATION_*.md                  (phase artifacts)
TASK_*.md                                (temporary logs)
TESTING_CHECKLIST.md                     (completed)
start_strapi.sh                          (Strapi removed)
```

### Phase 4: Final Root State (5 files + configs)

```
✅ Root Files - Core Project
├── README.md                 (Project overview)
├── LICENSE.md                (Licensing)
├── package.json              (Node workspaces)
├── pyproject.toml            (Python config)
├── postcss.config.js         (Tailwind)

✅ Environment & Deployment
├── .env.example              (Environment template)
├── docker-compose.yml        (Local development)
├── railway.json              (Railway deployment)
├── vercel.json               (Vercel deployment)
├── Procfile                  (Process manager)

✅ Configuration & Standards
├── .prettierrc.json          (Code formatting)
├── .markdownlint.json        (Documentation)
├── .npmrc                    (npm config)
├── .gitignore                (Git ignore rules)

✅ Folders
├── docs/                     (Documentation)
├── src/                      (Backend)
├── web/                      (Frontend)
├── cloud-functions/          (GCP functions)
├── scripts/                  (Utility scripts)
├── .github/                  (GitHub workflows)
├── .vscode/                  (VSCode settings)
├── archive/                  (Historical files)
├── logs/                     (Runtime logs)
├── backups/                  (Backup files)
└── tests/                    (Root-level tests)
```

---

## 📁 New Archive Structure

Create `archive/` with organized subdirectories:

```
archive/
├── README.md                           (Archive index)
├── phase-5-steps/                      (Phase 5 implementation steps)
│   ├── PHASE_5_STEP_2_COMPLETE.md
│   ├── PHASE_5_STEP_3_COMPLETE.md
│   ├── PHASE_5_STEP_4_COMPLETE.md
│   ├── PHASE_5_STEP_5_COMPLETE.md
│   ├── PHASE_5_STEP_6_DIAGNOSTIC_CHECKLIST.md
│   ├── PHASE_5_STEP_6_E2E_TESTING_PLAN.md
│   └── PHASE_5_IMPLEMENTATION_STATUS.md
│
├── phase-4-archive/                   (Older phase files)
│   ├── PHASE_4_COMPLETE.md
│   └── COMPLETE_SYSTEM_STATUS_PHASE_4.md
│
├── session-logs/                      (Historical session summaries)
│   ├── SESSION_SUMMARY_COMPLETE.md
│   ├── SESSION_SUMMARY_PHASE_5_COMPLETE.md
│   ├── PHASE_5_SESSION_SUMMARY.md
│   ├── PHASE_5_SESSION_SUMMARY_FINAL.md
│   └── PHASE_5_SESSION_EXECUTIVE_SUMMARY.md
│
├── strapi-migration-docs/             (Strapi removal documentation)
│   ├── MIGRATION_STRAPI_TO_FASTAPI.md
│   ├── STRAPI_API_EXPOSURE_FIX.md
│   ├── STRAPI_DIAGNOSTIC_REPORT.md
│   ├── STRAPI_REBUILD_EVALUATION.md
│   ├── STRAPI_REBUILD_IMPLEMENTATION_PLAN.md
│   ├── STRAPI_REBUILD_MASTER_CONTROL.md
│   ├── STRAPI_REBUILD_QUICK_START.md
│   ├── STRAPI_REBUILD_STRATEGY.md
│   ├── STRAPI_REMOVAL_CHECKLIST.md
│   ├── STRAPI_ROUTE_FIX_STRATEGY.md
│   ├── STRAPI_SETUP_COMPLETE.md
│   ├── STRAPI_V5_FIX_SUCCESS.md
│   └── STRAPI_VISIBILITY_AND_CONSOLE_ERRORS_FIX.md
│
├── implementation-docs/               (Implementation artifacts)
│   ├── API_REFACTORING_COMPLETE.md
│   ├── BACKEND_FIX_COMPLETE.md
│   ├── DATABASE_SCHEMA_FIX_COMPLETE.md
│   ├── FRONTEND_BACKEND_CONNECTION_COMPLETE.md
│   ├── FRONTEND_FIX_SUMMARY.md
│   ├── FASTAPI_CMS_IMPLEMENTATION_CHECKLIST.md
│   ├── FASTAPI_CMS_IMPLEMENTATION_ROADMAP.md
│   ├── FASTAPI_CMS_IMPLEMENTATION_SUMMARY.md
│   ├── FASTAPI_CMS_MIGRATION_SUMMARY.md
│   └── FASTAPI_CMS_NEXT_STEPS.md
│
├── audit-reports/                    (Audit and analysis documents)
│   ├── AUDIT_CLEANUP_ACTIONS_COMPLETE.md
│   ├── COMPREHENSIVE_CODEBASE_AUDIT_REPORT.md
│   ├── CONTENT_PIPELINE_AUDIT.md
│   ├── ENDPOINT_VERIFICATION_TEST.md
│   ├── ROOT_CAUSE_ANALYSIS_AND_REAL_FIX.md
│   ├── STRAPI_DIAGNOSTIC_REPORT.md
│   └── TASK_ACTIONS_VERIFICATION.md
│
└── ui-changes/                       (UI implementation records)
    ├── BUTTON_REPOSITIONING_COMPLETE.md
    ├── OVERSIGHT_HUB_UI_CHANGES.md
    ├── OVERSIGHT_HUB_UI_CHANGES_COMPLETE.md
    ├── TASK_DISPLAY_FIX.md
    └── TASK_TABLE_CONSOLIDATION_COMPLETE.md
```

---

## 📋 Migration Targets for Ongoing Files

These files should move to `docs/`:

**To `docs/decisions/`:**

- `DECISION_AND_NEXT_STEPS.md` (ongoing decisions)

**To `docs/roadmap/`:**

- `PHASE_6_ROADMAP.md` (active roadmap)

**To `docs/project-status/` (updated monthly):**

- `FINAL_STATUS_SUMMARY.md` (rename to CURRENT_STATUS.md)
- `QA_READY.md` (testing status)

**To `docs/reference/`:**

- `DATABASE_MIGRATION_PLAN.md` → `DATABASE_SCHEMA.md`

---

## 🔄 Execution Steps

### Step 1: Create Archive Structure

```bash
cd c:\Users\mattm\glad-labs-website
mkdir -p archive/phase-5-steps
mkdir -p archive/phase-4-archive
mkdir -p archive/session-logs
mkdir -p archive/strapi-migration-docs
mkdir -p archive/implementation-docs
mkdir -p archive/audit-reports
mkdir -p archive/ui-changes
echo "Archive structure created at $(date)" > archive/README.md
```

### Step 2: Move Phase 5 Files

```bash
mv PHASE_5_STEP_*.md archive/phase-5-steps/
mv PHASE_5_IMPLEMENTATION_STATUS.md archive/phase-5-steps/
mv PHASE_5_READY_FOR_EXECUTION.md archive/phase-5-steps/
mv PHASE_5_REAL_CONTENT_GENERATION_ROADMAP.md archive/phase-5-steps/
```

### Step 3: Move Session Logs

```bash
mv SESSION_SUMMARY_*.md archive/session-logs/
mv PHASE_5_SESSION_*.md archive/session-logs/
```

### Step 4: Move Strapi Docs

```bash
mv MIGRATION_STRAPI_TO_FASTAPI.md archive/strapi-migration-docs/
mv STRAPI_*.md archive/strapi-migration-docs/
```

### Step 5: Move Implementation Docs

```bash
mv FASTAPI_CMS_*.md archive/implementation-docs/
mv API_REFACTORING_COMPLETE.md archive/implementation-docs/
mv BACKEND_FIX_COMPLETE.md archive/implementation-docs/
mv DATABASE_SCHEMA_FIX_COMPLETE.md archive/implementation-docs/
mv FRONTEND_*.md archive/implementation-docs/
```

### Step 6: Move Audit Reports

```bash
mv AUDIT_*.md archive/audit-reports/
mv COMPREHENSIVE_CODEBASE_AUDIT_REPORT.md archive/audit-reports/
mv CONTENT_PIPELINE_AUDIT.md archive/audit-reports/
mv ENDPOINT_VERIFICATION_TEST.md archive/audit-reports/
mv ROOT_CAUSE_ANALYSIS_AND_REAL_FIX.md archive/audit-reports/
mv TASK_ACTIONS_VERIFICATION.md archive/audit-reports/
```

### Step 7: Move UI Changes

```bash
mv BUTTON_REPOSITIONING_COMPLETE.md archive/ui-changes/
mv OVERSIGHT_HUB_UI_CHANGES*.md archive/ui-changes/
mv TASK_DISPLAY_FIX.md archive/ui-changes/
mv TASK_TABLE_CONSOLIDATION_COMPLETE.md archive/ui-changes/
```

### Step 8: Delete Truly Temporary Files

```bash
rm CLEANUP_AND_AUDIT_SUMMARY.md
rm COMPLETE_SYSTEM_FIX_OVERVIEW.md
rm COMPLETE_SYSTEM_STATUS_PHASE_4.md
rm DECISION_AND_NEXT_STEPS.md (we're moving this)
rm EXECUTIVE_SUMMARY.md
rm FINAL_DELIVERABLES_SUMMARY.md
rm FINAL_FIX_VERIFICATION.md
rm IMPLEMENTATION_CHECKLIST.md
rm OVERSIGHT_HUB_UI_CHANGES.md (already archived duplicate)
rm QA_READY.md (migrate to docs first)
rm STEP_3_INTEGRATION_*.md
rm TASK_*.md
rm TESTING_CHECKLIST.md
rm start_strapi.sh
```

### Step 9: Move Ongoing Docs to docs/

```bash
# These need to migrate to docs/ structure
cp DECISION_AND_NEXT_STEPS.md docs/decisions/DECISIONS.md
cp FINAL_STATUS_SUMMARY.md docs/project-status/CURRENT_STATUS.md
cp PHASE_6_ROADMAP.md docs/roadmap/PHASE_6.md
cp QUICK_START_GUIDE.txt docs/QUICK_START.txt
```

### Step 10: Commit Cleanup

```bash
git add .
git commit -m "chore: clean up root folder - archive old status files and session logs

- Moved 50+ historical files to archive/
- Organized by category (phases, sessions, migrations, audits)
- Kept only essential root files
- Migrated ongoing docs to docs/ folder
- Root folder now clean and professional"
```

---

## ✅ Final Root State Checklist

After cleanup, root should have ONLY:

```
✅ Core Project Files (must keep)
  [ ] README.md
  [ ] LICENSE.md
  [ ] package.json
  [ ] pyproject.toml
  [ ] postcss.config.js

✅ Environment & Deployment (must keep)
  [ ] .env.example
  [ ] docker-compose.yml
  [ ] railway.json
  [ ] vercel.json
  [ ] Procfile

✅ Configuration (must keep)
  [ ] .prettierrc.json
  [ ] .markdownlint.json
  [ ] .npmrc
  [ ] .gitignore

✅ Folders (must keep)
  [ ] docs/
  [ ] src/
  [ ] web/
  [ ] cloud-functions/
  [ ] scripts/
  [ ] .github/
  [ ] .vscode/

✅ Optional (nice to have)
  [ ] archive/          (historical files)
  [ ] logs/             (runtime logs)
  [ ] backups/          (backup data)
  [ ] tests/            (test files)

❌ Should NOT exist in root:
  [ ] Any PHASE_*.md files
  [ ] Any SESSION_*.md files
  [ ] Any status update files
  [ ] STRAPI_* files
  [ ] Temporary audit files
```

---

## 🎯 Benefits After Cleanup

✅ **Professional appearance** - Root folder is clean and organized  
✅ **Easier navigation** - Only essential files in root  
✅ **Historical preservation** - Old files archived, not deleted  
✅ **Better maintainability** - Know what files are current vs. historical  
✅ **Git cleaner** - Easier to see real changes in `git log`  
✅ **Documentation consistency** - Related docs centralized in docs/

---

## 📊 Before/After Comparison

| Metric               | Before | After |
| -------------------- | ------ | ----- |
| Root files           | 80+    | ~15   |
| Status files in root | 50+    | 0     |
| Archive files        | 0      | 50+   |
| Historical preserved | No     | Yes   |
| Professional         | ❌     | ✅    |

---

## 🔄 Maintenance After Cleanup

**Going Forward:**

- ❌ **DO NOT** create new .md files in root for status updates
- ❌ **DO NOT** save session logs to root
- ✅ **DO** place all new docs in `docs/` folder
- ✅ **DO** use `archive/` for obsolete but historical content
- ✅ **DO** commit cleanup regularly (monthly)

---

**Ready to execute? Run the bash commands in Step 1-10 above, then commit.**
