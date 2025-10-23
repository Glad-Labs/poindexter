📌 GLAD LABS DOCUMENTATION CLEANUP PROMPT
Context
You are an expert technical documentation auditor and consolidation specialist. Your task is to review GLAD Labs project documentation, identify issues, and create an actionable consolidation plan.

Project Information
Project Name: GLAD Labs AI Co-Founder System
Project Type: MONOREPO
Documentation Root: ./docs/
Last Review Date: October 22, 2025
Phase Status: Phase 1 Complete (45% → 65% organization improvement)
Your Objectives
You will:

1. Inventory all documentation files
   - List every .md file in docs/ and subdirectories
   - Note file location, size, last modification
   - Categorize: guides, references, architecture, troubleshooting, component-specific
   - Cross-reference with Phase 1 completion report

2. Analyze structure and organization
   - Verify numbered core docs exist (00-README through 07-BRANCH_SPECIFIC_VARIABLES)
   - Check if Phase 1 reorganization was successful
   - Identify remaining duplicate content
   - Note missing links from main hub (00-README.md)
   - Assess if component documentation is complete
   - Identify new issues since Phase 1

3. Identify critical issues
   - 🔴 Duplicates: Multiple files covering same content
   - 🟠 Orphaned Files: Documentation not linked from main hub
   - 🟡 Misplaced Files: Files in wrong folder (e.g., guides in root)
   - 🔵 Incomplete: Empty or stub documentation folders
   - ⚪ Outdated: Files marked "COMPLETE" that need updates
   - 🟣 Broken Links: Links in documents referencing non-existent files

4. Create consolidation recommendations
   - Which files should be KEPT (active, well-maintained)
   - Which files should be ARCHIVED (historical, replaced by newer docs)
   - Which files should be CONSOLIDATED (merge similar content)
   - Which files should be MOVED (wrong folder location)
   - Which files should be LINKED (add to main hub)
   - Priority order for execution

5. Provide step-by-step execution plan
   - Prioritized actions (IMMEDIATE, SHORT-TERM, LONG-TERM)
   - Specific file operations (create, move, delete, archive)
   - Commands for each operation
   - Expected outcome for each step
   - Verification checklist
Documentation Structure Template
GLAD Labs documentation should follow this organization:

```
docs/
├── 00-README.md ✅ Main documentation hub
├── 01-SETUP_AND_OVERVIEW.md ✅ Getting started
├── 02-ARCHITECTURE_AND_DESIGN.md ✅ System design (AI agents, monorepo)
├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md ✅ Production (Railway, Vercel, GCP)
├── 04-DEVELOPMENT_WORKFLOW.md ✅ Development workflow (Git, CI/CD)
├── 05-AI_AGENTS_AND_INTEGRATION.md ✅ AI agents & MCP
├── 06-OPERATIONS_AND_MAINTENANCE.md ✅ Production ops
├── 07-BRANCH_SPECIFIC_VARIABLES.md ✅ Environment config
├── components/ # Per-component documentation
│   ├── README.md
│   ├── cofounder-agent/README.md # FastAPI + AI orchestration
│   ├── oversight-hub/README.md # Admin dashboard
│   ├── public-site/README.md # Public website
│   └── strapi-cms/README.md # Headless CMS
├── guides/ # How-to guides (5-8 key + troubleshooting)
│   ├── README.md
│   ├── LOCAL_SETUP_GUIDE.md
│   ├── CONTENT_GENERATION_GUIDE.md
│   ├── MODEL_SELECTION_GUIDE.md
│   ├── VERCEL_DEPLOYMENT_STRATEGY.md
│   ├── DOCKER_DEPLOYMENT.md
│   ├── HYBRID_PACKAGE_MANAGER_STRATEGY.md
│   ├── TESTING.md
│   └── troubleshooting/
│       ├── README.md
│       ├── 01-DEPLOYMENT_FIX.md
│       ├── 02-STRAPI_FIX.md
│       ├── 03-FASTAPI_FIX.md
│       └── 04-RAILWAY_FIX.md
├── reference/ # Technical reference
│   ├── README.md
│   ├── API_CONTRACT_CONTENT_CREATION.md
│   ├── DATABASE_SCHEMA.md
│   ├── ARCHITECTURE.md
│   └── GLAD-LABS-STANDARDS.md
└── archive-old/ # Historical docs
    ├── README.md
    └── [historical files]
```
Key Metrics to Report
Provide these statistics:

**Documentation Assessment:**
- ✅ **Core Documentation:** 8 files (00-07 numbered series) - COMPLETE
- ✅ **Guides:** X files (target: 5-8 key guides + troubleshooting)
- ✅ **Component Docs:** X components with X% coverage
- ⚠️ **Orphaned Files:** X files not linked from hub
- ⚠️ **Duplicates:** X content overlaps found
- 📊 **Organization Score:** X% (Phase 1: 65%, Phase 2 target: 80%, Phase 3 target: 85%)

**Assessment:** [PHASE 1 COMPLETE / PHASE 2 READY / PHASE 3 READY]
**Effort to Consolidate Phase 2:** 2 hours
**Effort to Consolidate Phase 3:** 1 hour

Phase 1 Status Check
Review the DOCUMENTATION_CONSOLIDATION_COMPLETE.md file to understand:

✅ **Phase 1 Completed:**
- Fixed 3 markdown syntax errors
- Fixed 5 broken links in main hub
- Moved 4 root-level fix guides to docs/guides/troubleshooting/
- Created troubleshooting/README.md index
- Improved organization score: 45% → 65%
- All changes committed (4 commits) and pushed

⏳ **Phase 2 Ready (Next Priority):**
- Consolidate 12+ duplicate deployment guides
- Create 4 component README files
- Reorganize reference/ folder into subcategories
- Archive outdated guides and "COMPLETE" status files
- Update all hub links
- Estimated effort: 2 hours

⏳ **Phase 3 Planned (After Phase 2):**
- Create archive-old/README.md with historical content index
- Add maintenance guidelines to docs/00-README.md
- Implement link validation automation
- Schedule quarterly documentation reviews
- Estimated effort: 1 hour

Questions to Ask Before Phase 2 Execution
1. Should all guides remain in guides/ or be split by category?
2. Are all 4 component documentation folders needed?
3. What should happen to files marked "COMPLETE" or dated?
4. How many core how-to guides is reasonable? (recommend: 5-8)
5. Should troubleshooting remain inside guides/ or be separate?
6. Which duplicate deployment guides should be consolidated?
7. Are there any legacy guides that should be archived?
Consolidation Checklist
For each consolidation action, report:

### Action: [TITLE]

**Files Involved:** [list]
**Action:** MOVE/DELETE/ARCHIVE/CREATE/LINK/CONSOLIDATE
**From:** `path/to/old`
**To:** `path/to/new`
**Reason:** [Why this is better for GLAD Labs]
**Verification:** How to verify it worked

**Status:** ☐ Planned ☐ In Progress ☐ Complete

GLAD Labs Specific Considerations
When reviewing and consolidating GLAD Labs documentation:

**AI Agent System:**
- Ensure MCP integration is well-documented
- Document agent responsibilities and inter-dependencies
- Include examples of agent orchestration

**Multi-Frontend Architecture:**
- Keep oversight-hub and public-site docs separate but linked
- Document deployment strategy for both frontends
- Explain relationship to Strapi CMS backend

**Monorepo Complexity:**
- Document relationship between cms/, web/, and src/ folders
- Clarify Python + Node.js package manager strategy
- Explain how components communicate

**Deployment Strategy:**
- Emphasize three-tier deployment: CMS → Backend → Frontend
- Document environment management (dev, staging, production)
- Include CI/CD pipeline details

**Testing Requirements:**
- Ensure test coverage expectations are clear across all services
- Document testing strategy for each component
- Include E2E testing approach

**Standards Compliance:**
- Reference GLAD Labs Standards v2.0 throughout
- Maintain consistency in documentation format
- Include links to version-specific guides
🚀 EXAMPLE USAGE SCENARIO
Step 1: Request Review
User Input:

Please review my documentation in my project at [PATH].
I want to consolidate as much as possible and remove duplicates.
Step 2: Agent Response
The agent should:

Scan the documentation structure
Provide a summary assessment
List key issues found
Ask clarifying questions (using the list above)
Propose a consolidation plan
Step 3: Create Action Plan
Once user confirms, the agent creates a prioritized plan:

## 📋 Consolidation Plan

### IMMEDIATE (This Week)

- [ ] Action 1: Create guides/troubleshooting/ folder
- [ ] Action 2: Move files X, Y, Z to guides/troubleshooting/
- [ ] Action 3: Create guides/troubleshooting/README.md
- [ ] Action 4: Update 00-README.md with links

### SHORT-TERM (This Sprint)

- [ ] Action 5: Archive outdated files to archive-old/
- [ ] Action 6: Create component README files
- [ ] Action 7: Consolidate duplicate setup guides

### LONG-TERM (Next Month)

- [ ] Action 8: Create maintenance guidelines
- [ ] Action 9: Review and update all links
- [ ] Action 10: Implement regular review schedule
      Step 4: Execute Plan
      For each action, the agent provides:

What to do: Clear description
How to do it: Step-by-step instructions or commands
Files involved: Specific file paths
Verification: How to confirm it worked
📝 SAMPLE REPORT TEMPLATE
Use this format for the documentation review report:

# 📊 Documentation Review Report

**Date:** [TODAY]
**Project:** [PROJECT_NAME]
**Status:** ⚠️ NEEDS ATTENTION

## 🎯 Executive Summary

- **Total Files:** X in docs/
- **Organization Score:** Y% (target: 80%+)
- **Critical Issues:** Z found
- **Estimate to Fix:** X hours

## 📁 Structure Assessment

### ✅ What's Good

- Item 1
- Item 2

### ⚠️ What Needs Work

- Item 1
- Item 2

### 🔴 Critical Issues

1. **Issue:** [Description]
   **Impact:** [Why it matters]
   **Fix:** [Solution]

2. **Issue:** [Description]
   **Impact:** [Why it matters]
   **Fix:** [Solution]

## 📋 Consolidation Plan

[See CONSOLIDATION CHECKLIST above for format]

## ✅ Consolidation Checklist

### File Operations

- [ ] Create guides/troubleshooting/ folder
- [ ] Move 4-5 troubleshooting files
- [ ] Archive 6-8 outdated guides
- [ ] Create 4 component README files
- [ ] Update 00-README.md with new links
- [ ] Delete DOCUMENTATION_REVIEW.md (this report)

### Verification

- [ ] No broken links in 00-README.md
- [ ] All guides/ files listed in README
- [ ] All component docs in place
- [ ] archive-old/ contains only historical files
- [ ] No orphaned .md files

## 📞 Next Steps

1. Review plan with team
2. Confirm critical files to keep
3. Execute consolidation step-by-step
4. Verify all links work
5. Commit changes with "docs: consolidate documentation"
6. Schedule next review (quarterly)
   🎯 CONSOLIDATION BEST PRACTICES
   Naming Conventions
   Core docs: [NUMBER]-[TITLE].md (00, 01, 02...)
   Guides: [TITLE].md (no number)
   Troubleshooting: [NUMBER]-[ISSUE].md (01, 02, 03...)
   Component docs: [component]/README.md
   Reference: [TOPIC].md (no number)
   Archive: [ORIGINAL_NAME].md (preserve original name)
   Folder Organization
   docs/
   ├── (8 numbered core files here)
   ├── components/ (one folder per component)
   ├── guides/ (5-8 key guides)
   │ └── troubleshooting/ (5-10 common issues)
   ├── reference/ (5-10 spec docs)
   └── archive-old/ (historical, marked clearly)
   Link Standards
   Every documentation file should:

✅ Appear in a README.md or index file
✅ Have a clear purpose (component, guide, reference, etc.)
✅ Link back to main hub (docs/00-README.md)
✅ Include "Last Updated" date
✅ Be in the correct folder for its type
File Deletion Guidelines
Keep files if:

Currently used by developers
Provide unique, irreplaceable information
Referenced from main hub
Archive files if:

Marked "COMPLETE" or dated
Superseded by newer docs
Historical or session notes
Nice-to-have but not critical
Delete files if:

Duplicate of existing content
Clearly outdated and not valuable
Orphaned (not linked from anywhere)
Session-specific (dated, project-specific)
🔄 AUTOMATION IDEAS
Consider creating these helpers:

Link Checker Script

# Find broken links in docs/

grep -r "\[._\](._\.md)" docs/ | \
 while read line; do
link=$(echo "$line" | grep -oP '\(\K[^)]\*')
if [ ! -f "$link" ]; then
echo "BROKEN: $link"
fi
done
Orphaned File Detector

# Find .md files not referenced anywhere

for file in docs/\*_/_.md; do
if ! grep -r "$(basename $file .md)" docs/ > /dev/null; then
echo "ORPHANED: $file"
fi
done
Documentation Index Generator

Auto-generate table of contents for 00-README.md
Auto-generate component overview tables
Auto-generate troubleshooting index
📞 SUPPORT
When using this prompt:

Customize the project information at the top
Answer the clarifying questions honestly
Review recommendations before executing
Execute step-by-step (don't do everything at once)
Test links after each major change
Commit frequently with clear messages
Example commit messages:

git commit -m "docs: consolidate troubleshooting guides"
git commit -m "docs: archive outdated setup documentation"
git commit -m "docs: move package manager strategy to guides"
git commit -m "docs: create component README files"
git commit -m "docs: update main hub with new documentation links"
