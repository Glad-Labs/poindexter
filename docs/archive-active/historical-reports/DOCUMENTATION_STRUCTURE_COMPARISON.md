# DOCUMENTATION STRUCTURE: CURRENT vs. TARGET

**Status:** Visual comparison of documentation gaps  
**Date:** February 10, 2026  

---

## 📊 CORE DOCUMENTATION STRUCTURE COMPARISON

### CURRENT STRUCTURE (❌ Non-Compliant - 7 files)

```
docs/
├── 00-README.md                              ✅ CORRECT
├── 01-SETUP_AND_OVERVIEW.md                  ✅ CORRECT
├── 02-ARCHITECTURE_AND_DESIGN.md             ✅ CORRECT
├── 03-AI_AGENTS_AND_INTEGRATION.md           ⚠️ WRONG POSITION (should be #5)
├── 04-MODEL_ROUTER_AND_MCP.md                ❌ NOT IN FRAMEWORK
├── 05-ENVIRONMENT_VARIABLES.md               ❌ SHOULD BE MERGED INTO #7
├── 06-DEPLOYMENT_GUIDE.md                    ⚠️ WRONG NAME (should be 03)
└── 07-OPERATIONS_AND_MAINTENANCE.md          ⚠️ SHOULD BE #6

MISSING:
├── 04-DEVELOPMENT_WORKFLOW.md                ❌ CRITICAL GAP
└── 07-BRANCH_SPECIFIC_VARIABLES.md           ❌ CRITICAL GAP
```

### TARGET STRUCTURE (✅ Compliant - 8 files)

```
docs/
├── 00-README.md                                     ✅ Navigation Hub
├── 01-SETUP_AND_OVERVIEW.md                        ✅ Getting Started
├── 02-ARCHITECTURE_AND_DESIGN.md                   ✅ System Design
├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md             📝 RENAME from 06
│   └── Deployment procedures, CI/CD, infrastructure stack
├── 04-DEVELOPMENT_WORKFLOW.md                      📝 NEW FILE REQUIRED
│   └── Branch strategy (Tier 1-4), git workflow, development process
├── 05-AI_AGENTS_AND_INTEGRATION.md                 📝 RENAME from 03
│   └── Agent architecture, 6-phase pipeline, MCP integration
├── 06-OPERATIONS_AND_MAINTENANCE.md                📝 RENAME from 07
│   └── Monitoring, logging, health checks, maintenance
└── 07-BRANCH_SPECIFIC_VARIABLES.md                 📝 NEW FILE REQUIRED
    └── Environment variables per tier, secrets management
```

---

## 🔄 MIGRATION PLAN

### Step 1: Create New Files (Feb 10-12)

```bash
# File 1: Extract from .github/copilot-instructions.md
touch docs/04-DEVELOPMENT_WORKFLOW.md

# File 2: Consolidate from multiple sources
touch docs/07-BRANCH_SPECIFIC_VARIABLES.md
```

### Step 2: Rename Existing Files (Feb 13-14)

```bash
# Backup current structure
git checkout -b docs/restructure

# Rename file 3
mv docs/03-AI_AGENTS_AND_INTEGRATION.md docs/03-AI_AGENTS_AND_INTEGRATION.md.tmp
mv docs/06-DEPLOYMENT_GUIDE.md docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md
mv docs/03-AI_AGENTS_AND_INTEGRATION.md.tmp docs/05-AI_AGENTS_AND_INTEGRATION.md

# Rename file 6
mv docs/07-OPERATIONS_AND_MAINTENANCE.md docs/06-OPERATIONS_AND_MAINTENANCE.md

# Archive/remove migration
mv docs/04-MODEL_ROUTER_AND_MCP.md docs/04-MODEL_ROUTER_AND_MCP.md.optional
# (Keep as optional supplementary doc, or merge into #5)

# Merge file 5 content
# (Extract relevant sections, merge into 07-BRANCH_SPECIFIC_VARIABLES.md)
```

### Step 3: Update Navigation (Feb 14)

```bash
# Update 00-README.md to link to new structure
# Update 07-BRANCH_SPECIFIC_VARIABLES.md with merged content from 05
# Update all cross-document links
```

---

## 📁 REFERENCE DOCUMENTATION STRUCTURE

### CORRECT (Keep as-is)

```
docs/reference/
├── API_CONTRACTS.md                    ✅ Endpoint specifications
├── data_schemas.md                     ✅ Database schemas
├── TESTING.md                          ✅ Testing strategy
├── GITHUB_SECRETS_SETUP.md             ✅ Secrets management
├── TASK_STATUS_AUDIT_REPORT.md         ✅ Task status docs
├── TASK_STATUS_QUICK_START.md          ✅ Task status quick ref
└── ci-cd/
    ├── GITHUB_ACTIONS_REFERENCE.md     ✅ GitHub Actions workflows
    ├── BRANCH_HIERARCHY_IMPLEMENTATION_SUMMARY.md    ✅ Branch details
    └── BRANCH_HIERARCHY_QUICK_REFERENCE.md           ✅ Branch quick ref
```

### NEEDS IMPROVEMENT

```
docs/reference/
├── GLAD-LABS-STANDARDS.md              ⚠️ NOT CODE STANDARDS
│   └── Currently reads like strategic business plan
│   └── Should contain: Python/React/TypeScript coding standards
│   └── Naming conventions, architectural patterns, best practices
```

### MUST ARCHIVE

```
docs/reference/
├── FINAL_SESSION_SUMMARY.txt           ❌ Move to archive-active/
├── PHASE_1_COMPLETION_REPORT.txt       ❌ Move to archive-active/
├── SESSION_COMPLETE.txt                ❌ Move to archive-active/
└── QUICK_REFERENCE.txt                 ❌ Move to archive-active/
```

---

## 📚 COMPONENT DOCUMENTATION STRUCTURE

### COMPLETE (cofounder-agent)

```
docs/components/cofounder-agent/
├── README.md                                        ✅ Architecture overview
└── troubleshooting/
    ├── QUICK_FIX_COMMANDS.md                       ✅ Quick fixes
    └── RAILWAY_WEB_CONSOLE_STEPS.md                ✅ Railway debugging
```

### NEEDS COMPLETION (oversight-hub)

```
docs/components/oversight-hub/
├── README.md                                        ✅ Architecture overview
└── troubleshooting/                                 ⚠️ EMPTY FOLDER
    ├── QUICK_FIX_COMMANDS.md                       ❌ MISSING
    ├── REACT_BUILD_ERRORS.md                       ❌ MISSING
    ├── FIREBASE_ISSUES.md                          ❌ MISSING
    └── MATERIAL_UI_STYLING.md                      ❌ MISSING
```

### NEEDS COMPLETION (public-site)

```
docs/components/public-site/
├── README.md                                        ✅ Architecture overview
└── troubleshooting/                                 ⚠️ EMPTY FOLDER
    ├── QUICK_FIX_COMMANDS.md                       ❌ MISSING
    ├── NEXTJS_BUILD_ERRORS.md                      ❌ MISSING
    ├── STRAPI_INTEGRATION.md                       ❌ MISSING
    └── ISR_ISSUES.md                               ❌ MISSING
```

### NEEDS CONTENT (strapi-cms)

```
docs/components/strapi-cms/
├── README.md                                        ✅ Architecture overview
└── troubleshooting/                                 ⚠️ NO FOLDER
    ├── QUICK_FIX_COMMANDS.md                       ❌ MISSING
    └── COMMON_ISSUES.md                            ❌ MISSING
```

---

## 🗂️ TROUBLESHOOTING STRUCTURE

### COMPLETE

```
docs/troubleshooting/
├── README.md                           ✅ Hub & index
├── 01-railway-deployment.md            ✅ Railway fixes (15 issues documented)
├── 04-build-fixes.md                   ✅ Build errors (8 issues documented)
└── 05-compilation.md                   ✅ TypeScript issues (6 issues documented)
```

### GAPS TO FILL

**Missing General Issues:**

- PostgreSQL connection failures
- Ollama/local model failures
- Model router provider fallback issues
- Task executor not polling
- API timeout issues
- CORS errors
- Database migration failures

**Missing Component-Specific Issues:**

- React component issues (Oversight Hub)
- Next.js rendering issues (Public Site)
- Strapi CMS issues

---

## 📊 CONTENT CONSOLIDATION MAP

### Where Content Currently Lives (Fragmented)

```
Branch Strategy Information:
  ├── .github/copilot-instructions.md (BEST SOURCE)
  └── README.md (brief mentions)

Environment Variables:
  ├── 05-ENVIRONMENT_VARIABLES.md (single-source for local)
  ├── reference/GITHUB_SECRETS_SETUP.md (GitHub-specific)
  └── .github/copilot-instructions.md (environment info)

Deployment Process:
  ├── 06-DEPLOYMENT_GUIDE.md (current, incomplete)
  ├── reference/ci-cd/ (GitHub Actions)
  └── .github/copilot-instructions.md (startup commands)

Operations:
  ├── 07-OPERATIONS_AND_MAINTENANCE.md
  └── reference/ (scattered)
```

### Where Content Should Go (Consolidated)

```
04-DEVELOPMENT_WORKFLOW.md (NEW)
  ├── Extract: .github/copilot-instructions.md → "Critical Knowledge for Productivity"
  ├── Expand: Branch strategy details
  ├── Add: Development environment setup
  └── Add: Review & merge procedures

07-BRANCH_SPECIFIC_VARIABLES.md (NEW)
  ├── Consolidate: 05-ENVIRONMENT_VARIABLES.md → Tier 1 (local only)
  ├── Consolidate: GITHUB_SECRETS_SETUP.md → Tier 3-4 (staging/prod)
  ├── Add: Tier 2 (feature branches) configuration
  └── Add: Rotation & security procedures

03-DEPLOYMENT_AND_INFRASTRUCTURE.md (UPDATED)
  ├── Consolidate: Current 06-DEPLOYMENT_GUIDE.md
  ├── Consolidate: reference/ci-cd/ → Inline GitHub Actions section
  ├── Add: Infrastructure stack details
  └── Add: Secrets synchronization procedures
```

---

## ✅ COMPLIANCE CHECKLIST

### Phase 1 Complete (Critical)

- [ ] Create `04-DEVELOPMENT_WORKFLOW.md` (8 hours)
- [ ] Create `07-BRANCH_SPECIFIC_VARIABLES.md` (6 hours)
- [ ] Rename 5 core documentation files (4 hours)
- [ ] Archive 4 session files from `/reference/` (1 hour)
- [ ] Update `00-README.md` with new structure (2 hours)
- [ ] Update all cross-document links (2 hours)

**Total Phase 1:** ~23 hours  
**Target Completion:** February 14, 2026

### Phase 2 Complete (High-Priority)

- [ ] Create Oversight Hub troubleshooting guides (5 hours)
- [ ] Create Public Site troubleshooting guides (5 hours)
- [ ] Complete API_CONTRACTS.md (4 hours)
- [ ] Complete data_schemas.md (3 hours)
- [ ] Enhance GLAD-LABS-STANDARDS.md (3 hours)

**Total Phase 2:** ~20 hours  
**Target Completion:** February 28, 2026

### Phase 3 Complete (Medium-Priority)

- [ ] Document architectural decisions (4 hours)
- [ ] Add missing troubleshooting guides (5 hours)
- [ ] Create archive README (2 hours)
- [ ] Update metrics in navigation (2 hours)

**Total Phase 3:** ~13 hours  
**Target Completion:** March 2, 2026

---

## 📈 SUCCESS METRICS

### After Phase 1 (Feb 14)

- [ ] 8/8 core documentation files exist with correct numbering
- [ ] 00-README.md updated with correct links
- [ ] All cross-document links working
- [ ] Framework compliance: 92%+

### After Phase 2 (Feb 28)

- [ ] All component troubleshooting folders populated
- [ ] Reference documentation complete
- [ ] Framework compliance: 96%+

### After Phase 3 (Mar 2)

- [ ] All architectural decisions documented
- [ ] Archive fully indexed
- [ ] Framework compliance: 98%+

### Final Audit (Mar 10)

- [ ] Target: 100% compliance with ENTERPRISE_DOCUMENTATION_FRAMEWORK.md

---

## 🔗 REFERENCE DOCUMENTS

- **Full Audit:** `DOCUMENTATION_COMPLETENESS_AUDIT_2026-02-10.md`
- **Quick Start:** `DOCUMENTATION_AUDIT_QUICK_START.md`
- **Framework:** `.github/prompts/ENTERPRISE_DOCUMENTATION_FRAMEWORK.md`
- **Copilot Guide:** `.github/copilot-instructions.md`
