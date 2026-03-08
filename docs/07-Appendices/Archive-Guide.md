# Archive Navigation Guide

This document helps you find and understand archived documentation that is no longer in active use but may be useful for historical context or reference.

## Archive Locations

### Primary Archives

- **`docs/archive-active/`** - Main archive for historical documentation and reports
- **`docs/archive/`** - Legacy/historical archive (older content)
- **`.archive/cleanup-feb2026/`** - Oversight Hub service-specific archived docs

---

## Archive Directories in `docs/archive-active/`

### 1. **root-cleanup-feb2026/**

**Purpose:** Root-level documentation files moved during February 2026 cleanup
**Contains:** Phase reports, session summaries, quick references from previous development phases
**Size:** ~20 files
**When to use:**

- Understanding previous implementation phases
- Finding context about early development decisions
- Referencing historical quick start guides that may no longer apply

**Notable files:**

- PHASE_1B_1C_DISCOVERY_QUICK_REF.md
- SESSION_QUICK_REFERENCE.md
- QUICK_REFERENCE.md
- SPRINT_2_QUICK_REFERENCE.md
- OVERSIGHT_HUB_QUICK_START.md

---

### 2. **audits-and-testing/**

**Purpose:** Test audits, E2E references, and testing documentation from past iterations
**Contains:** Testing procedures, audit reports, quality assurance documentation
**Size:** ~5 files
**When to use:**

- Understanding historical test structures
- Finding old E2E testing patterns (reference only - use current TESTING.md)
- Reviewing past audit findings

**Notable files:**

- E2E_QUICK_REFERENCE.md - Old E2E testing guide
- Implementation testing docs

---

### 3. **cleanup-and-implementation/**

**Purpose:** Documentation from code cleanup and refactoring sessions
**Contains:** Cleanup procedures, implementation quick references, cookie banner setup
**Size:** ~3 files
**When to use:**

- Understanding past code organization changes
- Reference for similar cleanup tasks
- Cookie banner implementation history

**Notable files:**

- CLEANUP_QUICK_REFERENCE.txt
- PRIORITY_1_QUICK_REFERENCE.txt
- QUICK_START_COOKIE_BANNER.txt

---

### 4. **historical-reports/**

**Purpose:** Session reports, audit summaries, and historical documentation
**Contains:** Documentation audit results, environment variable references, implementation notes
**Size:** ~4 files
**When to use:**

- Understanding system audit histories
- Environmental configuration historical context
- Finding past documentation decisions

**Notable files:**

- DOCUMENTATION_AUDIT_QUICK_START.md
- ENV_QUICK_REFERENCE.md

---

### 5. **debug-logs/**

**Purpose:** Debugging information and troubleshooting logs from past sessions
**Contains:** Error logs, debug traces, problem-solving documentation
**Size:** Varies
**When to use:**

- Researching similar bugs that have appeared before
- Understanding how past issues were diagnosed
- Learning troubleshooting approaches

---

## Active vs. Archived Quick References

### Active Quick Start Guides (in `docs/reference/`)

These are **current** and should be used for active development:

- **QUICK_START_GUIDE.md** - Capability system guide
- **TASK_STATUS_QUICK_START.md** - Task status implementation checklist
- **QUICK_FIX_COMMANDS.md** (in troubleshooting/) - Live troubleshooting

### Active Analytics Documentation

- **ANALYTICS_QUICK_START.md** (in `docs/`) - Analytics and profiling guide

### Archived Quick References

These should **not** be used for current work:

- All `QUICK_REFERENCE*.txt` files in archive-active/
- All `*_QUICK_REFERENCE.md` files in root-cleanup-feb2026/
- SESSION_QUICK_REFERENCE.md
- Phase-specific quick references

---

## Service-Specific Archives

### Oversight Hub Archive

**Location:** `web/oversight-hub/archive/cleanup-feb2026/`
**Contains:** Oversight Hub-specific cleanup and implementation docs
**When to use:** Understanding Oversight Hub development history

**Notable files:**

- QUICK_FIX_GUIDE.md
- QUICK_REFERENCE.md

### Public Site Archive

**Location:** `web/public-site/archive/cleanup-feb2026/`
**Contains:** Public Site-specific archived documentation
**When to use:** Understanding Public Site development history

---

## How to Use Archives Safely

### ✅ DO

- Use archives for **historical context** and **understanding past decisions**
- Reference archived guides when dealing with **similar problems**
- Check archives when you need **implementation examples** from previous phases
- Read audit reports to understand **past quality issues**

### ❌ DON'T

- Copy code from archived documents without verification
- Follow archived quick references as current processes
- Assume archived test procedures are still valid
- Copy archived environment configurations without checking current `.env.example`

---

## Finding What You Need

### If you're looking for

- Current setup instructions: `docs/01-Getting-Started/Local-Development-Setup.md` (`docs/01-Getting-Started/`)
- Current API documentation: `docs/reference/API_CONTRACTS.md` (`docs/reference/`)
- Current testing guide: `docs/reference/TESTING.md` (`docs/reference/`)
- Current architecture: `docs/02-Architecture/System-Design.md` (`docs/02-Architecture/`)
- Current troubleshooting: `docs/troubleshooting/` (`docs/troubleshooting/`)
- Historical context: `docs/archive-active/` (`docs/archive-active/`)
- Past phase documentation: `docs/archive-active/root-cleanup-feb2026/` (`docs/archive-active/`)
- Test implementation history: `docs/archive-active/audits-and-testing/` (`docs/archive-active/`)
- Cleanup procedures: `docs/archive-active/cleanup-and-implementation/` (`docs/archive-active/`)

---

## Archive Maintenance

**Last Reviewed:** February 25, 2026
**Maintenance Schedule:** Quarterly review of archive structure
**Review Process:**

1. Check if any archived docs answer frequently-asked questions
2. Consider promoting useful archived content to active docs
3. Consolidate similar archived topics
4. Remove genuinely obsolete documents

**Next Review Date:** May 25, 2026

---

## Questions?

For questions about specific archived documentation:

1. Check the **`INDEX.md`** file in the relevant archive subdirectory
2. Refer to **`docs/00-README.md`** for the main documentation hub
3. Check **`docs/04-Development/Development-Workflow.md`** for current processes
