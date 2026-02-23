# Unfixable Issues Log Guide

The agent loop now automatically tracks and logs issues that cannot be automatically fixed, saving them to **`unfixable_issues.json`** for manual review.

## Overview

The system categorizes unfixable issues into three types:

### 1. **Stuck Auto-Fixable Issues**

- Auto-fixable tools that couldn't resolve their issues after maximum iterations
- **Tools affected:** black, isort, autoflake, prettier, eslint, stylelint
- **When logged:** Phase 1 exits due to stuck loop detection (same issue count for 3+ iterations)
- **Action required:** Manually fix or suppress the issues

### 2. **Non-Auto-Fixable Tool Issues**

- Issues from tools that require reasoning or manual interpretation
- **Tools affected:** pylint, flake8, mypy, pyright, bandit, vulture, radon, pydocstyle, darglint
- **When logged:** After Phase 1 ends, final diagnostic scan captures all remaining issues
- **Action required:** Requires Phase 2 (AI reasoning) or manual fixes

### 3. **Phase 2 Test Failures**

- Tests that failed and couldn't be fixed after 5+ iterations
- **When logged:** Phase 2 exits due to stuck test failure detection
- **Action required:** Debug test logic, fixtures, or implementation

## JSON Log Format

```json
{
  "timestamp": "2026-02-21T10:30:45.123456",
  "stuck_auto_fixes": {
    "autoflake": 1,
    "isort": 2
  },
  "non_fixable_tools": {
    "pyright": 636,
    "pylint": 45,
    "bandit": 3
  },
  "phase2_failures": [
    {
      "test": "tests/e2e/test_phase_3_6_end_to_end.py::TestRAGRetrievalSystem::test_tone_filtering",
      "error": "AssertionError: assert 0.0 >= 0.5",
      "stuck_at_iteration": 5
    }
  ],
  "summary": {
    "stuck_autofixable_issues": 3,
    "nonfixable_tool_issues": 684,
    "phase2_test_failures": 1,
    "total_issues": 688,
    "actions_required": [
      "Fix or suppress auto-fixable issues that got stuck (check logs)",
      "Address 684 issues from: pyright, pylint, bandit",
      "Debug/fix 1 test failures (see phase2_failures)"
    ]
  }
}
```

## Workflow

### Running the Agent Loop

```bash
npm run dev:cofounder    # or
python agent_loop.py
```

### After Completion

1. **Check console output** for the "UNFIXABLE ISSUES SUMMARY" section
2. **Review `unfixable_issues.json`** for detailed categorization
3. **Address issues by category:**

#### For Stuck Auto-Fixable Issues

```bash
# Option 1: Fix the underlying issue
# Review logs, fix in editor, re-run agent loop

# Option 2: Suppress tool (not recommended for production)
poetry run black --version # verify black is working
```

#### For Non-Auto-Fixable Tool Issues

```bash
# Run specific tool to see details
poetry run pylint src/ 
poetry run pyright src/
poetry run bandit -r src/

# Fix critical issues first (security, type errors)
# Less critical issues can be scheduled
```

#### For Phase 2 Test Failures

```bash
# Run the specific failing test
pytest tests/e2e/test_phase_3_6_end_to_end.py::TestRAGRetrievalSystem::test_tone_filtering -xvs

# Debug and fix the test
# Options:
# - Fix test assertions/expectations
# - Fix mock/fixture setup
# - Fix implementation code
```

## Example Console Output

When the agent loop completes, you'll see:

```
================================================================================
📋 UNFIXABLE ISSUES SUMMARY
================================================================================
⚠️  688 issues require manual attention:
   • Auto-fixable issues stuck: 3
   • Non-fixable tool issues: 684
   • Phase 2 test failures: 1

📝 Detailed log: unfixable_issues.json

💡 Recommended actions:
   • Fix or suppress auto-fixable issues that got stuck (check logs)
   • Address 684 issues from: pyright, pylint, bandit
   • Debug/fix 1 test failures (see phase2_failures)
================================================================================
```

## Prioritization Strategy

When addressing unfixable issues:

1. **Phase 2 Test Failures** (highest priority)
   - These block other fixes
   - Usually fixes other issues as a side effect

2. **Stuck Auto-Fixable Issues** (medium priority)
   - Should be resolvable
   - May need special handling or suppression

3. **Non-Auto-Fixable Issues** (lowest priority)
   - Likely requires AI reasoning or major refactoring
   - Can be triaged for importance (security > correctness > style)

## Integration with CI/CD

The unfixable issues log can be:

- Committed to version control for history
- Parsed by CI/CD to fail builds if threshold exceeded
- Used to track progress over time

```bash
# Track progress over multiple runs
git log --follow unfixable_issues.json
jq '.summary.total_issues' unfixable_issues.json
```

## Troubleshooting

**Issue:** Log file not created

- Check file permissions in workspace root
- Ensure agent loop runs to completion (check for Ctrl+C)

**Issue:** All issues logged but none fixed

- Check in Phase 1 auto-fix succeeded (look for "✏️ Applied fixes" messages)
- Check Phase 2 is enabled (SKIP_PHASE_2=false)
- Review error logs for patches that failed to apply

**Issue:** Test failures keep getting logged

- May indicate real bug in implementation (not test setup issue)
- Consider disabling that test: `pytest -k 'not test_name'`
- Use `CONTINUE_ON_TEST_FAILURE=true` to keep Phase 2 running on failures

## Next Steps

After addressing unfixable issues:

1. Re-run agent loop to continue improvements
2. Commit fixes: `git add . && git commit -m "Fix issues from unfixable_issues.json"`
3. Review summary again to verify progress
4. Repeat until total_issues reaches acceptable level
