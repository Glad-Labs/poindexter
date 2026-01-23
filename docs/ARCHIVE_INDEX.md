# Documentation Archive Index

**Last Updated:** January 23, 2026  
**Total Archived Files:** 1,227 consolidated from 1,150+

## Archive Overview

To significantly reduce documentation clutter, old session reports, phase summaries, and one-off status files have been consolidated into two compressed archives.

### Active Archives

#### 1. `archive-old-sessions.tar.gz` (3.5MB)

- **Contains:** 1,181 historical session and phase-specific documentation
- **Date Range:** November 2024 - December 2025
- **Structure:**
  - `legacy-root-docs/` - Original root-level status files
  - `root-level/` - Various root documentation attempts
  - `session-files/` - Individual session work logs
  - `sessions/` - Grouped session summaries
  - `root-cleanup/` - Cleanup phase documentation
  - `root-level-sessions/` - Session-specific cleanup files
  - `cleanup-history/` - Historical cleanup tracking
  - `reference/` - Technical reference documents
  - Additional subdirectories for phases and specific work

**To extract:**

```bash
tar -xzf docs/archive-old-sessions.tar.gz
```

#### 2. `archive-root-consolidated.tar.gz` (137KB)

- **Contains:** 46 outdated root-level documentation files
- **Date Range:** December 2025 - January 2026
- **Includes:**
  - Phase completion reports (consolidated)
  - Cleanup summaries
  - Gemini testing documentation (now in components/)
  - Deprecated cleanup status files

**To extract:**

```bash
tar -xzf docs/archive-root-consolidated.tar.gz
```

### Active Documentation Folder

#### Root-Level Documentation (23 files)

Located in repository root:

- `README.md` - Main project overview
- `README_AUDIT.md` - Code audit findings
- `README_GEMINI.md` - Gemini integration guide
- Audit and testing reports from January 2025
- Quick reference guides
- Status summaries for recent fixes (Jan 2025)

#### Core Documentation (8 files)

Located in `docs/`:

- `00-README.md` - Documentation hub
- `01-SETUP_AND_OVERVIEW.md` - Getting started
- `02-ARCHITECTURE_AND_DESIGN.md` - System design
- `03-DEPLOYMENT_AND_INFRASTRUCTURE.md` - Deployment guide
- `04-DEVELOPMENT_WORKFLOW.md` - Git and CI/CD
- `05-AI_AGENTS_AND_INTEGRATION.md` - Agent documentation
- `06-OPERATIONS_AND_MAINTENANCE.md` - Operations
- `07-BRANCH_SPECIFIC_VARIABLES.md` - Environment variables

#### Organized Subdirectories

- `docs/components/` - Component-specific documentation
- `docs/decisions/` - Architectural decision records
- `docs/reference/` - API contracts and technical specs
- `docs/troubleshooting/` - Problem resolution guides
- `docs/archive-active/` - Active but less frequently used docs

## Consolidation Statistics

| Category            | Before                 | After              | Reduction |
| ------------------- | ---------------------- | ------------------ | --------- |
| Root folder         | 84 files               | 23 files           | 73%       |
| Active archive      | 0 files                | 2 .tar.gz          | -         |
| Compressed old docs | 1,150+ dirs            | 2 archives (3.6MB) | 99%       |
| Total footprint     | ~500+ MB (loose files) | ~3.7 MB (archived) | 99%       |

## Finding Information

### For Recent Work (Jan 2025)

Check root folder files:

- `CRITICAL_BUG_FIXES_20250117.md` - Latest critical fixes
- `E2E_TESTING_SUMMARY.md` - Testing status
- `FINAL_OVERSIGHT_HUB_AUDIT_REPORT_20250116.md` - Component audit
- `TASK_COMPONENTS_ACCESSIBILITY_AUDIT.md` - UI accessibility

### For Older Work (Nov-Dec 2024)

Extract and search the relevant archive:

```bash
# Search in old sessions
tar -xzf docs/archive-old-sessions.tar.gz && grep -r "YOUR_SEARCH_TERM" archive-old/

# Search in root consolidation
tar -xzf docs/archive-root-consolidated.tar.gz && grep -r "YOUR_SEARCH_TERM" archive-consolidated/
```

### For Project Context

Always start with:

1. `docs/00-README.md` - Navigation hub
2. `docs/02-ARCHITECTURE_AND_DESIGN.md` - System overview
3. Root `README.md` - Project status

## Archive Extraction Tips

Extract everything:

```bash
cd docs && tar -xzf archive-old-sessions.tar.gz && tar -xzf archive-root-consolidated.tar.gz
```

Extract specific files (without full extraction):

```bash
tar -xzf docs/archive-old-sessions.tar.gz "archive-old/path/to/file.md"
```

Search compressed archives without extracting:

```bash
tar -tzf docs/archive-old-sessions.tar.gz | grep "search_term"
```

## Maintenance Notes

- Archives are updated as project phases complete
- Very old files (>6 months) should be moved to `.tar.gz` format
- Keep root folder to <30 active documentation files
- Link to archives from main documentation as needed for historical context

---

**Next consolidation recommended:** June 2026 (when current working docs become historical)
