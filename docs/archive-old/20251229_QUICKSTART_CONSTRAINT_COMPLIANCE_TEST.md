# Quick Start: Testing Constraint Compliance Display

## TL;DR - 30 Second Setup

```bash
# 1. Make sure backend is running (should be running already)
curl http://localhost:8000/health

# 2. Run the test script
python scripts/test_constraint_compliance.py

# 3. Wait for task to complete (5-10 minutes)

# 4. Open Oversight Hub and find the task
http://localhost:3001
```

---

## What This Does

The test script:

1. Creates a **real task** with **actual content constraints**
2. Waits for the backend to generate content and validate constraints
3. Extracts the constraint compliance data from the response
4. Verifies the data structure is correct
5. Provides instructions for viewing in the Oversight Hub

---

## Why This Matters

- **Tests real integration** - Not mock data
- **Generates authentic metrics** - Backend actually validates constraints
- **Confirms full pipeline** - API → DB → Frontend
- **Validates component display** - See metrics in Oversight Hub

---

## Running the Test

### Prerequisites

- Python 3.8+
- Backend running on http://localhost:8000
- PostgreSQL database configured
- requests library installed (usually included with Python)

### Command

```bash
python scripts/test_constraint_compliance.py
```

### Expected Output

```
======================================================================
STEP 1: Creating Task with Content Constraints
======================================================================

📤 Sending POST /api/tasks with constraints:
{
  "task_name": "Test Constraint Compliance - AI Marketing 2025",
  "topic": "How Artificial Intelligence is Revolutionizing Digital Marketing...",
  ...
}

✅ Task created successfully!
   Task ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   Status: pending

======================================================================
STEP 2: Monitoring Task Progress
======================================================================

⏳ [    0s] Status: pending      (0%)
⚙️  [    5s] Status: running      (25%) Researching topic...
⚙️  [   10s] Status: running      (50%) Generating content...
⚙️  [   15s] Status: running      (75%) Validating constraints...
✅ [   20s] Status: completed    (100%) Task complete!

... continues with more steps ...

✅ TEST COMPLETE
======================================================================

Task ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
Status: completed
Compliance Valid: True

Next: Visit http://localhost:3001 to verify the display in Oversight Hub
```

---

## Viewing Results in Oversight Hub

1. **Open:** http://localhost:3001 (should already be running)
2. **Login:** If needed (use test credentials)
3. **Go to:** Tasks tab
4. **Find:** Search for the task ID from the script output
5. **Click:** Open the task detail
6. **Look for:** "Constraint Compliance" section

### Expected to See:

- ✓ Word count progress bar (should show ~800/800)
- ✓ Writing style indicator ("professional")
- ✓ Strict mode status ("ON")
- ✓ Variance percentage (something like "-0.625%")
- ✓ Compliance status badge (green for "compliant")

---

## Troubleshooting

### Issue: "Backend is not responding"

```bash
# Check if backend is running
curl http://localhost:8000/api/tasks

# If not running, start it:
npm run dev:cofounder
# or
python -m uvicorn main:app --reload
```

### Issue: "Task timed out after 120 seconds"

This is normal if the backend is slow. You can:

- Manually check the task status after waiting longer
- Check backend logs for errors
- Try again with a simpler task

### Issue: "No constraint_compliance data found"

This means the task didn't use the constraint system. Check:

1. Was the script able to create the task?
2. Did the task complete successfully?
3. Check the task ID is correct
4. Check backend logs for errors during content generation

### Issue: "Cannot import requests"

```bash
# Install requests library
pip install requests
```

---

## Manual Alternative: Using cURL

If you prefer to run manually without the script:

```bash
# 1. Create task
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXYtdXNlciIsImlhdCI6MTcwMjc0NzIwMCwianRpIjoiZGV2LXRva2VuIiwidHlwZSI6ImFjY2VzcyJ9.Y8J_2F7G5H4K9L0M1N2O3P4Q5R6S7T8U9V0W1X2Y3Z4" \
  -H "Content-Type: application/json" \
  -d '{
    "task_name": "Test Task",
    "topic": "AI in Healthcare",
    "content_constraints": {
      "target_word_count": 800,
      "word_count_tolerance": 10,
      "writing_style": "professional"
    }
  }'

# 2. Copy the "id" from response

# 3. Check status (repeat until "completed")
curl -X GET http://localhost:8000/api/tasks/{id} \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXYtdXNlciIsImlhdCI6MTcwMjc0NzIwMCwianRpIjoiZGV2LXRva2VuIiwidHlwZSI6ImFjY2VzcyJ9.Y8J_2F7G5H4K9L0M1N2O3P4Q5R6S7T8U9V0W1X2Y3Z4"

# 4. Look for "constraint_compliance" in the response
```

---

## Understanding the Results

The compliance data includes:

| Field                         | Meaning                  | Example                |
| ----------------------------- | ------------------------ | ---------------------- |
| `word_count_actual`           | Words in final content   | 795                    |
| `word_count_target`           | Target word count        | 800                    |
| `word_count_within_tolerance` | Within acceptable range? | true                   |
| `word_count_percentage`       | Deviation from target    | -0.625% (under target) |
| `writing_style`               | Applied style            | "professional"         |
| `strict_mode_enforced`        | Strict checking enabled? | true                   |
| `compliance_status`           | Overall status           | "compliant"            |

---

## More Information

- Full guide: [CONSTRAINT_COMPLIANCE_DISPLAY_TESTING.md](../docs/CONSTRAINT_COMPLIANCE_DISPLAY_TESTING.md)
- Status report: [CONSTRAINT_COMPLIANCE_DISPLAY_IMPLEMENTATION_STATUS.md](../docs/CONSTRAINT_COMPLIANCE_DISPLAY_IMPLEMENTATION_STATUS.md)
- Session summary: [SESSION_DEC_26_CONSTRAINT_DISPLAY_FINALIZATION.md](../docs/SESSION_DEC_26_CONSTRAINT_DISPLAY_FINALIZATION.md)

---

## Success Indicators

✅ **Test Successful When:**

1. Script runs without errors
2. Task is created with a task ID
3. Task completes (status = "completed")
4. Compliance data is extracted
5. All required fields are present
6. Component displays in Oversight Hub

That's it! The component is working correctly.
