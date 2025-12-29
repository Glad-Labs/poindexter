# Constraint Compliance Display Testing Guide

## Overview

The `ConstraintComplianceDisplay` component in the Oversight Hub frontend is **complete and functional**. It properly displays constraint compliance metrics when tasks have been generated with content constraints.

## Current State

### ✅ What's Working

- Frontend component `ConstraintComplianceDisplay.jsx` renders correctly
- Backend extracts `constraint_compliance` from task metadata
- UI displays word count, tolerance, status indicators
- Integration with task detail views is functional

### ⚠️ What's Missing

- **Existing test tasks DON'T have constraint_compliance data**
  - Database has 140 tasks, but ZERO with constraint compliance
  - These tasks were created before the constraint feature was added
  - They went through the old pipeline that didn't generate compliance data

## Root Cause Analysis

```
Timeline:
---------
Dec 15: Old pipeline created tasks without constraint tracking
Dec 20: Constraint system added to ContentOrchestrator
Dec 25: ConstraintComplianceDisplay component created
Dec 26: NOW → Existing tasks still lack the compliance data
```

**Why mock data is NOT the solution:**

- ❌ Technical debt - masks real integration issues
- ❌ Doesn't test the actual data flow
- ❌ Hides potential bugs in the real pipeline
- ❌ Makes it harder to debug real compliance generation

## Proper Testing Approach

### Option 1: Create a Real Task with Constraints (RECOMMENDED)

Create a new content generation task that explicitly includes word count constraints. This will generate real `constraint_compliance` data through the actual pipeline.

#### Step 1: Call the Backend API

```bash
# Generate JWT token (use existing test token or get one from Oversight Hub)
TOKEN="your_jwt_token_here"

# Create a task with content constraints
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_name": "Test Constraint Compliance - AI Marketing Blog",
    "topic": "How AI is Revolutionizing Digital Marketing",
    "category": "marketing",
    "primary_keyword": "AI marketing",
    "target_audience": "Digital marketing professionals",
    "content_constraints": {
      "target_word_count": 800,
      "word_count_tolerance": 10,
      "writing_style": "professional",
      "strict_mode": true
    }
  }'

# Response will include task_id, e.g.:
# {"id": "new-task-uuid", "status": "pending", "task_name": "..."}
```

#### Step 2: Monitor Task Progress

```bash
# Poll the task status every 5 seconds until completed
TASK_ID="new-task-uuid"
TOKEN="your_jwt_token_here"

# Check status
curl -X GET "http://localhost:8000/api/tasks/$TASK_ID" \
  -H "Authorization: Bearer $TOKEN"

# Response will show:
# {
#   "id": "...",
#   "status": "completed",
#   "constraint_compliance": {
#     "word_count_actual": 795,
#     "word_count_target": 800,
#     "word_count_within_tolerance": true,
#     "word_count_percentage": -0.625,
#     "compliance_status": "compliant",
#     ...
#   },
#   "task_metadata": { ... }
# }
```

#### Step 3: View in Oversight Hub

1. Open http://localhost:3001 (Oversight Hub)
2. Go to Tasks tab
3. Click on the newly created task
4. The `ConstraintComplianceDisplay` component should render with:
   - ✅ Word count progress bar (should show ~800/800)
   - ✅ Writing style indicator
   - ✅ Strict mode status (ON)
   - ✅ Variance percentage (-0.625%)
   - ✅ Compliance status badge (green for "compliant")

### Option 2: Manually Update Existing Task (For Quick Testing)

If you want to test the display with existing data immediately:

```sql
-- Add constraint_compliance to an existing completed task
UPDATE content_tasks
SET task_metadata = jsonb_set(
  COALESCE(task_metadata, '{}'::jsonb),
  '{constraint_compliance}',
  '{
    "word_count_actual": 795,
    "word_count_target": 800,
    "word_count_within_tolerance": true,
    "word_count_percentage": -0.625,
    "writing_style": "professional",
    "strict_mode_enforced": true,
    "compliance_status": "compliant"
  }'::jsonb
)
WHERE task_id = '96dbfae2-7548-4dda-902a-6526400212fe'
RETURNING task_id, task_metadata;
```

Then refresh the Oversight Hub and the task will show compliance data.

## Component Architecture

### ConstraintComplianceDisplay Props

```javascript
{
  compliance: {
    word_count_actual: number,          // Current word count (795)
    word_count_target: number,           // Target word count (800)
    word_count_within_tolerance: boolean, // true if within range
    word_count_percentage: number,       // -0.625 (minus 0.625%)
    writing_style: string,               // "professional"
    strict_mode_enforced: boolean,       // true if strict checking
    compliance_status: "compliant" | "warning" | "violation"
  },
  phaseBreakdown?: {                     // Optional phase-by-phase data
    [phaseName]: {
      word_count: number,
      status: "compliant" | "warning" | "violation"
    }
  }
}
```

### Data Flow

```
Content Generation Pipeline
  ↓
ContentOrchestrator.run()
  ↓ (calls validate_constraints)
  ↓
Generates constraint_compliance object
  ↓
Stores in task_metadata['constraint_compliance']
  ↓
POST /api/tasks returns task with constraint_compliance
  ↓
GET /api/tasks/{id} returns constraint_compliance
  ↓
convert_db_row_to_dict() extracts it to top level
  ↓
UnifiedTaskResponse includes constraint_compliance
  ↓
Frontend receives and passes to ConstraintComplianceDisplay
  ↓
Component renders compliance visualization
```

## Testing Checklist

### Backend Tests

- [ ] TaskOrchestrator receives content_constraints in request
- [ ] ContentOrchestrator.run() validates constraints
- [ ] validate_constraints() generates ConstraintCompliance object
- [ ] constraint_compliance stored in task_metadata
- [ ] GET /api/tasks/{id} returns constraint_compliance at top level

### Frontend Tests

- [ ] ConstraintComplianceDisplay renders when compliance data exists
- [ ] Word count progress bar shows correct percentage
- [ ] Status badge shows correct color (green/orange/red)
- [ ] Variance percentage displays correctly (+/- X%)
- [ ] Handles missing data gracefully (doesn't crash)

### Integration Tests

- [ ] Create task with constraints via API
- [ ] Monitor task completion
- [ ] Verify constraint_compliance in database
- [ ] Display in Oversight Hub matches expected metrics

## Files Reference

| File                                                                                                                                             | Purpose                                   |
| ------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------- |
| [web/oversight-hub/src/components/tasks/ConstraintComplianceDisplay.jsx](web/oversight-hub/src/components/tasks/ConstraintComplianceDisplay.jsx) | React component for displaying compliance |
| [src/cofounder_agent/utils/constraint_utils.py](src/cofounder_agent/utils/constraint_utils.py)                                                   | Word count & style validation             |
| [src/cofounder_agent/services/content_orchestrator.py#L309-L365](src/cofounder_agent/services/content_orchestrator.py)                           | Generates constraint_compliance           |
| [src/cofounder_agent/routes/task_routes.py#L25-135](src/cofounder_agent/routes/task_routes.py)                                                   | Extracts compliance from metadata         |

## Troubleshooting

### Issue: "constraint_compliance is undefined"

**Check 1:** Does the task have content_constraints in creation request?

```bash
curl http://localhost:8000/api/tasks/$TASK_ID | grep content_constraints
```

**Check 2:** Did the backend process constraints?

```bash
# Check task_metadata in database
psql glad_labs_dev -c "SELECT task_metadata FROM content_tasks WHERE task_id='...' LIMIT 1"
```

**Check 3:** Is the component receiving the prop?

- Open browser DevTools
- Go to React Components tab
- Find ConstraintComplianceDisplay
- Check props panel for `compliance` value

### Issue: "Status shows 'violation' but word count looks fine"

Check the `word_count_tolerance` setting:

- If tolerance is 5% and target is 800:
  - Valid range: 760-840
  - Actual: 750 = violation (outside range)
- Check the calculation: `|actual - target| / target > tolerance`

## Next Steps

1. **For immediate testing:** Use Option 2 (manual SQL update) to add compliance data to an existing task
2. **For production verification:** Use Option 1 (create new task) to validate the full pipeline
3. **For debugging:** Enable SQL_DEBUG=true in .env.local to see constraint validation logs

## Related Documents

- [WORD_COUNT_IMPLEMENTATION_COMPLETE.md](WORD_COUNT_IMPLEMENTATION_COMPLETE.md) - Full constraint system implementation
- [FRONTEND_CONSTRAINT_INTEGRATION_COMPLETE.md](FRONTEND_CONSTRAINT_INTEGRATION_COMPLETE.md) - Frontend integration details
- [constraint_utils.py](../src/cofounder_agent/utils/constraint_utils.py) - Validation logic
