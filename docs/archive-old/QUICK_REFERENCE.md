# Task Status System - Developer Quick Reference

**Last Updated:** December 22, 2025  
**For:** Developers integrating task status management

---

## 🚀 Quick Start

### Import the Service

```python
from services.enhanced_status_change_service import EnhancedStatusChangeService
from services.tasks_db import TaskDatabaseService
from utils.task_status import TaskStatus, StatusTransitionValidator
```

### Basic Usage

```python
# Initialize
task_db = TaskDatabaseService(database_pool)
status_service = EnhancedStatusChangeService(task_db)

# Change status
success, message, errors = await status_service.validate_and_change_status(
    task_id="550e8400-e29b-41d4-a716-446655440000",
    new_status="awaiting_approval",
    reason="Content complete",
    user_id="user@example.com"
)

# Handle result
if success:
    print(f"✅ {message}")
else:
    print(f"❌ {message}")
    for error in errors:
        print(f"   - {error}")
```

---

## 📊 Valid Statuses

```python
TaskStatus.PENDING              # Initial state
TaskStatus.IN_PROGRESS          # Being worked on
TaskStatus.AWAITING_APPROVAL    # Waiting for review
TaskStatus.APPROVED             # Passed review
TaskStatus.PUBLISHED            # Live
TaskStatus.FAILED               # Error state
TaskStatus.ON_HOLD              # Paused
TaskStatus.REJECTED             # Rejected by reviewer
TaskStatus.CANCELLED            # Cancelled
```

---

## 🔄 Common Transitions

```
Workflow 1 (Success):
pending → in_progress → awaiting_approval → approved → published

Workflow 2 (Reject & Rework):
awaiting_approval → rejected → in_progress → awaiting_approval → approved

Workflow 3 (Failure):
pending → in_progress → failed → pending (retry)

Workflow 4 (Pause):
in_progress → on_hold → in_progress → (continue)
```

---

## ✅ Context Validation

### Examples by Status

**To `awaiting_approval`:**
```python
await status_service.validate_and_change_status(
    task_id=task_id,
    new_status="awaiting_approval",
    metadata={"approval_type": "editorial"}  # REQUIRED
)
```

**To `rejected`:**
```python
await status_service.validate_and_change_status(
    task_id=task_id,
    new_status="rejected",
    reason="Quality below threshold"  # REQUIRED
)
```

**To `published`:**
```python
await status_service.validate_and_change_status(
    task_id=task_id,
    new_status="published",
    metadata={"result": {"url": "https://example.com/post"}}  # REQUIRED
)
```

---

## 🔍 Query Audit Trail

### Get Full History

```python
audit_trail = await status_service.get_status_audit_trail(
    task_id=task_id,
    limit=50  # Up to 200
)

for entry in audit_trail["history"]:
    print(f"{entry['timestamp']} | {entry['old_status']} → {entry['new_status']}")
    print(f"  Reason: {entry['reason']}")
    print(f"  User: {entry['metadata'].get('user_id')}")
```

### Get Validation Failures

```python
failures = await status_service.get_validation_failures(
    task_id=task_id,
    limit=50
)

for failure in failures["failures"]:
    print(f"⚠️ {failure['reason']}")
    for error in failure['errors']:
        print(f"  - {error}")
```

---

## 🧪 Manual Testing

### Using REST API

```bash
# Test valid transition
curl -X PUT "http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/status/validated" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "in_progress",
    "reason": "Starting processing"
  }'

# Expected Response (Success):
# {
#   "success": true,
#   "task_id": "550e8400-e29b-41d4-a716-446655440000",
#   "message": "Status changed: pending → in_progress",
#   "errors": []
# }

# Get history
curl -X GET "http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/status-history?limit=10" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## 🐛 Debugging

### Check Valid Transitions

```python
from utils.task_status import TaskStatus, get_allowed_transitions

current = TaskStatus.IN_PROGRESS
allowed = get_allowed_transitions(current)
print(f"From {current.value}, can transition to: {allowed}")

# Output: {'awaiting_approval', 'failed', 'on_hold', 'cancelled'}
```

### Validate Status Value

```python
from utils.task_status import validate_status

try:
    status = validate_status("in_progress")
    print(f"Valid: {status}")
except ValueError as e:
    print(f"Invalid: {e}")
```

### Check Terminal States

```python
from utils.task_status import TaskStatus

print(TaskStatus.is_terminal_state("published"))  # True
print(TaskStatus.is_terminal_state("pending"))    # False
```

---

## 📝 Error Messages

### Invalid Transition
```json
{
  "success": false,
  "errors": ["Invalid transition: pending → published"],
  "message": "Invalid status transition"
}
```

### Missing Context
```json
{
  "success": false,
  "errors": [
    "Transition to awaiting_approval requires approval_type in context"
  ],
  "message": "Invalid status transition"
}
```

### Task Not Found
```json
{
  "success": false,
  "errors": ["task_not_found"],
  "message": "Task not found: 550e8400-e29b-41d4-a716-446655440000"
}
```

---

## 🔧 Configuration

### In Code

```python
# Create service with custom database
from services.tasks_db import TaskDatabaseService
from services.enhanced_status_change_service import EnhancedStatusChangeService

task_db = TaskDatabaseService(pool)
status_service = EnhancedStatusChangeService(task_db)
```

### In FastAPI Routes

```python
from fastapi import Depends
from utils.route_utils import get_enhanced_status_change_service

@app.put("/tasks/{task_id}/status")
async def update_status(
    task_id: str,
    status_service = Depends(get_enhanced_status_change_service)
):
    # Use status_service here
    pass
```

---

## 📚 More Information

- **Full Documentation:** `docs/TASK_STATUS_IMPLEMENTATION.md`
- **Unit Tests:** `tests/test_status_transition_validator.py`
- **Integration Tests:** `tests/test_enhanced_status_change_service.py`
- **Database Tests:** `tests/test_tasks_db_status_history.py`

---

## 🎯 Checklist for New Features

When adding status-dependent logic, ensure:

- [ ] Status is one of the valid TaskStatus values
- [ ] Transition is valid using `is_valid_transition()`
- [ ] Required context is provided (approval_type, reason, result)
- [ ] Change is logged via `log_status_change()`
- [ ] Audit trail is retrievable via `get_status_history()`
- [ ] Error cases are tested

---

## 💬 Common Questions

**Q: Can I skip status validation?**  
A: Not recommended, but use `db_service.update_task()` directly. Always prefer validated changes.

**Q: How long is history kept?**  
A: Indefinite. No automatic cleanup implemented yet.

**Q: Can I manually log a status change?**  
A: Yes, use `db_service.log_status_change()` directly for manual logging.

**Q: What if status change fails midway?**  
A: The service is designed to be atomic - either both validation and persistence succeed, or neither does.

---

**Need Help?** Check the test files for more examples! 🧪
