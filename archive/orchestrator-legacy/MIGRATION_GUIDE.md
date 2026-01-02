# Orchestrator Migration Guide - Quick Reference

**Updated:** January 1, 2026

## TL;DR: What Changed

OLD orchestrators have been archived. All task generation now uses **UnifiedOrchestrator**.

| Aspect                | Before (Broken)                         | After (Fixed)                                             |
| --------------------- | --------------------------------------- | --------------------------------------------------------- |
| **Orchestrator Used** | `orchestrator_logic.Orchestrator` (OLD) | `services.unified_orchestrator.UnifiedOrchestrator` (NEW) |
| **Task Output**       | 797 chars of help text                  | Real blog posts                                           |
| **Reference Type**    | Stored at init (stale)                  | Dynamic from app.state                                    |
| **Content Routing**   | Fallback to help text                   | Proper ContentOrchestrator routing                        |

## For Developers

### How to Check Which Orchestrator Is Being Used

```python
# In task_executor._execute_task():
logger.info(f"Orchestrator type: {type(self.orchestrator).__name__}")

# Should log: "UnifiedOrchestrator" (not "Orchestrator")
```

### If You Need to Use the OLD Orchestrator (For Reference Only)

```python
# ❌ DON'T DO THIS IN PRODUCTION:
from orchestrator_logic import Orchestrator

# ✅ DO THIS INSTEAD:
from services.unified_orchestrator import UnifiedOrchestrator

# ✅ If auditing old code (archival only):
from archive.orchestrator_legacy.orchestrator_logic import Orchestrator
```

### How Dynamic Orchestrator Reference Works

```python
# In task_executor.py:

class TaskExecutor:
    def __init__(self, ..., app_state=None):
        self.orchestrator_initial = orchestrator  # From startup_manager
        self.app_state = app_state                # Injected by main.py

    @property
    def orchestrator(self):
        # Priority 1: Use updated from app.state
        if self.app_state and hasattr(self.app_state, 'orchestrator'):
            orch = getattr(self.app_state, 'orchestrator', None)
            if orch is not None:
                return orch
        # Priority 2: Fall back to initial
        return self.orchestrator_initial
```

**Flow:**

1. Startup: TaskExecutor gets OLD Orchestrator from startup_manager
2. main.py: Updates app.state.orchestrator = UnifiedOrchestrator
3. main.py: Injects app.state into task_executor
4. Execution: property getter fetches UnifiedOrchestrator from app.state ✅

## For QA / Testing

### Verify the Fix Works

```bash
# 1. Create a task via API
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_name": "Test Article",
    "topic": "How to test your pc stability",
    "primary_keyword": "PC testing",
    "target_audience": "tech enthusiasts"
  }'

# 2. Check the response in PostgreSQL
psql -U postgres -d glad_labs_dev -c "
SELECT id, task_name, LENGTH(content) as content_length
FROM content_tasks
ORDER BY created_at DESC
LIMIT 5;"

# Expected: content_length > 1000 (NOT 797)
# Expected: content contains actual article (NOT help commands)
```

### What to Look For

✅ **GOOD (After Fix):**

- Content length: 2000+ characters
- Content starts with article title/body
- Quality score feedback (60-100)
- No "help text" messages

❌ **BAD (Before Fix):**

- Content length: Exactly 797 characters
- Content starts with "🤖 Glad Labs AI Co-Founder - Available Commands"
- No real article content

## Files Affected by This Change

| File                  | Impact                               | Status                                 |
| --------------------- | ------------------------------------ | -------------------------------------- |
| startup_manager.py    | Initializes placeholder orchestrator | ✅ OK - Still needed for compatibility |
| main.py               | Replaces with UnifiedOrchestrator    | ✅ Fixed - Now proper init             |
| task_executor.py      | Dynamic reference via property       | ✅ Fixed - Gets updated orchestrator   |
| orchestrator_logic.py | OLD code (not used anymore)          | ⚠️ Archived - Reference only           |

## Troubleshooting

### "Still seeing help text in content?"

1. Check orchestrator type:

   ```bash
   grep "Orchestrator type" your_logs.txt
   ```

   Should show: `UnifiedOrchestrator` not `Orchestrator`

2. Restart server:

   ```bash
   poetry run uvicorn main:app --reload
   ```

3. Check task_executor received app.state:
   ```python
   # In logs:
   logger.info(f"TaskExecutor injected with app.state reference: {task_executor.app_state is not None}")
   # Should show: True
   ```

### "Google Cloud status error?"

Already fixed in orchestrator_logic.py line 350. If still seeing error:

1. Restart server
2. Check logs for import errors
3. Verify orchestrator_logic.py was updated

## Archive Location

```
archive/orchestrator-legacy/
├── ARCHIVAL_NOTES.md                    # Why it was archived
├── CLEANUP_SESSION_SUMMARY.md           # This cleanup session details
└── orchestrator_logic.py.backup         # Backup for audit
```

## Questions?

Refer to these files:

- **What was the bug?** → [CLEANUP_SESSION_SUMMARY.md](archive/orchestrator-legacy/CLEANUP_SESSION_SUMMARY.md) - Issues Fixed section
- **How was it fixed?** → [ARCHIVAL_NOTES.md](archive/orchestrator-legacy/ARCHIVAL_NOTES.md) - Resolution section
- **What's next?** → [CLEANUP_SESSION_SUMMARY.md](archive/orchestrator-legacy/CLEANUP_SESSION_SUMMARY.md) - Next Steps section
