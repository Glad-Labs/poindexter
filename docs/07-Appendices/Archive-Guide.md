# Archive Navigation Guide

This document helps you find and understand archived documentation that is no longer in active use but may be useful for historical context or reference.

## Archive Locations

### Primary Archives

- **`archive/`** - Canonical archive root for historical phase/session/testing documentation
- **`docs/archive-old-sessions.tar.gz`** - Compressed snapshot of older documentation sessions
- **`docs/archive-root-consolidated.tar.gz`** - Consolidated compressed archive from root cleanup operations

---

## Archive Directories in `archive/`

### 1. **phase1/**

**Purpose:** Phase 1 completion and testing artifacts
**Contains:** Phase summaries, infrastructure/testing milestone reports
**Size:** ~4 files
**When to use:**

- Reviewing Phase 1 deliverables and decisions
- Understanding early implementation/testing checkpoints

**Notable files:**

- PHASE_1_COMPLETION_REPORT.md
- PHASE_1_TEST_INFRASTRUCTURE.md
- PHASE_1_VISUAL_SUMMARY.md

---

### 2. **phase2/**

**Purpose:** Phase 2 completion and test status artifacts
**Contains:** Phase 2 completion summaries and test status records
**Size:** ~2 files
**When to use:**

- Reviewing outcomes from Phase 2 milestones
- Cross-checking historical test pass/fail status

**Notable files:**

- PHASE2_COMPLETION_SUMMARY.md
- PHASE2_TEST_STATUS.md

---

### 3. **phase3/**

**Purpose:** Placeholder for future phase 3 archived artifacts
**Contains:** Phase 3 archival files as they are finalized
**Size:** Currently minimal
**When to use:**

- Reviewing finalized phase 3 archival records

### 4. **sessions/**

**Purpose:** Session-level implementation summaries and fix notes
**Contains:** Session summaries, implementation notes, and one-off fix reports
**Size:** ~6 files
**When to use:**

- Reconstructing work done during specific implementation sessions
- Tracing historical reasoning for targeted fixes

### 5. **testing/**

**Purpose:** Historical testing guides and completion reports
**Contains:** Legacy testing documentation retained for historical reference
**Size:** ~4 files
**When to use:**

- Finding prior testing workflows for comparison
- Understanding legacy testing process changes over time

---

### 6. **Top-level archive files**

**Purpose:** Fast access to major cross-phase summaries
**Contains:** Aggregate archive index and phase completion summaries
**Notable files:**

- `archive/README.md`
- `archive/PHASE_3A_COMPLETION_SUMMARY.md`

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
