# 📋 Documentation Cleanup Plan

**Date:** November 5, 2025  
**Goal:** Keep only 8 core docs in /docs root; move everything else to organized subfolders

---

## Current State

### Root /docs Contains (19 files)

**Core Docs (KEEP in root):**

- ✅ `00-README.md`
- ✅ `01-SETUP_AND_OVERVIEW.md`
- ✅ `02-ARCHITECTURE_AND_DESIGN.md`
- ✅ `03-DEPLOYMENT_AND_INFRASTRUCTURE.md`
- ✅ `04-DEVELOPMENT_WORKFLOW.md`
- ✅ `05-AI_AGENTS_AND_INTEGRATION.md`
- ✅ `06-OPERATIONS_AND_MAINTENANCE.md`
- ✅ `07-BRANCH_SPECIFIC_VARIABLES.md`

**Non-Core Docs (MOVE to archive/):**

- `COMPLETION_REPORT.md` → archive/2025-11-05_COMPLETION_REPORT.md
- `DOCUMENTATION_QUICK_REFERENCE.md` → archive/reference/2025-11-05_QUICK_REFERENCE.md
- `DOCUMENTATION_STATE_SUMMARY.md` → archive/reference/2025-11-05_STATE_SUMMARY.md
- `FINAL_DOCUMENTATION_SUMMARY.md` → archive/reference/2025-11-05_FINAL_SUMMARY.md
- `INDEX.md` → archive/reference/2025-11-05_INDEX.md
- `ESLINT_V9_MIGRATION_COMPLETE.md` → archive/2025-11-05_ESLINT_V9_MIGRATION.md
- `PRODUCTION_READINESS_AUDIT_SUMMARY.md` → archive/2025-11-04_AUDIT_SUMMARY.md
- `PRODUCTION_READINESS_CHECKLIST.md` → archive/2025-11-04_CHECKLIST.md

**Folders (KEEP - organized subfolders):**

- ✅ `archive/` (for historical docs)
- ✅ `components/` (component-specific docs)
- ✅ `reference/` (technical references)
- ✅ `troubleshooting/` (troubleshooting guides)

---

## Cleanup Steps

### Phase 1: Move Files to Archive

All non-core docs move to `docs/archive/` with date prefix:

```
docs/
├── 00-README.md ✅ CORE
├── 01-SETUP_AND_OVERVIEW.md ✅ CORE
├── 02-ARCHITECTURE_AND_DESIGN.md ✅ CORE
├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md ✅ CORE
├── 04-DEVELOPMENT_WORKFLOW.md ✅ CORE
├── 05-AI_AGENTS_AND_INTEGRATION.md ✅ CORE
├── 06-OPERATIONS_AND_MAINTENANCE.md ✅ CORE
├── 07-BRANCH_SPECIFIC_VARIABLES.md ✅ CORE
├── archive/ ← MOVE ALL NON-CORE HERE
│   ├── 2025-11-05_COMPLETION_REPORT.md
│   ├── 2025-11-05_ESLINT_V9_MIGRATION.md
│   ├── reference/
│   │   ├── 2025-11-05_QUICK_REFERENCE.md
│   │   ├── 2025-11-05_STATE_SUMMARY.md
│   │   ├── 2025-11-05_FINAL_SUMMARY.md
│   │   └── 2025-11-05_INDEX.md
│   ├── ... (existing archive files)
├── components/ ✅ KEEP
│   ├── cofounder-agent/
│   ├── oversight-hub/
│   ├── public-site/
│   └── strapi-cms/
├── reference/ ✅ KEEP
│   ├── API_CONTRACT_*.md
│   ├── TESTING.md
│   └── ...
└── troubleshooting/ ✅ KEEP
    └── ...
```

### Phase 2: Verify & Update Core Docs

Each core doc must be verified line-by-line against actual codebase:

**01-SETUP_AND_OVERVIEW.md:**

- [ ] Verify Node.js version requirements (currently says 18-22)
- [ ] Check Python version (currently says 3.12+)
- [ ] Verify all command examples work on Windows PowerShell
- [ ] Check API key setup for Ollama/OpenAI/Claude/Gemini
- [ ] Verify local development URLs (port 3000, 3001, 8000, 1337)
- [ ] Test `npm run dev` command sequence
- [ ] Verify Strapi first-time setup steps

**02-ARCHITECTURE_AND_DESIGN.md:**

- [ ] Verify tech stack versions
- [ ] Check component ports and URLs
- [ ] Verify database options (PostgreSQL/SQLite)
- [ ] Check AI model provider list (Ollama, Claude, GPT, Gemini)
- [ ] Verify multi-agent system description
- [ ] Check database schemas

**03-DEPLOYMENT_AND_INFRASTRUCTURE.md:**

- [ ] Verify Railway deployment steps
- [ ] Check Vercel deployment steps
- [ ] Verify GitHub Secrets configuration
- [ ] Check environment variable names and formats
- [ ] Verify CI/CD workflow triggers
- [ ] Test deployment commands

**04-DEVELOPMENT_WORKFLOW.md:**

- [ ] Verify git branch naming conventions
- [ ] Check conventional commit format examples
- [ ] Verify test commands (npm test, pytest, etc.)
- [ ] Check coverage requirements
- [ ] Verify PR template
- [ ] Check release numbering scheme

**05-AI_AGENTS_AND_INTEGRATION.md:**

- [ ] Verify agent types and capabilities
- [ ] Check model fallback chain order
- [ ] Verify memory system design
- [ ] Check MCP integration details
- [ ] Verify agent configuration examples

**06-OPERATIONS_AND_MAINTENANCE.md:**

- [ ] Verify health check endpoints
- [ ] Check backup procedures
- [ ] Verify monitoring setup
- [ ] Check troubleshooting steps
- [ ] Verify runbook procedures
- [ ] Check metrics and thresholds

**07-BRANCH_SPECIFIC_VARIABLES.md:**

- [ ] Verify environment variable names
- [ ] Check GitHub Actions workflow trigger conditions
- [ ] Verify Railway/Vercel deployment targets
- [ ] Check database URL formats
- [ ] Verify API endpoint examples

**00-README.md:**

- [ ] Update to reflect core-only structure
- [ ] Remove references to moved docs
- [ ] Update links to archived materials
- [ ] Clarify "high-level only" policy
- [ ] Verify all section references

---

## Files to Create

### 1. Archive Index (docs/archive/README.md)

Purpose: Explain what's in archive and why

```markdown
# 📦 Documentation Archive

This folder contains historical documentation, completion reports, and reference materials that are no longer actively maintained but are useful for understanding project history and decisions.

## Contents

- `2025-11-*_*.md` - Session completion reports and status summaries
- `reference/` - Reference documentation snapshots
- `sessions/` - Old session work logs
- `phases/` - Phase completion reports

## Policy

Archive files are **read-only**. They are not updated as code changes. For current information, see the core 8 docs in the parent directory.

## Last Updated

November 5, 2025
```

### 2. Reference Index (docs/reference/README.md)

Purpose: Explain what's in reference and how to use it

```markdown
# 📚 Technical Reference Documentation

This folder contains technical specifications, API contracts, testing guides, and other reference materials.

## Contents

- `API_CONTRACT_*.md` - API endpoint specifications
- `TESTING.md` - Testing guide and examples
- `GLAD-LABS-STANDARDS.md` - Code standards
- `data_schemas.md` - Database schema reference
- And more...

## Usage

These docs are organized by topic and linked from core docs (00-07). Use the index below to find what you need.

[Detailed index with links to all reference docs]

## Maintenance

Reference docs are updated as-needed when relevant systems change. See core docs for when/how updates happen.

## Last Updated

November 5, 2025
```

### 3. Components Index (docs/components/README.md)

Purpose: Explain component organization

```markdown
# 🧩 Component Documentation

This folder contains documentation for individual Glad Labs components.

## Components

- `strapi-cms/` - Strapi v5 CMS documentation
- `oversight-hub/` - React dashboard admin panel
- `public-site/` - Next.js public website
- `cofounder-agent/` - FastAPI AI orchestrator backend

## Usage

Each component has:

- `README.md` - Overview and architecture
- `troubleshooting/` - Common issues and solutions
- Development setup instructions
- Testing guidelines
- Deployment procedures

## Maintenance

Component docs are updated per release or when architecture changes. See core docs for update procedures.

## Last Updated

November 5, 2025
```

---

## Success Criteria

✅ Only 8 core docs in /docs root  
✅ All other docs moved to appropriate subfolders  
✅ Archive files have date prefixes  
✅ All core docs verified against actual codebase  
✅ 00-README.md updated to reflect structure  
✅ All moved docs remain accessible via cross-links  
✅ Zero broken links in documentation

---

## Timeline

- **Phase 1 (Move Files):** 10 minutes
- **Phase 2 (Verify Docs):** 2-3 hours (thorough line-by-line review)
- **Phase 3 (Update References):** 30 minutes
- **Phase 4 (Commit & Push):** 5 minutes

**Total: ~3 hours for complete cleanup and verification**

---

## Git Commit Message

```
docs: consolidate to core docs only - archive misc files

BREAKING CHANGE: Documentation structure reorganized

- Move all non-core docs to docs/archive/ with date prefixes
- Keep only 8 core docs in root: 00-README through 07-BRANCH_SPECIFIC_VARIABLES
- Create index files in archive/, reference/, and components/
- Verify all core docs against current codebase state
- Update links and references throughout

This implements the "high-level only" policy where only architecture-stable
documentation remains in root, with historical materials organized in archive.

Docs structure now:
- docs/ (8 core + 4 index folders)
- docs/archive/ (historical docs)
- docs/reference/ (technical specs)
- docs/components/ (per-component docs)
- docs/troubleshooting/ (problem solutions)
```

---

**Status:** Ready to execute  
**Owner:** Matt M. Gladding  
**Last Updated:** November 5, 2025
