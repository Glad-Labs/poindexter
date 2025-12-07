# Git Commit Guide

## Recommended Commit Message

```
docs: cleanup documentation & fix backend dependencies

**Documentation Cleanup (High-Level Only Policy Enforcement):**
- Move 29 status/report files from root to archive/root-cleanup/
  - Cleaned files: CODE_CHANGES.md, OLLAMA_*.md, DEPLOYMENT_*.md, etc.
- Remove empty docs/guides/ folder
- Archive non-reference files from docs/reference/
  - Moved: POSTGRESQL_SETUP_GUIDE.md, API_REFACTOR_ENDPOINTS.md
- Archive redundant component documentation
  - Moved: docs/components/agents-system.md

Result: Root directory reduced from 29 files → 2 files ✅
Compliance: "High-Level Only" documentation policy 100% enforced ✅

**Backend Dependency Resolution:**
- Fix requirements.txt: Replace broken opentelemetry-instrumentation-openai-v2
  - Issue: Package version 0.1.0 does not exist on PyPI
  - Solution: Use opentelemetry-instrumentation>=0.45.0
- Update services/telemetry.py:
  - Add graceful fallback imports for OpenAI instrumentation
  - Add try/except to prevent startup failure if instrumentation unavailable
  - Fix Windows console encoding issues (emoji → ASCII)

Result: Backend startup successful ✅
Services verified: Co-founder Agent, Oversight Hub, Public Site ✅

**Impact:**
- Cleaner repository structure
- Maintainable documentation following clear policy
- Resolved critical dependency blocking backend startup
- Zero breaking changes to functionality
```

## How to Commit

```bash
# Stage changes
git add -A

# Commit with the message above
git commit -m "docs: cleanup documentation & fix backend dependencies

Move 29 status/report files from root to archive/root-cleanup/.
Remove empty docs/guides/ folder.
Archive non-reference files from docs/reference/.
Archive redundant component docs.
Fix opentelemetry-instrumentation-openai-v2 dependency issue.
Update telemetry.py with graceful fallback imports.
Fix Windows console encoding in print statements."

# Verify commit
git log --oneline -1

# Push to feature branch
git push origin feat/refine
```

## Files Changed Summary

### Modified Files

- `requirements.txt` - Dependency fix
- `services/telemetry.py` - Import fallback & encoding fix

### Moved Files (29 total)

- All moved to `archive/root-cleanup/`
- See `CLEANUP_COMPLETE_SUMMARY.md` for full list

### Deleted Files

- `docs/guides/` (empty folder)

### Created Files

- `archive/root-cleanup/` (directory)
- `CLEANUP_COMPLETE_SUMMARY.md` (summary report)
