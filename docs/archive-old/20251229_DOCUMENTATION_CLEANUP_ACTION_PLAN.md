# Documentation Cleanup Action Plan

**Date Created:** December 23, 2025  
**Status:** READY FOR EXECUTION  
**Estimated Time:** 15 minutes  
**Tool Required:** PowerShell 6+ (pwsh) or manual file operations

---

## Overview

This action plan implements the **HIGH-LEVEL ONLY documentation policy** by archiving 11 violation files from `docs/` and 1 file from root directory. These files are session-specific summaries, implementation checklists, and temporary guides that violate the architecture-focused documentation standard.

---

## Files to Archive

### Root Directory (1 file)

| File                                | Reason                       | Archive Name                                 |
| ----------------------------------- | ---------------------------- | -------------------------------------------- |
| `DEBUG_CONTENT_GENERATION_FIXES.md` | Session-specific debug notes | `20251223_DEBUG_CONTENT_GENERATION_FIXES.md` |

### docs/ Directory (11 files)

| File                                               | Reason                                              | Archive Name                                                |
| -------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------------------- |
| `CONSOLIDATION_PLAN_DEC_19.md`                     | Session planning document                           | `20251219_CONSOLIDATION_PLAN_DEC_19.md`                     |
| `ENTERPRISE_DOCUMENTATION_FRAMEWORK.md`            | Meta-documentation (already reflected in core docs) | `20251223_ENTERPRISE_DOCUMENTATION_FRAMEWORK.md`            |
| `LLM_SELECTION_IMPLEMENTATION_CHECKLIST.md`        | Implementation checklist (temporary)                | `20251223_LLM_SELECTION_IMPLEMENTATION_CHECKLIST.md`        |
| `LLM_SELECTION_LOG_EXAMPLES.md`                    | Implementation examples (temporary)                 | `20251223_LLM_SELECTION_LOG_EXAMPLES.md`                    |
| `LLM_SELECTION_PERSISTENCE_FIXES.md`               | Bug fix summary (temporary)                         | `20251223_LLM_SELECTION_PERSISTENCE_FIXES.md`               |
| `LLM_SELECTION_QUICK_SUMMARY.md`                   | Feature summary (temporary)                         | `20251223_LLM_SELECTION_QUICK_SUMMARY.md`                   |
| `QUICK_NAVIGATION_GUIDE.md`                        | Session welcome guide (temporary)                   | `20251223_QUICK_NAVIGATION_GUIDE.md`                        |
| `SESSION_DEC_19_CONSOLIDATION_SUMMARY.md`          | Session summary (temporary)                         | `20251219_SESSION_DEC_19_CONSOLIDATION_SUMMARY.md`          |
| `WARNINGS_REFERENCE.md`                            | Implementation notes (temporary)                    | `20251223_WARNINGS_REFERENCE.md`                            |
| `WARNING_RESOLUTION_SQL_PATTERN_MODEL_PROVIDER.md` | Bug fix notes (temporary)                           | `20251223_WARNING_RESOLUTION_SQL_PATTERN_MODEL_PROVIDER.md` |

### decisions/ Directory (1 file to archive)

| File                                           | Reason                                        | Archive Name                                            |
| ---------------------------------------------- | --------------------------------------------- | ------------------------------------------------------- |
| `FRONTEND_BACKEND_INTEGRATION_STATUS_DEC19.md` | Status update (not an architectural decision) | `20251219_FRONTEND_BACKEND_INTEGRATION_STATUS_DEC19.md` |

---

## Execution Commands

### Option 1: PowerShell 6+ (pwsh) - Recommended

```powershell
# Navigate to repo root
cd c:\Users\mattm\glad-labs-website

# Archive root file
Move-Item -Path ".\DEBUG_CONTENT_GENERATION_FIXES.md" -Destination ".\docs\archive-old\20251223_DEBUG_CONTENT_GENERATION_FIXES.md" -Force

# Archive docs/ files
Move-Item -Path ".\docs\CONSOLIDATION_PLAN_DEC_19.md" -Destination ".\docs\archive-old\20251219_CONSOLIDATION_PLAN_DEC_19.md" -Force
Move-Item -Path ".\docs\ENTERPRISE_DOCUMENTATION_FRAMEWORK.md" -Destination ".\docs\archive-old\20251223_ENTERPRISE_DOCUMENTATION_FRAMEWORK.md" -Force
Move-Item -Path ".\docs\LLM_SELECTION_IMPLEMENTATION_CHECKLIST.md" -Destination ".\docs\archive-old\20251223_LLM_SELECTION_IMPLEMENTATION_CHECKLIST.md" -Force
Move-Item -Path ".\docs\LLM_SELECTION_LOG_EXAMPLES.md" -Destination ".\docs\archive-old\20251223_LLM_SELECTION_LOG_EXAMPLES.md" -Force
Move-Item -Path ".\docs\LLM_SELECTION_PERSISTENCE_FIXES.md" -Destination ".\docs\archive-old\20251223_LLM_SELECTION_PERSISTENCE_FIXES.md" -Force
Move-Item -Path ".\docs\LLM_SELECTION_QUICK_SUMMARY.md" -Destination ".\docs\archive-old\20251223_LLM_SELECTION_QUICK_SUMMARY.md" -Force
Move-Item -Path ".\docs\QUICK_NAVIGATION_GUIDE.md" -Destination ".\docs\archive-old\20251223_QUICK_NAVIGATION_GUIDE.md" -Force
Move-Item -Path ".\docs\SESSION_DEC_19_CONSOLIDATION_SUMMARY.md" -Destination ".\docs\archive-old\20251219_SESSION_DEC_19_CONSOLIDATION_SUMMARY.md" -Force
Move-Item -Path ".\docs\WARNINGS_REFERENCE.md" -Destination ".\docs\archive-old\20251223_WARNINGS_REFERENCE.md" -Force
Move-Item -Path ".\docs\WARNING_RESOLUTION_SQL_PATTERN_MODEL_PROVIDER.md" -Destination ".\docs\archive-old\20251223_WARNING_RESOLUTION_SQL_PATTERN_MODEL_PROVIDER.md" -Force

# Archive decisions/ status file
Move-Item -Path ".\docs\decisions\FRONTEND_BACKEND_INTEGRATION_STATUS_DEC19.md" -Destination ".\docs\archive-old\20251219_FRONTEND_BACKEND_INTEGRATION_STATUS_DEC19.md" -Force
```

### Option 2: Windows File Explorer (Manual)

1. Open File Explorer and navigate to `c:\Users\mattm\glad-labs-website`
2. Select all files listed in the tables above
3. Cut (Ctrl+X) each file
4. Navigate to `c:\Users\mattm\glad-labs-website\docs\archive-old\`
5. Paste (Ctrl+V) each file
6. Rename each file with the prefix shown in "Archive Name" column

### Option 3: Node.js Script

```javascript
// Save as cleanup.js and run: node cleanup.js
const fs = require('fs');
const path = require('path');

const moves = [
  [
    'DEBUG_CONTENT_GENERATION_FIXES.md',
    '20251223_DEBUG_CONTENT_GENERATION_FIXES.md',
  ],
  [
    'docs/CONSOLIDATION_PLAN_DEC_19.md',
    '20251219_CONSOLIDATION_PLAN_DEC_19.md',
  ],
  [
    'docs/ENTERPRISE_DOCUMENTATION_FRAMEWORK.md',
    '20251223_ENTERPRISE_DOCUMENTATION_FRAMEWORK.md',
  ],
  [
    'docs/LLM_SELECTION_IMPLEMENTATION_CHECKLIST.md',
    '20251223_LLM_SELECTION_IMPLEMENTATION_CHECKLIST.md',
  ],
  [
    'docs/LLM_SELECTION_LOG_EXAMPLES.md',
    '20251223_LLM_SELECTION_LOG_EXAMPLES.md',
  ],
  [
    'docs/LLM_SELECTION_PERSISTENCE_FIXES.md',
    '20251223_LLM_SELECTION_PERSISTENCE_FIXES.md',
  ],
  [
    'docs/LLM_SELECTION_QUICK_SUMMARY.md',
    '20251223_LLM_SELECTION_QUICK_SUMMARY.md',
  ],
  ['docs/QUICK_NAVIGATION_GUIDE.md', '20251223_QUICK_NAVIGATION_GUIDE.md'],
  [
    'docs/SESSION_DEC_19_CONSOLIDATION_SUMMARY.md',
    '20251219_SESSION_DEC_19_CONSOLIDATION_SUMMARY.md',
  ],
  ['docs/WARNINGS_REFERENCE.md', '20251223_WARNINGS_REFERENCE.md'],
  [
    'docs/WARNING_RESOLUTION_SQL_PATTERN_MODEL_PROVIDER.md',
    '20251223_WARNING_RESOLUTION_SQL_PATTERN_MODEL_PROVIDER.md',
  ],
  [
    'docs/decisions/FRONTEND_BACKEND_INTEGRATION_STATUS_DEC19.md',
    '20251219_FRONTEND_BACKEND_INTEGRATION_STATUS_DEC19.md',
  ],
];

const base = 'c:\\Users\\mattm\\glad-labs-website\\';
const archiveDir = base + 'docs\\archive-old\\';

moves.forEach(([src, dest]) => {
  const srcPath = path.join(base, src);
  const destPath = path.join(archiveDir, dest);

  if (fs.existsSync(srcPath)) {
    fs.renameSync(srcPath, destPath);
    console.log(`✅ Moved: ${src} → ${dest}`);
  } else {
    console.log(`⚠️  Not found: ${src}`);
  }
});

console.log('\n✅ Documentation cleanup complete!');
```

---

## After Archiving: Update 00-README.md

Once files are archived, update `docs/00-README.md` to reflect the changes:

### Changes Needed

1. **Line 6:** Remove reference to `ENTERPRISE_DOCUMENTATION_FRAMEWORK.md`
2. **Line 70:** Remove the line about Frontend-Backend Integration Status
3. **Line 86:** Update file count from "260+ files" to "271 files" (260 + 11 new archives)
4. **Line 10:** Update session consolidation note to mention December 23 cleanup

### Updated Lines

Replace lines 6-11:

```markdown
**Last Updated:** December 23, 2025 (HIGH-LEVEL ONLY Policy Enforcement Complete)  
**Status:** ✅ All 8 Core Docs Complete | 271 Files Archived | Production Ready  
**Documentation Policy:** 🎯 HIGH-LEVEL ONLY (Architecture-Focused, Zero Maintenance Burden)

> **Policy:** This hub contains only high-level, architecture-stable documentation. Implementation details belong in code. Feature how-tos belong in code comments. Status updates are not maintained. This keeps documentation focused on what matters: system design, deployment, operations, and AI agent orchestration.

**Session Consolidation:** December 23 - archived 11 additional violation files (session summaries, implementation checklists, temporary guides) to maintain HIGH-LEVEL ONLY policy.
```

Remove line 70:

```markdown
- **[Frontend-Backend Integration Status (Dec 19)](./decisions/FRONTEND_BACKEND_INTEGRATION_STATUS_DEC19.md)** - Current architecture, implementation status, and scaling considerations
```

Update line 88:

```markdown
- **December 23 Cleanup:** 11 files (LLM selection guides, session summaries, navigation guides)
- **December 19 Session:** 118 files (session summaries, implementation plans, analysis documents)
- **Previous sessions:** 142 files (from October-December sessions)
- **Total archived:** 271 files with timestamp prefixes for audit trail
```

---

## Verification Checklist

After executing the cleanup:

- [ ] Root directory contains ONLY: `README.md`, `LICENSE`, config files, and source folders
- [ ] `docs/` contains 8 core files (00-07-\*.md)
- [ ] `docs/components/` contains 3 component READMEs
- [ ] `docs/decisions/` contains 3 files: `DECISIONS.md`, `WHY_FASTAPI.md`, `WHY_POSTGRESQL.md`
- [ ] `docs/reference/` contains ~8 technical specs
- [ ] `docs/troubleshooting/` contains ~4 focused guides
- [ ] `docs/archive-old/` contains 271 archived files
- [ ] No broken links in `docs/00-README.md`
- [ ] File count: `ls docs/*.md | wc -l` returns 8 files (Windows: `(Get-ChildItem docs\*.md).Count`)

---

## Commit Message

```bash
git add .
git commit -m "docs: enforce HIGH-LEVEL ONLY policy - archive 11 violation files

- Archive 1 root-level debug file (DEBUG_CONTENT_GENERATION_FIXES.md)
- Archive 10 docs/ session/implementation files (LLM selection, navigation, consolidation summaries)
- Archive 1 decisions/ status file (FRONTEND_BACKEND_INTEGRATION_STATUS_DEC19.md)
- Update 00-README.md to reflect 271 total archived files
- All files preserved with timestamp prefixes for audit trail
- Documentation now 100% compliant with HIGH-LEVEL ONLY policy"
```

---

## Policy Enforcement Complete

After this cleanup, Glad Labs documentation will be **100% compliant** with the HIGH-LEVEL ONLY policy:

- ✅ **8 core architecture docs** (stable, high-level)
- ✅ **3 architectural decisions** (stable, rationale-focused)
- ✅ **~8 technical references** (API specs, schemas, standards)
- ✅ **~4 troubleshooting guides** (focused, specific issues)
- ✅ **3 component READMEs** (minimal, architecture-level)
- ✅ **271 archived files** (historical context preserved)
- ✅ **Zero maintenance burden** (no guides, no status updates, no duplicates)

---

## Support

If you encounter issues during execution:

1. Ensure PowerShell 6+ is installed: `https://aka.ms/powershell`
2. Verify you're in the correct directory: `c:\Users\mattm\glad-labs-website`
3. Check file existence before moving: `Test-Path .\docs\filename.md`
4. If files were already archived in previous sessions, skip them

---

**Next Steps After Completion:**

1. Execute file moves (Option 1, 2, or 3)
2. Update `docs/00-README.md` as specified
3. Run verification checklist
4. Commit with provided message
5. Delete this action plan: `Remove-Item .\DOCUMENTATION_CLEANUP_ACTION_PLAN.md`
