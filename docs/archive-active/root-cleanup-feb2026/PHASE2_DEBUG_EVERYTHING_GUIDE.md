# Phase 2: Debug Everything Mode

## What Changed

Phase 2 has been expanded from **"test-focused debugging"** to **"comprehensive system debugging"**. It now analyzes and attempts to fix:

- ✅ **Failed tests** (root cause analysis + fixes)
- ✅ **Type errors** (pyright, mypy issues preventing correct behavior)
- ✅ **Code quality** (pylint, flake8 violations)
- ✅ **Security issues** (bandit vulnerabilities)
- ✅ **Logic bugs** (inferred from test failures and code patterns)

## How It Works

### New Diagnostic Function: `get_detailed_issue_report()`

Instead of just running tests, Phase 2 now:

1. **Runs comprehensive diagnostics** collecting:
   - Test failures with error details
   - Type checking issues (pyright) with file/line references
   - Code quality issues (pylint) with specific violations
   - Security issues (bandit) with vulnerability descriptions

2. **Generates formatted report** showing all issues by priority:

   ```
   FAILED TESTS:
   - test_name_1
   - test_name_2
   
   CODE QUALITY ISSUES:
   pyright: 636 issues
     - src/file.py: Type error description
   pylint: 45 issues
     - src/other.py:123: Violation description
   ```

3. **Passes complete context to debugger** instead of just test failure details

### Enhanced Reasoning Prompt

The debugger (DeepSeek R1) now gets instructions to:

1. **Understand root causes** - Why do issues exist?
2. **Prioritize fixes** - Which issues block others?
3. **Consider dependencies** - Fix order matters
4. **Provide context** - Explain root_cause for each fix
5. **Think systematically** - Don't just surface-level fixes

**Priority System:**

```
🔴 CRITICAL: Test failures + security issues
🟠 HIGH: Type errors (prevent correct execution)
🟡 MEDIUM: Code quality (maintainability)
🟢 LOW: Style (only if others are fixed)
```

### Example Output Format

```
PHASE 2: DEBUG EVERYTHING mode activated
   Analyzing: Tests, Type Errors, Code Quality, Security

Running comprehensive diagnostic scan...

FAILED TESTS:
==============================================================
  • tests/e2e/test_tone_filtering
  
Error Details (first 1000 chars):
AssertionError: assert 0.0 >= 0.5

CODE QUALITY ISSUES:
==============================================================

pyright: 636 issues
  - src/cofounder_agent/services/model_router.py: 'ModelResponse' has no attribute 'usage'
  - src/cofounder_agent/routes/task_routes.py: Function 'execute_task' is missing return type

pylint: 45 issues
  - src/cofounder_agent/services/task_executor.py:123: Line too long (105 > 100)

bandit: 3 issues
  - src/cofounder_agent/utils.py:456: Use of hardcoded SQL strings [HIGH]

Debugger response preview:
   {
     "done": false,
     "steps": [
       {
         "id": 1,
         "priority": "critical",
         "description": "Fix test_tone_filtering by correcting MockRAGService tone matching logic",
         "root_cause": "tone_match returns 0.0 for mismatched tones, but test expects >= 0.5"
       }
     ]
   }

Plan contains 6 debugging steps
```

## Execution Flow

```
Phase 2 Loop:
├─ Get detailed issue report (tests + linters)
├─ Check if issues exist
│  └─ If none: Exit loop (all systems operational)
├─ Check for stuck loops (same issue 5+ iterations)
│  └─ If stuck: Log and exit
├─ Send to DeepSeek R1 reasoner with complete context
├─ Receive prioritized debugging plan
├─ Execute each step with Qwen3 Coder
│  ├─ Generate patches for fixes
│  └─ Apply patches with `git apply`
└─ Repeat until all issues resolved or limit reached
```

## Key Improvements Over Previous Version

### Before

```
❌ Only analyzed test failures
❌ Ignored type errors and code quality issues
❌ Focused on surface-level test fixes
```

### After

```
✅ Analyzes tests + types + code quality + security
✅ Considers all issues together
✅ Prioritizes by impact (critical → high → medium → low)
✅ Explains root causes for each fix
✅ Systematically addresses blockers first
```

## Configuration

Both Phase 2 and Phase 1 are configurable:

```bash
# Skip Phase 2 entirely
SKIP_PHASE_2=true python agent_loop.py

# Run Phase 2 with specific iteration limit
PHASE2_MAX_ITERATIONS=20 python agent_loop.py

# Combine with Phase 1 settings
SKIP_PHASE_1=false SKIP_PHASE_2=false python agent_loop.py
```

## Model Selection

Phase 2 uses two optimized models for your system:

- **DeepSeek R1 70B** (reasoning) - Analyzes all issues comprehensively
- **Qwen3 Coder 32B** (generation) - Generates precise code fixes

Both are quantized (4K) for efficient local execution.

## What Gets Debugged

### ✅ Supported Issues

- Test assertion failures
- Runtime exceptions / missing imports
- Type checking violations
- Linting violations (style, naming, complexity)
- Security vulnerabilities
- Logic errors (inferred from test failures)

### ⚠️ Partial Support

- Performance issues (detected, but optimization requires careful analysis)
- Architectural issues (detected, but refactoring is complex)
- API design issues (detected, but breaking changes are risky)

### ❌ Out of Scope

- Config/environment issues (manual setup required)
- External service failures (requires actual services)
- Infrastructure issues (requires deployment changes)

## Monitoring Progress

### Console Output

```
PHASE 2 ITERATION 3/500
🔍 Running comprehensive diagnostic scan...
🧠 PHASE 2: DEBUG EVERYTHING mode activated
📝 Plan contains 5 debugging steps
✏️ Applied fixes from step 1/5: Fix type error in model_router.py
```

### Unfixable Issues Log

Issues that can't be fixed automatically are logged to `unfixable_issues.json`:

- Stuck test failures (5+ iterations)
- Non-fixable type errors
- Security issues that need manual review

## Next Steps

After Phase 2 completes:

1. **Review `unfixable_issues.json`** for any issues that couldn't be auto-fixed
2. **Check git diff** to see all changes made by Phase 2
3. **Run tests manually** to verify fixes: `npm run test:python`
4. **Commit improvements**: `git add . && git commit -m "Phase 2: Automated debugging fixes"`
5. **Re-run agent loop** if more issues remain

## Troubleshooting

**Q: Why is Phase 2 slower than Phase 1?**
A: Phase 2 uses reasoning models (DeepSeek R1 70B) which are slower but much smarter. Typical iteration: 5-10 minutes.

**Q: What if the debugger suggests bad fixes?**
A: Patches go through `git apply --check` before being applied, catching most invalid changes.

**Q: Can I debug specific issues only?**
A: You can skip Phase 1 to focus on Phase 2: `SKIP_PHASE_1=true python agent_loop.py`

**Q: How do I stop the loop?**
A: Press Ctrl+C anytime. Phase 2 exits gracefully and logs all progress.
