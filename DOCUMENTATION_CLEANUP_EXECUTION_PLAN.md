# 📋 Documentation Cleanup Execution Plan - HIGH-LEVEL ONLY POLICY

**Status:** 🎯 READY TO EXECUTE  
**Policy:** HIGH-LEVEL DOCUMENTATION ONLY v2.0  
**Estimated Time:** 45-60 minutes  
**Date:** October 24, 2025

---

## 📌 Policy Reminder

**GLAD Labs Documentation Policy:**

- ✅ Create: Architecture-level, stable, reusable documentation
- ✅ Create: Reference materials (schemas, APIs, standards)
- ✅ Create: Focused troubleshooting guides (max 5-10 per category)
- ❌ Don't Create: Session reports, status updates, project-specific guides
- ❌ Don't Create: Implementation step-by-step guides (code is the guide)
- ❌ Don't Create: Guides that duplicate core documentation

**Current Issue:** Multiple policy-violating files at root level and in docs/ folder

---

## 🎬 Execution Steps

### STEP 1: Archive Session/Status Files (10 min)

Files to move to `docs/archive/`:

```bash
# Create archive directory (if doesn't exist)
mkdir -p docs/archive

# Archive these files (violate "high-level only" policy)
mv PHASE_3.4_TESTING_COMPLETE.md docs/archive/
mv PHASE_3.4_NEXT_STEPS.md docs/archive/
mv PHASE_3.4_VERIFICATION.md docs/archive/
mv DOCUMENTATION_REVIEW_REPORT_OCT_2025.md docs/archive/
mv CLEANUP_COMPLETE_OCT_2025.md docs/archive/
mv SESSION_SUMMARY_TESTING.md docs/archive/
mv TEST_SUITE_INTEGRATION_REPORT.md docs/archive/
mv INTEGRATION_COMPLETE.md docs/archive/
mv INTEGRATION_CONFIRMATION.md docs/archive/
mv INTEGRATION_VERIFICATION_FINAL.md docs/archive/
```

**Why Archive?**

- Session reports become outdated
- Status updates not architecture-level
- Keep only if historical reference needed (unlikely)

**Verification:**

```bash
ls -la | grep -E "PHASE_3|SESSION|INTEGRATION|CLEANUP|DOCUMENTATION_REVIEW"
# Should return nothing (empty)
```

---

### STEP 2: Move Branch Hierarchy Docs to Reference (10 min)

**Current State:**

```
Root/
├── BRANCH_HIERARCHY_GUIDE.md (600 lines)
├── BRANCH_HIERARCHY_QUICK_REFERENCE.md (200 lines)
└── GITHUB_ACTIONS_TESTING_ANALYSIS.md (400 lines)
```

**Action:**

```bash
# Create CI/CD reference folder
mkdir -p docs/reference/ci-cd

# Move files
mv BRANCH_HIERARCHY_IMPLEMENTATION_SUMMARY.md docs/reference/ci-cd/
mv BRANCH_HIERARCHY_QUICK_REFERENCE.md docs/reference/ci-cd/
mv GITHUB_ACTIONS_TESTING_ANALYSIS.md docs/reference/ci-cd/GITHUB_ACTIONS_REFERENCE.md
```

**Why Move?**

- These are reference materials (technical specs, not guides)
- Reduce root-level clutter
- Better organization with related docs

**Verification:**

```bash
ls docs/reference/ci-cd/
# Should show: 3 files moved
```

---

### STEP 3: Update Core Documentation (15 min)

**File:** `docs/04-DEVELOPMENT_WORKFLOW.md`

**Action:** Add new section consolidating branch hierarchy info

**New Section to Add (after existing Branch Strategy section):**

```markdown
## 🌳 Four-Tier Branch Hierarchy

[Consolidate key points from:

- BRANCH_HIERARCHY_GUIDE.md (remove from root)
- BRANCH_HIERARCHY_QUICK_REFERENCE.md (moved to reference/)
- Add: feat/\* branches have NO CI/CD cost
- Add: dev/staging/main have cost breakdown
- Add: git commands for each tier
  ]

### Branch Names & Purposes

### CI/CD Workflow Per Branch

### Cost Analysis

(See docs/reference/ci-cd/ for detailed analysis)
```

**Why Update Core Docs?**

- Consolidate related information
- Single source of truth for developers
- Remove need for separate reference files at root

---

### STEP 4: Delete Root-Level Policy Violations (5 min)

After consolidating into core docs, delete duplicates:

```bash
# Delete (after consolidating into docs/04-DEVELOPMENT_WORKFLOW.md)
rm BRANCH_HIERARCHY_GUIDE.md
rm BRANCH_HIERARCHY_QUICK_REFERENCE.md
```

**Note:** `GITHUB_ACTIONS_TESTING_ANALYSIS.md` is moved (not deleted), kept as reference

**Verification:**

```bash
ls -la *.md | grep -i branch
# Should return nothing

ls docs/reference/ci-cd/
# Should show moved files
```

---

### STEP 5: Verify and Update Links (15 min)

**Check:** `docs/00-README.md` for broken links

```bash
# Look for links to:
# - BRANCH_HIERARCHY_* ❌ (should not exist)
# - GITHUB_ACTIONS_TESTING_ANALYSIS ❌ (should link to docs/reference/ci-cd/)
# - docs/reference/CI_CD_REFERENCE.md ✅ (correct)
```

**Update Links:**

If 00-README.md mentions moved files, update:

```markdown
# Old

[Branch Hierarchy Guide](../BRANCH_HIERARCHY_GUIDE.md)

# New

[Branch Hierarchy](./04-DEVELOPMENT_WORKFLOW.md#-four-tier-branch-hierarchy)
```

---

### STEP 6: Cleanup Additional Issues (5-10 min)

**Check & Fix in `docs/TESTING_GUIDE.md`:**

This file violates policy (step-by-step guide). Options:

#### Option A: Consolidate into reference

```bash
mv docs/TESTING_GUIDE.md docs/reference/TESTING_GUIDE.md
# Update docs/00-README.md to link to new location
```

#### Option B: Archive (if duplicate)

```bash
# If docs/reference/TESTING.md already exists
mv docs/TESTING_GUIDE.md docs/archive/
# Keep only one
```

---

## ✅ Verification Checklist

After executing all steps:

- [ ] **All archived files in `docs/archive/`:**

```bash
ls docs/archive/ | wc -l  # Should show 8-10 files moved
```

- [ ] **Branch hierarchy files in reference:**

```bash
ls docs/reference/ci-cd/ | grep -i branch  # Should show 2-3 files
```

- [ ] **No policy violations at root:**

```bash
ls -la | grep -E "PHASE_|SESSION_|INTEGRATION_|TESTING_GUIDE"
# Should return NOTHING
```

- [ ] **Core docs updated:**

```bash
grep -l "Four-Tier Branch" docs/04-DEVELOPMENT_WORKFLOW.md
# Should find the section
```

- [ ] **All links valid:**

```bash
npm run format  # Clean up markdown
# Check for broken links (manual review of 00-README.md)
```

---

## 📊 Documentation Structure After Cleanup

```
docs/
├── 00-README.md ✅ (Hub - main navigation)
├── 01-SETUP_AND_OVERVIEW.md ✅ (Getting started)
├── 02-ARCHITECTURE_AND_DESIGN.md ✅ (System design)
├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md ✅ (Cloud deployment)
├── 04-DEVELOPMENT_WORKFLOW.md ✅ (Git, testing, branch hierarchy)
├── 05-AI_AGENTS_AND_INTEGRATION.md ✅ (Agent orchestration)
├── 06-OPERATIONS_AND_MAINTENANCE.md ✅ (Production ops)
├── 07-BRANCH_SPECIFIC_VARIABLES.md ✅ (Environment config)
│
├── components/ (4 component folders)
│   ├── strapi-cms/README.md
│   ├── public-site/README.md
│   ├── oversight-hub/README.md
│   └── cofounder-agent/README.md
│
├── reference/ (Technical specs only)
│   ├── GLAD-LABS-STANDARDS.md ✅
│   ├── data_schemas.md ✅
│   ├── API_CONTRACT_CONTENT_CREATION.md ✅
│   ├── npm-scripts.md ✅
│   ├── POWERSHELL_API_QUICKREF.md ✅
│   └── ci-cd/ (NEW)
│       ├── GITHUB_ACTIONS_REFERENCE.md
│       ├── BRANCH_HIERARCHY_IMPLEMENTATION_SUMMARY.md
│       └── BRANCH_HIERARCHY_QUICK_REFERENCE.md
│
├── troubleshooting/ (Focused issues only)
│   └── strapi-cms/
│       ├── STRAPI_V5_PLUGIN_ISSUE.md ✅
│       └── STRAPI_SETUP_WORKAROUND.md ✅
│
└── archive/ (Historical/outdated)
    ├── PHASE_3.4_TESTING_COMPLETE.md
    ├── PHASE_3.4_NEXT_STEPS.md
    ├── SESSION_SUMMARY_TESTING.md
    ├── DOCUMENTATION_REVIEW_REPORT_OCT_2025.md
    └── ... (8-10 archived files)

Root/
├── README.md ✅ (Project overview - keep)
├── CODEBASE_ANALYSIS_AND_NEXT_STEPS.md ✅ (Latest analysis - keep for now)
└── (No PHASE_*/SESSION_*/INTEGRATION_* files)
```

**Result:**

- ✅ 8 core docs (high-level, stable)
- ✅ 4 component folders (focused, linked)
- ✅ 8-10 reference docs (technical, architectural)
- ✅ 2-4 troubleshooting guides (focused issues only)
- ✅ ~10 archived files (historical, cleanly organized)
- ✅ **Total active docs: ~20-25 files** (manageable, maintainable)

---

## 🔄 Git Workflow

After cleanup, commit changes:

```bash
# 1. View changes
git status

# 2. Stage cleanup
git add -A

# 3. Commit with clear message
git commit -m "docs: apply high-level documentation policy cleanup

- Archive 8+ session/status reports to docs/archive/
- Move branch hierarchy files to docs/reference/ci-cd/
- Consolidate branch info into 04-DEVELOPMENT_WORKFLOW.md
- Reduce root-level documentation from 15+ to <5 files
- Policy: HIGH-LEVEL DOCUMENTATION ONLY

Cleanup improves:
✅ Maintainability (fewer files to update)
✅ Navigation (clear structure)
✅ Staleness risk (remove session reports)
✅ Developer experience (core docs only)

See: CODEBASE_ANALYSIS_AND_NEXT_STEPS.md for full analysis"

# 4. Push to branch
git push origin feat/docs-cleanup
```

---

## ⏱️ Timeline

| Step                  | Time     | Total     |
| --------------------- | -------- | --------- |
| 1. Archive files      | 10 min   | 10 min    |
| 2. Move to reference  | 10 min   | 20 min    |
| 3. Update core docs   | 15 min   | 35 min    |
| 4. Delete duplicates  | 5 min    | 40 min    |
| 5. Verify & fix links | 15 min   | 55 min    |
| 6. Additional cleanup | 5-10 min | 60-65 min |

**Total: ~60 minutes (including verification)**

---

## 🎯 Expected Outcomes

After cleanup:

✅ **Root-level documentation:** Reduced from 15+ to <5 files
✅ **Docs folder:** Clean structure with no violations
✅ **Policy compliance:** 100% (high-level only)
✅ **Maintenance burden:** Significantly reduced
✅ **Developer experience:** Clearer navigation
✅ **Staleness risk:** Eliminated (no status/session docs)

---

## 📝 Next Session

After cleanup is complete:

1. Review new structure
2. Test all links: `grep -r "\.md)" docs/ | wc -l`
3. Verify no broken links
4. Create final report
5. Move to **Tier 1 development tasks** (see CODEBASE_ANALYSIS_AND_NEXT_STEPS.md)

---

## 🚀 Ready to Execute?

✅ **Review this plan**  
✅ **Execute steps 1-6 in order**  
✅ **Verify after each step**  
✅ **Commit with clear message**  
✅ **Move to next tasks**

**Let's clean up and ship! 🎉**
