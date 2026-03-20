# Documentation Maintenance Guide

**Last Updated:** March 5, 2026  
**Version:** 1.0  
**Owner:** Development Team

This guide ensures documentation stays current, consistent, and useful as the project evolves. It provides workflows, checklists, and conventions for maintaining all documentation files.

---

## Documentation File Inventory

### Core Reference Files

- `.github/copilot-instructions.md`: Copilot-specific guidance and project overview. Audience: AI assistants (Copilot). Update: when major phases complete.
- `CLAUDE.md`: Claude Code session guidance. Audience: AI assistants (Claude). Update: when major phases complete.
- `docs/02-Architecture/System-Design.md`: System architecture and design decisions. Audience: developers/architects. Update: quarterly or after major refactors.
- `docs/05-Operations/Operations-Maintenance.md`: Deployment procedures and infrastructure. Audience: DevOps/backend developers. Update: as deployment changes.
- `docs/04-Development/Development-Workflow.md`: Development processes, branching, testing. Audience: all developers. Update: as workflows evolve.
- `docs/02-Architecture/Multi-Agent-Pipeline.md`: AI agent architecture and patterns. Audience: backend developers. Update: when agents change.
- `docs/07-Appendices/Technical-Debt-Tracker.md`: Issue tracking and completion status. Audience: project leads/developers. Update: after each completed issue.
- `README.md`: Project overview and quick start. Audience: everyone/new users. Update: quarterly.
- `.env.example`: Environment variable templates. Audience: all developers. Update: when env vars change.

### Documentation Relationships

```
┌─────────────────────────────────────────────────────────┐
│       .github/copilot-instructions.md (PRIMARY)         │
│                                                         │
│  Master reference containing:                          │
│  - Project overview                                    │
│  - All service descriptions                           │
│  - Key architecture patterns                          │
│  - Environment variables                              │
│  - Debugging checklist                                │
└─────────────────────────────────────────────────────────┘
                            ↓
           ┌────────────────┼────────────────┐
           ↓                ↓                ↓
    ┌─────────────┐  ┌────────────┐  ┌──────────────┐
    │  CLAUDE.md  │  │  README.md │  │ Deep Docs    │
    │ (Condensed) │  │(Quick Ref) │  │(Detailed)    │
    └─────────────┘  └────────────┘  └──────────────┘
         ↓                ↓                ↓
      Claude        End-users       Architecture,
      sessions      & developers    Deployment, Agents
```

---

## Update Workflows

### Workflow 1: Document Complete Phase/Milestone

**Trigger:** When a phase completes (e.g., Issue #6 complete)

**Files to Update (in order):**

1. **TECHNICAL_DEBT_TRACKER.md**
   - Update status from "IN PROGRESS" → "COMPLETE"
   - Add completion date
   - Update progress metrics
   - Add commit SHA references
   - Document final statistics

2. **.github/copilot-instructions.md**
   - Update version number (e.g., 2.1 → 2.2)
   - Update "Last Updated" timestamp
   - Add completion section in "Latest Completions"
   - Document pattern/approach used
   - Document impact and benefits
   - Add reference to TECHNICAL_DEBT_TRACKER.md

3. **CLAUDE.md**
   - Update "Latest Milestone" section
   - Mirror the completed phase documentation
   - Keep condensed version of key info
   - Link to full details in copilot-instructions

4. **README.md** (if major milestone)
   - Update project status badges or summary
   - Add to "Recent Milestones" section
   - Link to completion documentation

**Checklist:**

- [ ] All files updated in sequence
- [ ] Version numbers match across files
- [ ] Timestamps are consistent
- [ ] Links are correct (relative paths only)
- [ ] Key metrics are documented
- [ ] Commit SHAs are included
- [ ] Benefits/impact clearly described
- [ ] Files compile without errors (markdown lint)

**Example Commit Message:**

```
docs: Document Phase 1C completion - Issue #6 complete

Update TECHNICAL_DEBT_TRACKER.md with final metrics (312/312 exceptions)
Add Phase 1C section to copilot-instructions.md (v2.1 → v2.2)
Mirror Phase 1C info in CLAUDE.md
Update timestamps to March 5, 2026

All files reference commit SHA and final statistics.
```

---

### Workflow 2: Add New Feature/Capability

**Trigger:** When new major feature is added (e.g., new agent type, new route, new service)

**Files to Update:**

1. **docs/02-Architecture/Multi-Agent-Pipeline.md** (if agent-related)
   - Document agent type/capability
   - List files involved
   - Describe workflow/flow
   - Add examples

2. **docs/02-Architecture/System-Design.md** (if architectural change)
   - Update architecture diagram if needed
   - Document new component
   - Explain integration points
   - Update service inventory

3. **.github/copilot-instructions.md**
   - Update service counts (e.g., "87+ service modules" → "90+ service modules")
   - Add to relevant section
   - Update Key Files Reference table if applicable

4. **CLAUDE.md**
   - Update condensed version with key info
   - Ensure consistency with copilot-instructions.md

5. **.env.example**
   - Add any new environment variables
   - Document their purpose and default values

**Checklist:**

- [ ] Architectural docs updated
- [ ] Service/agent counts updated in headers
- [ ] Key Files Reference updated (if new files)
- [ ] Environment variables documented
- [ ] Both copilot-instructions.md and CLAUDE.md aligned
- [ ] Examples provided in relevant sections
- [ ] README.md updated if visible to end users
- [ ] Markdown validation passes

---

### Workflow 3: Fix Documentation Issues

**Trigger:** When docs become outdated, inaccurate, or confusing

**Process:**

1. **Identify scope:**
   - Single file? → Direct edit
   - Multiple files? → Check relationships first
   - Affects version/timestamp? → Coordinate across files

2. **Make edits:**
   - Update affected file(s)
   - Check all references to changed info
   - Update related docs to stay consistent

3. **Validation:**
   - Run markdown lint: `npm run format:check`
   - Check relative links are correct
   - Verify code examples still work
   - Validate timestamps are current

4. **Commit:**
   - Clear message describing what was fixed
   - Rationale for the fix
   - Files changed listed explicitly

**Example Commit Message:**

```
docs: Fix outdated service count in copilot-instructions

Update service module count from 85 to 87 (added capability_introspection_v2)
Fix broken reference to capability_registry.py
Sync CLAUDE.md to match

Rationale: count_services.py showed actual count is 87
```

---

### Workflow 4: Update Environment Variables

**Trigger:** When new env vars are added or changed

**Files to Update (in order):**

1. **.env.example**
   - Add/update variable
   - Include comment with description
   - Show example value

2. **web/public-site/.env.example**
   - Add if frontend-relevant

3. **web/oversight-hub/.env.example**
   - Add if admin UI-relevant

4. **.env.production.example**
   - Add if differs from development

5. **.github/copilot-instructions.md**
   - Update "Environment Variables" section
   - Update variable count (e.g., "52+ environment variables")
   - Document new variable in relevant subsection

6. **docs/reference/ENVIRONMENT_SETUP.md**
   - Add setup instructions for new variable

**Checklist:**

- [ ] All .env.example files updated
- [ ] Count of variables updated in docs
- [ ] Documentation includes purpose and defaults
- [ ] Example values are realistic
- [ ] Markdown validation passes
- [ ] README.md setup section still accurate

---

### Workflow 5: Automated Documentation Cleanup

**Trigger:** After completing a phase/sprint or monthly maintenance

**Purpose:** Intelligently organize stray documentation files into proper archive and docs structure

**Tool:** `npm run docs:cleanup` (or `docs:cleanup:ps`/`docs:cleanup:sh`)

**What It Does:**

1. **Creates organized structure**:
   - `archive/sessions/` → Session summaries, implementation reports
   - `archive/phase1/`, `phase2/`, `phase3/` → Phase-specific documentation
   - `archive/testing/` → Testing documentation
   - `archive/sprints/` → Sprint reports
   - `docs/reference/`, `troubleshooting/`, `decisions/`, `components/` → Categorized docs

2. **Moves files automatically** based on naming patterns:
   - `PHASE*.md` → `archive/phase*/` (by number)
   - `SESSION*.md`, `CONSOLIDATION*.md`, `*_SUMMARY.md`, `*_FIX.md` → `archive/sessions/`
   - `TESTING*.md`, `TEST_*.md`, `USER_TESTING*.md` → `archive/testing/`
   - `SPRINT*.md` → `archive/sprints/`
   - `IMPLEMENTATION*.md`, `*_STATUS.md`, `*_REPORT.md` → `archive/sessions/`
   - Other `.md` files categorized by content keywords

3. **Protects essential files** (never moved):
   - README.md, CLAUDE.md, VERSION_HISTORY.md
   - SECURITY.md, VERSIONING_GUIDE.md, DEBUG_GUIDE.md
   - DEPLOYMENT_CHECKLIST.md, LICENSE

**Usage:**

**After Phase/Sprint Completion:**

```bash
# 1. Commit all work
git add .
git commit -m "feat: complete Phase X implementation"

# 2. Run cleanup
npm run docs:cleanup

# 3. Review what was moved
git status

# 4. Commit organized structure
git add .
git commit -m "docs: archive Phase X documentation"
```

**Monthly Maintenance:**

```bash
# Run cleanup to catch stray docs
npm run docs:cleanup

# Optional: Update VERSION_HISTORY.md to reference archive
```

**Platform-Specific:**

```bash
# Windows PowerShell
npm run docs:cleanup:ps

# Unix/Linux/Mac Bash
npm run docs:cleanup:sh

# Auto-detects platform
npm run docs:cleanup
```

**Safety Features:**

- ✅ Git-aware (uses `git mv` to preserve history)
- ✅ Non-destructive (moves, never deletes)
- ✅ Idempotent (safe to run multiple times)
- ✅ Protected files never moved
- ✅ Reports what was moved

**For Details:** See [archive/README.md](../archive/README.md)

**Checklist:**

- [ ] Run `npm run docs:cleanup` after phase completion
- [ ] Review moved files with `git status`
- [ ] Update VERSION_HISTORY.md if needed
- [ ] Commit with descriptive message
- [ ] Check GitHub Action runs monthly (optional)

---

## Consistency Checks

### Before Every Commit

Run this checklist to ensure documentation consistency:

```bash
# 1. Check markdown format
npm run format:check

# 2. Count service modules (should match docs)
grep -r "^class.*Service.*:" src/cofounder_agent/services/*.py | wc -l

# 3. Count environment variables
grep "^[A-Z_]*=" .env.example | wc -l

# 4. Check timestamp consistency
grep -l "Last Updated" .github/copilot-instructions.md CLAUDE.md docs/07-Appendices/Technical-Debt-Tracker.md

# 5. Verify links exist
find docs -name "*.md" -exec grep -l "^\[.*\](.*\.md)" {} \;
```

### Cross-File Consistency

These items should match across files:

| Item                       | Files                                                         | Check                                              |
| -------------------------- | ------------------------------------------------------------- | -------------------------------------------------- |
| Last Updated date          | copilot-instructions.md, CLAUDE.md, TECHNICAL_DEBT_TRACKER.md | Should be same or copilot-instructions most recent |
| Version number             | copilot-instructions.md header                                | Only place version is updated                      |
| Service count              | copilot-instructions.md, various sections                     | Update all mentions together                       |
| Phase completion info      | copilot-instructions.md, CLAUDE.md, TECHNICAL_DEBT_TRACKER.md | Mirror to all three                                |
| Environment variable count | copilot-instructions.md, .env.example comment                 | Should match                                       |
| Route count                | copilot-instructions.md header                                | Should match actual route modules                  |

---

## Version Management

### Version Numbering Scheme

Format: `MAJOR.MINOR` (e.g., 2.2, 3.0)

- **MAJOR:** Incremented when significant architectural changes or new phases complete
  - Example: 2.0 → 3.0 when Phase 2 complete
- **MINOR:** Incremented for feature additions within same phase
  - Example: 2.1 → 2.2 when Phase 1C completes within Phase 2 timeline

**Current Version:** 2.2 (reflects Phase 2 ongoing with Phase 1C error handling complete)

### Update Pattern

1. Update version in **.github/copilot-instructions.md** header
2. Update "Last Updated" in all three reference files:
   - .github/copilot-instructions.md
   - CLAUDE.md
   - docs/07-Appendices/Technical-Debt-Tracker.md

3. Document change in "Latest Completions" section

---

## Common Update Patterns

### Pattern 1: Update Service/Route/Agent Count

**Files affected:**

- .github/copilot-instructions.md (header and inventory)
- CLAUDE.md (mirrors copilot-instructions)

**Search-replace strategy:**

```
OLD: "87+ service modules"
NEW: "90+ service modules"

OLD: "29+ route modules"
NEW: "30+ route modules"
```

**Validation:**

- Run: `find src/cofounder_agent/services -name "*.py" -type f | wc -l`
- Run: `find src/cofounder_agent/routes -name "*.py" -type f | wc -l`

---

### Pattern 2: Add New Service/Feature Section

**Template to follow:**

```markdown
## [NEW FEATURE NAME]

**Files:** List all files involved

**Purpose:** What does it do?

**Key Components:**

- Component 1: Description
- Component 2: Description

**Integration Points:**

- Where it connects in the system
- What it calls/what calls it

**Configuration:**

- Any environment variables
- Any setup needed

**Reference:** Link to detailed docs
```

---

### Pattern 3: Update Phase Completion

**Always include:**

1. Completion date
2. Files affected count
3. Metrics (exceptions fixed, tests added, etc.)
4. Validation approach
5. Impact/benefits
6. Commits involved
7. Reference to detailed tracker

**Template:**

```markdown
### [PHASE NAME] ✅ COMPLETE

**Completion Date:** [Date]  
**Impact:** [What changed?]

- **Metric 1:** X/Y (completion %)
- **Metric 2:** Details
- **Validation:** How was it verified?
- **Files affected:** Count and categories

**Benefits:**

- Benefit 1
- Benefit 2
- Benefit 3

**Reference:** Link to TECHNICAL_DEBT_TRACKER.md
```

---

## Review Checklist

Use this checklist before committing documentation changes:

### Content Accuracy

- [ ] Information is current and correct
- [ ] Code examples compile (or are clearly marked as pseudocode)
- [ ] File paths are correct (use relative paths only)
- [ ] Service/file counts match actual codebase
- [ ] Environment variable list is complete

### Format & Style

- [ ] Markdown passes lint validation
- [ ] Headings are properly nested (h1, h2, h3, etc.)
- [ ] Code blocks have language specifier (`bash,`python, etc.)
- [ ] Tables are properly formatted
- [ ] Lists use consistent formatting

### Consistency

- [ ] Related files updated together
- [ ] Version numbers match across files
- [ ] Timestamps are consistent
- [ ] Terminology is consistent
- [ ] Cross-references link to correct sections

### Completeness

- [ ] All affected files updated
- [ ] No orphaned references
- [ ] Examples are documented
- [ ] Integration points are clear
- [ ] Configuration requirements listed

### Links & References

- [ ] All links are relative paths (not absolute)
- [ ] Links point to existing files
- [ ] GitHub issue/commit links are correct
- [ ] References in TECHNICAL_DEBT_TRACKER match commits

---

## Emergency Procedures

### If Documentation Gets Out of Sync

**Quick Fix Process:**

1. **Identify the source of truth:**
   - TECHNICAL_DEBT_TRACKER.md for issues/completions
   - .github/copilot-instructions.md for architecture/current state
   - Code itself for actual counts/features

2. **Update in priority order:**
   1. TECHNICAL_DEBT_TRACKER.md (the log)
   2. .github/copilot-instructions.md (primary reference)
   3. CLAUDE.md (mirrors copilot-instructions)
   4. README.md (if user-facing)

3. **Run validation:**

   ```bash
   npm run format:check  # Validate markdown
   git diff              # Review all changes
   ```

4. **Commit with "docs: sync" message**

### If You Need Help

- **Architecture questions:** Check docs/02-Architecture/System-Design.md
- **Deployment questions:** Check docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md
- **Specific issue status:** Check docs/07-Appendices/Technical-Debt-Tracker.md
- **Quick reference:** Check CLAUDE.md for condensed version

---

## Tools & Commands

### Documentation Validation

```bash
# Check markdown format
npm run format:check

# Fix markdown format issues
npm run format

# Count files in a directory
find src/cofounder_agent/services -name "*.py" -type f | wc -l

# Search for outdated info
grep -r "87 service" .github/copilot-instructions.md  # Example

# Check for broken links in markdown
grep -r "\[.*\](.*/.*\.md)" docs/ | grep -v "^Binary"
```

### Before Documentation Commits

```bash
# 1. Validate markdown
npm run format:check

# 2. Verify counts match
echo "Services:" && find src/cofounder_agent/services -name "*.py" | wc -l
echo "Routes:" && find src/cofounder_agent/routes -name "*.py" | wc -l

# 3. Check for consistency
echo "Check timestamps in docs..."
grep "Last Updated" .github/copilot-instructions.md CLAUDE.md

# 4. View changes
git diff --cached

# 5. Commit
git commit -m "docs: [describe changes]"
```

---

## FAQ

**Q: Should I update all three reference files (copilot-instructions, CLAUDE, README) at the same time?**  
A: Yes, if the change affects documented info. Keep copilot-instructions as the source of truth, mirror key info to CLAUDE, and update README only if user-facing.

**Q: How often should I update timestamps?**  
A: Update when making substantive content changes. Don't update just for typo fixes.

**Q: What should I do if I add a new service or route?**  
A: 1) Update count in copilot-instructions.md header, 2) Add to Key Files Reference table, 3) Sync CLAUDE.md, 4) Update .env.example if needed, 5) Test that code section still compiles.

**Q: Can I add new documentation files?**  
A: Yes, but link them from the main reference file. Update docs/README.md to list new files. Notify the team in commit message.

**Q: How do I handle documentation for deprecated features?**  
A: Mark as "DEPRECATED - see [new feature]" in all references. Keep in docs for history. Add deprecation date. Link to replacement.

---

## References

- **Main Reference:** [.github/copilot-instructions.md](.github/copilot-instructions.md)
- **Condensed Guide:** [CLAUDE.md](../CLAUDE.md)
- **Issue Tracking:** [docs/07-Appendices/Technical-Debt-Tracker.md](07-Appendices/Technical-Debt-Tracker.md)
- **Architecture Guide:** [docs/02-Architecture/System-Design.md](02-Architecture/System-Design.md)
- **Development Workflow:** [docs/04-Development/Development-Workflow.md](04-Development/Development-Workflow.md)
