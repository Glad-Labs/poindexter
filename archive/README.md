# Documentation Cleanup System

**Automated, intelligent documentation organization for the Glad Labs codebase.**

## Quick Start

```bash
# Run cleanup (cross-platform)
npm run docs:cleanup

# Or platform-specific:
npm run docs:cleanup:ps    # PowerShell (Windows)
npm run docs:cleanup:sh    # Bash (Unix/Linux/Mac)
```

## What It Does

The cleanup system **intelligently organizes stray documentation** by:

1. **Creating organized archive structure**:
   - `archive/sessions/` → Session summaries, implementation reports
   - `archive/phase1/`, `phase2/`, `phase3/` → Phase-specific documentation
   - `archive/testing/` → Testing documentation
   - `archive/sprints/` → Sprint reports

2. **Creating organized docs structure**:
   - `docs/reference/` → Guides and reference materials
   - `docs/troubleshooting/` → Debugging and error guides
   - `docs/decisions/` → ADRs and decision records
   - `docs/components/` → Component-specific documentation

3. **Moving files automatically** based on naming patterns:
   - `PHASE*.md` → Appropriate `archive/phase*/` based on number
   - `SESSION*.md`, `CONSOLIDATION*.md` → `archive/sessions/`
   - `TESTING*.md`, `TEST_*.md` → `archive/testing/`
   - `SPRINT*.md` → `archive/sprints/`
   - `*_SUMMARY.md`, `*_FIX.md`, `*_STATUS.md` → `archive/sessions/`
   - Other files → Categorized by content keywords

## Protected Files

These files **always stay in root** and are never moved:

- `README.md`
- `CLAUDE.md`
- `VERSION_HISTORY.md`
- `SECURITY.md`
- `VERSIONING_GUIDE.md`
- `DEBUG_GUIDE.md`
- `DEPLOYMENT_CHECKLIST.md`
- `LICENSE` / `LICENSE.md`

## File Categorization Rules

The cleanup system uses **intelligent pattern matching** to determine where files belong:

### Archive Rules

| Pattern                                     | Destination         | Examples                     |
| ------------------------------------------- | ------------------- | ---------------------------- |
| `PHASE_?1*`, `PHASE1_*`                     | `archive/phase1/`   | PHASE_1_COMPLETION_REPORT.md |
| `PHASE_?2*`, `PHASE2_*`                     | `archive/phase2/`   | PHASE2_TEST_STATUS.md        |
| `PHASE_?3*`, `PHASE3_*`                     | `archive/phase3/`   | PHASE_3_SECURITY.md          |
| `SESSION*`, `CONSOLIDATION*`                | `archive/sessions/` | SESSION_SUMMARY_PHASE1.md    |
| `*_SUMMARY`, `*_FIX`, `*_STATUS`            | `archive/sessions/` | IMPLEMENTATION_SUMMARY.md    |
| `TESTING*`, `TEST_*`, `USER_TESTING*`       | `archive/testing/`  | TESTING_GUIDE.md             |
| `SPRINT*`                                   | `archive/sprints/`  | SPRINT_4_COMPLETION.md       |
| `IMPLEMENTATION*`, `*_REPORT`, `*_COMPLETE` | `archive/sessions/` | PHASE_1_COMPLETION_REPORT.md |

### Docs Rules

| Pattern                                | Destination             | Examples                 |
| -------------------------------------- | ----------------------- | ------------------------ |
| `*GUIDE*`                              | `docs/reference/`       | DEPLOYMENT_GUIDE.md      |
| `*TROUBLESHOOT*`, `*DEBUG*`, `*ERROR*` | `docs/troubleshooting/` | ERROR_HANDLING_DEBUG.md  |
| `*ADR*`, `*DECISION*`                  | `docs/decisions/`       | ADR_001_ARCHITECTURE.md  |
| `*COMPONENT*`, `*MODULE*`              | `docs/components/`      | AUTHENTICATION_MODULE.md |
| All other `.md` files                  | `docs/reference/`       | DEVELOPER_ONBOARDING.md  |

## Git Integration

The cleanup system is **git-aware**:

- Uses `git mv` for tracked files (preserves history)
- Falls back to regular `mv` for untracked files
- Safe to run on any branch
- Won't break your git history

## Safety Features

✅ **Non-destructive** - Files are moved, not deleted  
✅ **Idempotent** - Safe to run multiple times  
✅ **Protected roots** - Essential files never moved  
✅ **Error handling** - Reports errors without stopping  
✅ **Cross-platform** - Works on Windows, Mac, Linux

## Usage Examples

### Run cleanup after completing work

```bash
# After finishing a phase/sprint
git add .
npm run docs:cleanup
git add .
git commit -m "docs: archive completed phase documentation"
```

### Check what would be moved (dry-run)

```bash
# See what files exist that match patterns
ls -la PHASE*.md SESSION*.md TESTING*.md SPRINT*.md
```

### Manual cleanup

```bash
# Windows
powershell -ExecutionPolicy Bypass -File cleanup-docs.ps1

# Unix/Linux/Mac
bash scripts/cleanup-docs.sh
```

## Extending the System

To add new categorization rules, edit:

- **PowerShell**: `cleanup-docs.ps1` (lines 90-140)
- **Bash**: `scripts/cleanup-docs.sh` (lines 90-140)

Example - Add new pattern:

```powershell
# In cleanup-docs.ps1
$myPatterns = @("CUSTOM*.md")
foreach ($pattern in $myPatterns) {
    Get-ChildItem -Path "." -Filter $pattern | ForEach-Object {
        if (Safe-Move $_.Name "docs/custom/") {
            Write-Host "  ✓ Moved $($_.Name) → docs/custom/"
            $script:moved++
        }
    }
}
```

```bash
# In scripts/cleanup-docs.sh
for file in CUSTOM*.md; do
    [[ ! -f "$file" ]] && continue
    if safe_move "$file" "docs/custom/"; then
        echo "  ✓ Moved $file → docs/custom/"
        ((moved++))
    fi
done
```

## Maintenance Schedule

**Recommended frequency**: After completing each phase/sprint

**Automated option**: See `.github/workflows/documentation-cleanup.yml` for monthly GitHub Action

## Troubleshooting

### Script won't run on Windows

```powershell
# Enable script execution
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### Script won't run on Unix

```bash
# Make executable
chmod +x scripts/cleanup-docs.sh
```

### Files not moving

- Check file isn't in protected list
- Verify archive directories exist
- Check git status (conflicts block moves)

### Cleanup moved wrong file

```bash
# Undo with git (if file was tracked)
git log --all --full-history -- "path/to/file"
git restore --source=<commit> path/to/file
```

## Integration with VERSION_HISTORY.md

After running cleanup, remember to:

1. Update `VERSION_HISTORY.md` if completing a phase
2. Document what was archived
3. Update archive structure section

## Related Documentation

- [VERSION_HISTORY.md](../VERSION_HISTORY.md) - Project timeline and phase tracking
- [Documentation-Maintenance.md](../docs/07-Appendices/Documentation-Maintenance.md) - Full maintenance guide
- [00-README.md](../docs/00-README.md) - Documentation navigation hub

---

**Last Updated**: March 7, 2026  
**Maintainer**: Glad Labs Development Team
