# Unfixable Issues Logging - Implementation Summary

## What Changed

The agent loop now has comprehensive logging of issues that cannot be automatically fixed, enabling you to address them manually in a structured, prioritized way.

## Key Features

### 1. **Automatic Issue Categorization**

Three categories of unfixable issues are tracked:

- **Stuck Auto-Fixable Issues**: Auto-fixable tools that hit the stuck loop limit (3 iterations)
- **Non-Auto-Fixable Tool Issues**: Issues from tools requiring reasoning (pyright, mypy, bandit, etc.)
- **Phase 2 Test Failures**: Tests that failed after 5+ iterations and couldn't be fixed

### 2. **JSON Log Output**

Creates `unfixable_issues.json` with:

- Timestamp of when logging occurred
- Issues grouped by category
- Detailed error messages for each failure
- Summary statistics
- Recommended actions

### 3. **Console Summary Report**

Displays at agent loop completion:

```
================================================================================
📋 UNFIXABLE ISSUES SUMMARY
================================================================================
⚠️  688 issues require manual attention:
   • Auto-fixable issues stuck: 1
   • Non-fixable tool issues: 684
   • Phase 2 test failures: 1

📝 Detailed log: unfixable_issues.json

💡 Recommended actions:
   • Fix or suppress auto-fixable issues that got stuck
   • Address 684 issues from: pyright, pylint, bandit
   • Debug/fix 1 test failures (see phase2_failures)
```

## Implementation Details

### Files Modified

**`agent_loop.py`**

- Added `UnfixableIssuesLog` class (handles tracking, categorization, output)
- Integrated logging at 4 key points:
  1. Phase 1 stuck loop exit
  2. Phase 1 final diagnostic scan
  3. Phase 2 stuck test failure detection
  4. End of main() function

### Class: `UnfixableIssuesLog`

**Methods:**

- `__init__()` - Initialize log with timestamp
- `add_stuck_autofixable(issue_counts)` - Log Phase 1 stuck issues
- `add_nonfixable_issues(all_issues, autofixable_tools)` - Log non-fixable tool issues
- `add_phase2_failure(test_name, error_msg, iteration)` - Log Phase 2 test failures
- `finalize()` - Generate summary and write JSON file
- `print_summary()` - Display console report

## Example Output

### unfixable_issues.json

```json
{
  "timestamp": "2026-02-21T20:22:50.434132",
  "stuck_auto_fixes": {
    "autoflake": 1
  },
  "non_fixable_tools": {
    "pyright": 636,
    "pylint": 45,
    "bandit": 3
  },
  "phase2_failures": [
    {
      "test": "tests/e2e/test_tone_filtering",
      "error": "AssertionError: assert 0.0 >= 0.5",
      "stuck_at_iteration": 5
    }
  ],
  "summary": {
    "stuck_autofixable_issues": 1,
    "nonfixable_tool_issues": 684,
    "phase2_test_failures": 1,
    "total_issues": 686,
    "actions_required": [
      "Fix or suppress auto-fixable issues that got stuck (check logs)",
      "Address 684 issues from: pyright, pylint, bandit",
      "Debug/fix 1 test failures (see phase2_failures)"
    ]
  }
}
```

## Benefits

✅ **No more guessing about what's unfixable** - Clear categorization and error details

✅ **Prioritization guidance** - Recommended actions sorted by impact

✅ **Persistent history** - JSON log stays in repo, can track progress over time

✅ **Structured workflow** - Separate concerns: auto-fixable, reasoning-required, test-specific

✅ **Integration-ready** - JSON format can be consumed by CI/CD or monitoring systems

## Workflow Example

### Before This Change

```
1. Run agent_loop.py
2. Watch it fail many times
3. Try to manually figure out which issues are fixable
4. Get stuck trying random approaches
```

### After This Change

```
1. Run agent_loop.py
2. Agent completes with summary report
3. Open unfixable_issues.json to see exactly what needs fixing
4. Address issues by category:
   - Stuck auto-fixes: Check logs for what tool couldn't fix
   - Non-fixable: Run tool individually to understand issues
   - Test failures: Run specific failing test with -xvs for details
5. Fix issues in code
6. Re-run agent_loop to continue improvements
```

## Next Steps

1. **Test the feature:**

   ```bash
   python agent_loop.py
   # Wait for completion
   cat unfixable_issues.json  # See the detailed log
   ```

2. **Address issues** using the recommended actions

3. **Track progress** by comparing old and new unfixable_issues.json files

4. **Integrate with CI/CD** (future):

   ```bash
   # Fail build if more than N issues
   TOTAL=$(jq '.summary.total_issues' unfixable_issues.json)
   if [ $TOTAL -gt 100 ]; then exit 1; fi
   ```

## Documentation

See [UNFIXABLE_ISSUES_LOG_GUIDE.md](./UNFIXABLE_ISSUES_LOG_GUIDE.md) for comprehensive guide on:

- Issue categories and what they mean
- How to address each type
- Prioritization strategy
- Troubleshooting
- Integration examples
