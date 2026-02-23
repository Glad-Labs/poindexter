# Quick Reference: Autonomous Improvement System

## The Two Agents

### agent_loop.py - Code Improvement

```bash
python agent_loop.py                    # Fix everything
SKIP_PHASE_1=true python agent_loop.py  # Only debug (faster reasoning)
SKIP_PHASE_2=true python agent_loop.py  # Only auto-fix (fast)
MAX_ITERATIONS=5 python agent_loop.py   # Limited iterations (testing)
```

### doc_agent.py - Documentation Improvement

```bash
python doc_agent.py                               # Improve all docs
FOCUS_FILE="README.md" python doc_agent.py        # Focus on one file
SKIP_GENERATION=true python doc_agent.py          # Improve only, don't generate
MAX_ITERATIONS=3 python doc_agent.py              # Test mode
```

## Combined Workflows

### Standard Run

```bash
python agent_loop.py        # ~30 min
python doc_agent.py         # ~30 min
git add . && git commit -m "Automated improvements"
```

### Fast Run (Skip Non-Critical)

```bash
SKIP_PHASE_1=true python agent_loop.py            # ~15 min (just reasoning)
SKIP_GENERATION=true python doc_agent.py          # ~15 min (just improvement)
```

### Test Run

```bash
MAX_ITERATIONS=2 SKIP_GENERATION=true python agent_loop.py     # ~5 min
MAX_ITERATIONS=1 SKIP_GENERATION=true python doc_agent.py      # ~5 min
```

## What Gets Fixed

### agent_loop.py Fixes

| Phase | Tools | Time |
|-------|-------|------|
| 1 | black, isort, autoflake, prettier, eslint, stylelint | 2-5 min |
| 2 | pyright, mypy, pylint, flake8, bandit + test failures | 20-40 min |

### doc_agent.py Improves

- Quality, clarity, completeness
- Missing sections, examples
- Structure, formatting
- README generation

## Output Files

| File | Agent | What It Contains |
|------|-------|-----------------|
| `unfixable_issues.json` | agent_loop | Issues needing manual attention |
| `<filename>.md` | doc_agent | Improved documentation |
| Git changes | both | All modifications tracked |

## Common Commands

```bash
# Just check what would improve
FOCUS_FILE="README.md" python doc_agent.py

# Fix code only
SKIP_PHASE_2=true python agent_loop.py

# Debug only
SKIP_PHASE_1=true python agent_loop.py

# Generate missing docs
python doc_agent.py

# No generation (only improve existing)
SKIP_GENERATION=true python doc_agent.py

# Everything, quick test
MAX_ITERATIONS=1 SKIP_GENERATION=true python agent_loop.py

# Everything, unlimited
python agent_loop.py
python doc_agent.py
```

## Check Requirements

```bash
# Ollama running?
curl http://localhost:11434/api/tags

# Poetry installed?
poetry --version

# Python 3.12+?
python --version
```

## Review Results

```bash
# See code changes
git diff

# See doc improvements
git diff docs/

# Check what couldn't be fixed
cat unfixable_issues.json | python -m json.tool

# See all changes
git status
```

## Commit Results

```bash
# If happy with changes
git add . && git commit -m "Automated improvements: code + docs"

# If not happy, revert
git checkout -- .

# Check specifics first
git diff README.md  # See what changed
```

## Timing Guide

| Operation | Time |
|-----------|------|
| Phase 1 (auto-fix) | 2-5 min |
| Phase 2 iteration (debug) | 7-15 min |
| Doc analysis iteration | 5-10 min |
| Full agent_loop | 15-45 min |
| Full doc_agent | 15-45 min |
| Both parallel | ~45 min |
| Both sequential | ~60-90 min |

## Troubleshooting

```bash
# Ollama not running
ollama serve

# See logs
cat unfixable_issues.json

# Kill stuck process
Ctrl+C

# Revert changes
git checkout -- .

# Test specific file
FOCUS_FILE="file.md" python doc_agent.py
```

## File Locations

- `agent_loop.py` - Code improvement agent
- `doc_agent.py` - Documentation improvement agent
- `unfixable_issues.json` - Issues log (generated)
- `AUTONOMOUS_SYSTEM_OVERVIEW.md` - Full system guide
- `DOC_AGENT_GUIDE.md` - Doc agent detailed guide
- `PHASE2_DEBUG_EVERYTHING_GUIDE.md` - Phase 2 details
- `UNFIXABLE_ISSUES_LOG_GUIDE.md` - Issues logging guide

## Full Feature List

**agent_loop.py:**

- ✅ Auto-fix formatting (Phase 1)
- ✅ Debug tests (Phase 2)
- ✅ Fix type errors (Phase 2)
- ✅ Fix code quality (Phase 2)
- ✅ Security scanning (Phase 2)
- ✅ Issue logging

**doc_agent.py:**

- ✅ Analyze doc quality
- ✅ Generate improvements
- ✅ Create missing READMEs
- ✅ Score clarity/completeness

## One-Liner Examples

```bash
# Everything
python agent_loop.py && python doc_agent.py && git add . && git commit -m "Auto-improve"

# Just docs
FOCUS_FILE="docs/" python doc_agent.py

# Just tests
SKIP_PHASE_1=true PHASE2_MAX_ITERATIONS=3 python agent_loop.py

# Generate docs only
python doc_agent.py

# Fast test
MAX_ITERATIONS=1 SKIP_GENERATION=true python doc_agent.py && \
SKIP_PHASE_2=true python agent_loop.py
```

## Remember

- Always check `git diff` before committing
- Ctrl+C to stop either agent safely
- Revert with `git checkout -- .` if needed
- Run sequentially for safer builds
- Run parallel for faster improvements
- Leave Ollama running in background terminal
