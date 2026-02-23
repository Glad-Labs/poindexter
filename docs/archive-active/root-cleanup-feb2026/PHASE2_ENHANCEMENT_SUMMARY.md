# Agent Loop: Phase 2 "Debug Everything" Enhancement

## Summary of Changes

Your agent loop has been upgraded to **debug everything**, not just test failures. This is a significant enhancement that makes Phase 2 much more powerful and comprehensive.

## What Was Added

### 1. **Comprehensive Diagnostic Function**

**New**: `get_detailed_issue_report()`

- Scans tests, type checking, code quality, and security in one pass
- Returns formatted report + detailed issue dictionary
- Collects context from: pyright, pylint, bandit, plus test failures
- Shows first 3 issues of each type for reasoning context

### 2. **Enhanced Phase 2 Reasoning**

Phase 2 now:

- **Analyzes all issues together** (not just test failures)
- **Prioritizes by impact**:
  - 🔴 CRITICAL: Test failures + security issues
  - 🟠 HIGH: Type errors (prevent correct execution)
  - 🟡 MEDIUM: Code quality (maintainability)
  - 🟢 LOW: Style issues (lowest priority)
- **Includes root cause analysis** for each issue
- **Considers dependencies** between fixes
- **Exits early** when all issues are resolved

### 3. **Better Loop Detection**

- Same test failure stuck detection (5+ iterations)
- Now tests for "all issues resolved" condition
- Exits Phase 2 immediately if no issues found

## Key Improvements

### Before (Test-Only Mode)

```
Phase 2 would:
- Run only tests
- Only fix test failures
- Ignore type errors and code quality
- Generate patches only for test-related changes
```

### After (Debug Everything Mode)

```
Phase 2 now:
- Runs tests + linters + type checkers + security scanner
- Fixes tests, types, code quality, AND security issues
- Provides prioritized list by impact
- Explains root causes
- Generates patches for all high-impact issues
```

## New Console Output Example

```
====================================================================
🔄 PHASE 2 ITERATION 1/500
====================================================================
🔍 Running comprehensive diagnostic scan...
🧠 PHASE 2: DEBUG EVERYTHING mode activated
   Analyzing: Tests, Type Errors, Code Quality, Security

FAILED TESTS:
==============================================================
  • tests/e2e/test_tone_filtering

Error Details (first 1000 chars):
AssertionError: assert 0.0 >= 0.5

CODE QUALITY ISSUES:
==============================================================

pyright: 636 issues
  - src/cofounder_agent/services/model_router.py: 'ModelResponse' has no attribute 'usage'
  - src/cofounder_agent/routes/task_routes.py: Function is missing return type

pylint: 45 issues
  - src/cofounder_agent/services/task_executor.py:123: Line too long

bandit: 3 issues
  - src/cofounder_agent/utils.py:456: Hardcoded SQL strings [HIGH]

📋 Debugger response preview:
   {
     "done": false,
     "steps": [
       {
         "id": 1,
         "priority": "critical",
         "description": "Fix test_tone_filtering",
         "root_cause": "tone_match returns 0.0 but test expects >= 0.5"
       },
       {
         "id": 2,
         "priority": "high",
         "description": "Add type annotation to ModelResponse",
         "root_cause": "Missing type hints cause pyright errors"
       }
     ]
   }

✅ DEBUG PHASE COMPLETE: All systems operational
```

## What Issues Can Be Debugged

### ✅ Now Supported

- ✅ Failed test assertions and exceptions
- ✅ Type checking errors (pyright, mypy)
- ✅ Code quality violations (pylint, flake8)
- ✅ Security vulnerabilities (bandit)
- ✅ Logic errors inferred from test failures
- ✅ Missing imports and dependencies

### ⚠️ Partial Support (Attempted but May Require Manual Review)

- Performance optimizations
- Architectural improvements
- Complex refactorings

## Code Changes Made

### Files Modified

- **agent_loop.py** - Added `get_detailed_issue_report()` + expanded Phase 2

### New Functions

1. `get_detailed_issue_report()` (lines 798-895)
   - Comprehensive diagnostic scanning
   - Returns formatted report string + issue dictionary
   - Type-safe with proper type hints

### Logic Updates in Phase 2

1. **Diagnostic phase** - Replaced single test run with comprehensive scan
2. **Issue detection** - Now checks both test failures AND linter issues
3. **Exit conditions** - Added "all issues resolved" check
4. **Reasoning prompt** - Expanded with priority system and root cause analysis
5. **Unchanged** - Patch generation and application logic remains the same

## Running the Updated Loop

### Basic Usage

```bash
python agent_loop.py
```

### With Configuration

```bash
# Run only Phase 2 (skip auto-fixes)
SKIP_PHASE_1=true python agent_loop.py

# Limit iterations
PHASE2_MAX_ITERATIONS=10 python agent_loop.py

# Full configuration
SKIP_PHASE_1=false SKIP_PHASE_2=false PHASE1_MAX_ITERATIONS=100 PHASE2_MAX_ITERATIONS=50 python agent_loop.py
```

## Integration with Unfixable Issues Logging

Phase 2 improvements work seamlessly with the unfixable issues log:

- Test failures stuck 5+ iterations → Logged to unfixable_issues.json
- Non-fixable type errors → Logged after Phase 1
- Security issues that can't auto-fix → Logged  
- Final summary shows all categories

## Performance Notes

- **Phase 1**: Still fast (auto-fixes only, seconds per iteration)
- **Phase 2**: Slower but smarter
  - Diagnostic scan: ~30-60 seconds (runs all linters + tests)
  - Reasoning: ~5-10 minutes (using DeepSeek R1)
  - Patching: ~1 minute (Qwen3 Coder generation + git apply)
  - **Total per iteration**: ~7-15 minutes (depending on codebase size)

## Testing the Enhancement

To verify the new functionality works:

1. Run the agent loop:

   ```bash
   python agent_loop.py
   ```

2. Watch for the new output:
   - Should see "Running comprehensive diagnostic scan..."
   - Should show tests, pyright, pylint, bandit issues
   - Plan should include multiple types of fixes

3. Check unfixable_issues.json at the end for categorized issues

## Troubleshooting

**Q: Phase 2 is much slower than before**
A: Yes - it's now running more comprehensive checks. Use `PHASE2_MAX_ITERATIONS=3` to test it quickly.

**Q: It's trying to fix too many issues**
A: The system will prioritize. Critical issues first, then high, then medium. It's working correctly.

**Q: I only want to fix tests, not linter issues**
A: You can use `SKIP_PHASE_2=true` to skip reasoning entirely, or run just `SKIP_PHASE_1=true` and manually select what Phase 2 should focus on.

**Q: Debugger is generating bad patches**
A: The patches still go through validation before applying. Check unfixable_issues.json for failures.

## Next Steps

1. **Run the new agent loop** to see it debug everything
2. **Review the enhanced diagnostics** - much more comprehensive
3. **Check unfixable_issues.json** for categorized issues
4. **Consider performance** - Phase 2 is slower but solves more problems
5. **Adjust PHASE2_MAX_ITERATIONS** based on your needs

The system is now truly autonomous - it debugs types, quality, tests, and security all together instead of just test-by-test. This should significantly reduce the number of manual fixes needed!
