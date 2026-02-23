# Documentation Improvement Agent - User Guide

## Overview

The **Documentation Improvement Agent** (`doc_agent.py`) automatically analyzes your documentation for quality issues and makes improvements using AI. It can:

- ✅ **Analyze** documentation for quality, clarity, and completeness
- ✅ **Improve** existing docs by fixing issues and adding examples
- ✅ **Generate** missing README files in key directories
- ✅ **Track** improvements and report progress

## Quick Start

### Basic Usage

```bash
# Analyze and improve all documentation
python doc_agent.py

# Focus on a specific file
FOCUS_FILE="README.md" python doc_agent.py

# Only improve, don't generate new docs
SKIP_GENERATION=true python doc_agent.py

# Limit to 5 iterations (for testing)
MAX_ITERATIONS=5 python doc_agent.py

# Combine options
SKIP_GENERATION=true MAX_ITERATIONS=3 FOCUS_FILE="docs/" python doc_agent.py
```

## How It Works

### Phase 1: Analysis

For each documentation file:

1. **Reads** the markdown content
2. **Analyzes** using DeepSeek R1 70B (reasoner model)
3. **Scores** on: quality (0-100), clarity (0-100), completeness (0-100)
4. **Identifies** specific issues to address

### Phase 2: Improvement

For files scoring below 85:

1. **Generates** improved version using Qwen3 Coder 32B
2. **Addresses** top 3 high-priority issues
3. **Maintains** original style and good content
4. **Saves** improved version to the same file

### Phase 3: Generation

If `SKIP_GENERATION=false`:

1. **Scans** for missing README files
2. **Generates** documentation for undocumented directories
3. **Creates** helpful README files with structure/usage info

## Output Example

```
================================================================================
📚 DOCUMENTATION IMPROVEMENT AGENT
================================================================================
Start time: 2026-02-21 20:31:15

✅ Ollama available

📄 Found 24 documentation files

🔄 Iteration 1
================================================================================
🔍 Analyzing: README.md
   Quality: 72/100, Issues: 4
✏️  Generating improvements for README.md
   Quality: 72/100
   Issues: 4
✅ Saved: README.md
   Improved: 1 files

🔍 Analyzing: docs/API.md
   Quality: 85/100, Issues: 1
   ✅ Already excellent

... (more files) ...

📊 DOCUMENTATION IMPROVEMENT COMPLETE
================================================================================
Files processed: 24
Files improved: 8
Issues addressed: 22
New docs generated: 2
End time: 2026-02-21 20:35:42

💡 Next steps:
   1. Review improved documentation in git diff
   2. Commit changes: git add docs/ && git commit -m 'Improve documentation'
   3. Run again if more improvements needed
```

## Configuration Options

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_ITERATIONS` | 0 (infinite) | Number of improvement iterations |
| `SKIP_GENERATION` | false | Skip generating missing READMEs |
| `FOCUS_FILE` | "" | Focus on files matching this pattern |
| `ITERATION_DELAY` | 2 | Seconds between iterations |
| `ANALYZER_TIMEOUT` | 600 | Max seconds for analysis (10 min) |
| `VERBOSE` | false | Show verbose output |

### Examples

```bash
# Test mode - just 2 iterations, no generation
MAX_ITERATIONS=2 SKIP_GENERATION=true python doc_agent.py

# Focus on a specific directory
FOCUS_FILE="docs/api" python doc_agent.py

# Only generate missing docs, don't improve existing
SKIP_GENERATION=false python doc_agent.py

# Faster iterations (skip delay)
ITERATION_DELAY=0 python doc_agent.py
```

## What Gets Evaluated

### Analysis Scores

**Quality Score (0-100)**

- Measures overall documentation quality
- Considers completeness, clarity, accuracy
- Target: 85+

**Clarity Score (0-100)**

- Measures how easy to understand
- Considers language, structure, examples
- Target: 80+

**Completeness Score (0-100)**

- Measures coverage of topics
- Considers missing sections, examples, details
- Target: 85+

### Issue Categories

Issues are categorized and prioritized:

**🔴 HIGH Priority**

- Missing sections
- Incomplete examples
- Unclear explanations
- Poor structure

**🟡 MEDIUM Priority**

- Formatting inconsistencies
- Outdated information
- Missing links/references
- Vague descriptions

**🟢 LOW Priority**

- Minor style improvements
- Grammar/spelling
- Cosmetic formatting

## How It Improves Documentation

### Types of Improvements

1. **Add Missing Sections**
   - Before: Incomplete API documentation
   - After: Added Parameters, Returns, Examples sections

2. **Add Examples**
   - Before: Theoretical description only
   - After: Code examples with expected output

3. **Improve Clarity**
   - Before: Vague technical prose
   - After: Clear explanations with context

4. **Fix Structure**
   - Before: Poor organization, hard to scan
   - After: Logical sections with hierarchy

5. **Update Content**
   - Before: Outdated or incorrect information
   - After: Current, accurate content

### Example Improvement

**Before:**

```markdown
# API Guide

The API provides endpoints for various operations. Use the base URL.
Endpoints are authenticated. Parameters are documented below.

### Endpoints
- /api/tasks
- /api/users
```

**After:**

```markdown
# API Guide

The Glad Labs API provides REST endpoints for task management, user operations, and system administration. All endpoints require authentication via JWT tokens.

## Getting Started

### Base URL
```
<https://api.gladlabs.com/v1>

```

### Authentication
All requests require a Bearer token in the Authorization header:
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" https://api.gladlabs.com/v1/tasks
```

## Endpoints

### Tasks

- **GET /api/tasks** - List all tasks
- **POST /api/tasks** - Create a new task
- **GET /api/tasks/{id}** - Get task details

### Users

- **GET /api/users** - List users
- **POST /api/users** - Create user
...

```

## Generated Documentation

When generation is enabled, the agent creates README files for:
- `src/` - Backend code structure
- `web/` - Frontend applications
- `tests/` - Testing setup and guidelines
- Key subdirectories

Example generated README:
```markdown
# src/cofounder_agent

Backend FastAPI application for the Glad Labs AI Co-Founder system.

## Structure
- routes/ - API endpoints
- services/ - Business logic
- agents/ - AI agents
- models/ - Data schemas

## Running
poetry run uvicorn main:app --reload

## Testing
poetry run pytest tests/
```

## Integration with agent_loop.py

The `doc_agent.py` and `agent_loop.py` are complementary:

- **agent_loop.py** - Fixes code quality, tests, and types
- **doc_agent.py** - Improves documentation quality

Run them together for comprehensive improvement:

```bash
# Fix code
python agent_loop.py

# Improve docs
python doc_agent.py

# Commit everything
git add . && git commit -m "Improve code and documentation"
```

## Workflow

### Typical Session

```bash
# 1. Check current documentation quality
FOCUS_FILE="README.md" python doc_agent.py

# 2. Let it improve documentation
# (watch console output, Ctrl+C to stop)

# 3. Review improvements
git diff README.md

# 4. Commit if happy
git add README.md && git commit -m "Improve README"

# 5. Improve other docs
FOCUS_FILE="docs/" python doc_agent.py

# 6. Generate missing docs
SKIP_GENERATION=false python doc_agent.py
```

### Continuous Improvement

```bash
# Run alongside agent_loop.py for comprehensive improvements
# Terminal 1:
python agent_loop.py

# Terminal 2 (after agent_loop completes):
python doc_agent.py

# Commit all improvements
git add . && git commit -m "Automated improvements: code + docs"
```

## Performance Notes

- **Analysis**: ~30-60 seconds per file (DeepSeek R1)
- **Improvement**: ~2-5 minutes per file (Qwen3 Coder)
- **Generation**: ~5-10 minutes per new doc
- **Typical run**: 15-45 minutes for full repo

For faster testing:

```bash
# Test with just 2 iterations
MAX_ITERATIONS=2 SKIP_GENERATION=true python doc_agent.py
```

## Troubleshooting

### Ollama Not Running

```
Error: ❌ Ollama is not running. Start it with: ollama serve
Solution: Open another terminal and run: ollama serve
```

### Takes Too Long

```bash
# Focus on specific files
FOCUS_FILE="README.md" python doc_agent.py

# Limit iterations
MAX_ITERATIONS=3 python doc_agent.py

# Skip generation
SKIP_GENERATION=true python doc_agent.py
```

### Generated Docs Look Bad

```bash
# They improve iteratively - run again
python doc_agent.py

# Or manually review and edit
git diff docs/
```

### Want to Revert Changes

```bash
# See what changed
git diff

# Revert if needed
git checkout -- docs/
```

## Advanced Usage

### Focus on Specific Directory

```bash
FOCUS_FILE="docs/api" python doc_agent.py
```

### Just Generate Missing Docs

```bash
SKIP_GENERATION=false python doc_agent.py
# (will skip improving existing docs)
```

### Aggressive Improvement (More Iterations)

```bash
MAX_ITERATIONS=10 python doc_agent.py
```

### Batch Improvements

```bash
# Improve all docs
python doc_agent.py

# Improve specific sections
FOCUS_FILE="src/" python doc_agent.py

# Generate any missing docs
python doc_agent.py
```

## What It Can't Do

- ❌ Major structural changes to complex docs
- ❌ Extract technical details from code automatically
- ❌ Handle non-markdown documentation
- ❌ Update embedded links (must be manual)
- ❌ Create entirely new documentation from scratch (can improve existing)

## Tips & Tricks

1. **Review Before Committing** - Always check git diff before committing generated docs
2. **Iterative Improvement** - Run multiple times; quality improves with each iteration
3. **Focus on Quality Docs First** - Low-quality docs (< 60) improve the most
4. **Combine with Code Changes** - Run after code improvements for better context
5. **Use for Consistency** - Helps standardize documentation across the repo

## Next Steps

```bash
# 1. Run the agent
python doc_agent.py

# 2. Review what it changed
git diff

# 3. Commit improvements
git add . && git commit -m "Improve documentation"

# 4. Push changes
git push

# 5. Share improved docs with team!
```
