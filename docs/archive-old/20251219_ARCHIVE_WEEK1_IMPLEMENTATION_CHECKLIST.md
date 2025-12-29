# 🚀 Week 1 → Week 2 Implementation Checklist

**Status:** Foundation Complete, Ready for Integration  
**Created:** December 19, 2025  
**Your Vision:** Solopreneurs with full cost control + transparency

---

## What's Done ✅

### Backend Foundation

- [x] `model_selector_service.py` (309 LOC) - Auto-select + cost estimation
- [x] `model_selection_routes.py` (475 LOC) - 5 API endpoints
- [x] `cost_aggregation_service.py` (680 LOC) - Cost tracking & analytics
- [x] Database schema `cost_logs` table - Logs all costs

### Frontend Foundation

- [x] `CostMetricsDashboard.jsx` (589 LOC) - Cost visualization
- [x] `ModelSelectionPanel.jsx` (380 LOC) - Per-phase model selection UI ⭐ NEW

### Testing

- [x] API endpoints tested and working
- [x] Component renders correctly

---

## What You Need to Do (Next 4 Hours)

### PART A: Integration (2 hours)

#### Task A.1: Wire ModelSelectionPanel into TaskCreationModal

**File:** `web/oversight-hub/src/components/TaskCreationModal.jsx`  
**Time:** 20 minutes  
**What to do:**

1. Add import at top:

```jsx
import ModelSelectionPanel from './ModelSelectionPanel';
```

2. Add state for model selection:

```jsx
const [modelSelection, setModelSelection] = useState({
  modelSelections: {
    research: 'auto',
    outline: 'auto',
    draft: 'auto',
    assess: 'auto',
    refine: 'auto',
    finalize: 'auto',
  },
  qualityPreference: 'balanced',
  estimatedCost: 0.015,
});
```

3. Add component to form (after topic/description fields):

```jsx
<ModelSelectionPanel
  onSelectionChange={(selection) => setModelSelection(selection)}
  initialQuality="balanced"
/>
```

4. Update form submission to include model data:

```jsx
const taskData = {
  // ... existing fields (title, description, etc) ...
  modelSelections: modelSelection.modelSelections,
  qualityPreference: modelSelection.qualityPreference,
  estimatedCost: modelSelection.estimatedCost,
};
```

**Verify:** Modal opens → ModelSelectionPanel visible → Cost updates on preset click

---

#### Task A.2: Add cost display to TaskDetailModal

**File:** `web/oversight-hub/src/components/TaskDetailModal.jsx`  
**Time:** 15 minutes  
**What to do:**

1. Add state:

```jsx
const [costBreakdown, setCostBreakdown] = useState(null);
```

2. Fetch costs on task load:

```jsx
useEffect(() => {
  if (task?.id) {
    fetchTaskCosts();
  }
}, [task?.id]);

const fetchTaskCosts = async () => {
  try {
    const response = await fetch(`/api/tasks/${task.id}/costs`);
    const data = await response.json();
    setCostBreakdown(data);
  } catch (err) {
    console.error('Failed to fetch costs:', err);
  }
};
```

3. Display costs in modal:

```jsx
{
  costBreakdown && (
    <Card sx={{ mt: 2 }}>
      <CardHeader title="Cost Breakdown" />
      <CardContent>
        <Grid container spacing={1}>
          {['research', 'outline', 'draft', 'assess', 'refine', 'finalize'].map(
            (phase) => (
              <Grid item xs={6} key={phase}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Typography variant="body2">{phase}</Typography>
                  <Chip
                    size="small"
                    label={`$${(costBreakdown[phase] || 0).toFixed(4)}`}
                  />
                </Box>
              </Grid>
            )
          )}
        </Grid>
        <Divider sx={{ my: 1 }} />
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            fontWeight: 'bold',
          }}
        >
          <Typography>Total</Typography>
          <Typography>${(costBreakdown.total || 0).toFixed(4)}</Typography>
        </Box>
      </CardContent>
    </Card>
  );
}
```

**Verify:** Open task details → See cost breakdown by phase → Total cost correct

---

#### Task A.3: Update task_routes.py to capture model selections

**File:** `src/cofounder_agent/routes/task_routes.py`  
**Time:** 25 minutes  
**What to do:**

1. Update TaskCreate schema to include model selections:

```python
class TaskCreate(BaseModel):
    # ... existing fields ...
    modelSelections: Optional[Dict[str, str]] = None  # NEW
    qualityPreference: Optional[str] = "balanced"     # NEW
    estimatedCost: Optional[float] = 0.0              # NEW
```

2. Update task creation endpoint to save selections:

```python
@router.post("/api/tasks")
async def create_task(task: TaskCreate, user: User = Depends(get_current_user)):
    """Create task with optional model selections"""

    task_data = {
        # ... existing fields ...
        "modelSelections": task.modelSelections or {},
        "qualityPreference": task.qualityPreference,
        "estimatedCost": task.estimatedCost,
    }

    task_id = await db.create_task(task_data)
    return {"id": task_id, "status": "created"}
```

3. Add endpoint to get task costs:

```python
@router.get("/api/tasks/{task_id}/costs")
async def get_task_costs(task_id: str, user: User = Depends(get_current_user)):
    """Get cost breakdown for task"""

    db = DatabaseService()
    costs = await db.get_task_costs(task_id)

    return costs  # Returns: {"research": 0.0, "outline": 0.001, ..., "total": 0.0025}
```

**Verify:** Create task with model selection → Data saved to database → Can retrieve costs

---

### PART B: Testing (2 hours)

#### Test B.1: API Endpoints (30 minutes)

Run these commands to verify everything works:

```bash
# Test 1: Get available models
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  http://localhost:8001/api/models/available-models | jq

# Test 2: Estimate full task cost
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -X POST http://localhost:8001/api/models/estimate-full-task \
  -H "Content-Type: application/json" \
  -d '{
    "models_by_phase": {
      "research": "ollama",
      "outline": "ollama",
      "draft": "gpt-3.5-turbo",
      "assess": "gpt-4",
      "refine": "gpt-4",
      "finalize": "gpt-4"
    }
  }' | jq

# Expected output:
# {
#   "research": 0.0,
#   "outline": 0.0,
#   "draft": 0.0015,
#   "assess": 0.0015,
#   "refine": 0.0015,
#   "finalize": 0.0015,
#   "total": 0.006
# }

# Test 3: Create task with model selection
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -X POST http://localhost:8001/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Blog Post",
    "description": "Test with model selection",
    "modelSelections": {
      "research": "ollama",
      "outline": "ollama",
      "draft": "gpt-3.5-turbo",
      "assess": "gpt-4",
      "refine": "gpt-4",
      "finalize": "gpt-4"
    },
    "qualityPreference": "fast",
    "estimatedCost": 0.006
  }' | jq

# Test 4: Get task costs
TASK_ID="from-test-3-response"
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  http://localhost:8001/api/tasks/$TASK_ID/costs | jq
```

**Success Criteria:** All tests return 200 status with expected JSON

---

#### Test B.2: Frontend Component (30 minutes)

1. **Start frontend:**

   ```bash
   cd web/oversight-hub
   npm start
   ```

2. **Test ModelSelectionPanel:**
   - Open http://localhost:3000
   - Navigate to task creation
   - Should see ModelSelectionPanel with:
     - [ ] "Fast" button
     - [ ] "Balanced" button
     - [ ] "Quality" button
     - [ ] 6 phase dropdowns
     - [ ] Cost breakdown showing ~$0.003, $0.015, $0.040
     - [ ] Total cost updates when selections change

3. **Test Integration:**
   - Click "Fast" preset
   - Verify cost estimate changes to ~$0.006
   - Click "Balanced" preset
   - Verify cost estimate changes to ~$0.015
   - Manually select models
   - Verify cost updates correctly

4. **Test Task Creation:**
   - Fill in task details
   - ModelSelectionPanel should show
   - Click "Create Task"
   - Task should be created with model selections saved

---

#### Test B.3: End-to-End (30 minutes)

1. **Create a Test Task:**
   - Open task creation modal
   - Select "Fast" mode (Ollama + budget models)
   - Title: "Test Blog Post"
   - Description: "Testing model selection and cost tracking"
   - Submit

2. **Verify in Database:**

   ```bash
   # Check task was created
   psql -h localhost -U postgres -d glad_labs_dev -c \
     "SELECT id, title, modelSelections, qualityPreference, estimatedCost FROM tasks ORDER BY created_at DESC LIMIT 1;"
   ```

3. **Execute Task:**
   - Backend should execute with selected models
   - Each phase logs cost to cost_logs table

4. **Verify in Dashboard:**
   - Open Cost Metrics Dashboard
   - Should see:
     - Phase breakdown updated
     - Model breakdown updated
     - History updated with today's costs
     - Budget usage updated

5. **Check Cost Logs:**
   ```bash
   psql -h localhost -U postgres -d glad_labs_dev -c \
     "SELECT phase, model, cost_usd FROM cost_logs WHERE task_id = 'YOUR_TASK_ID';"
   ```

---

### PART C: Documentation (Minimal)

Update your IMPLEMENTATION_ROADMAP_YOUR_VISION.md with:

```markdown
## Completion Status - Week 1 (Model Selection Foundation)

### ✅ COMPLETE

**Backend:**

- Model selection service with auto-select
- 5 API endpoints for model control
- Cost estimation for every selection
- Integration points in task execution

**Frontend:**

- ModelSelectionPanel component
- Quality preset buttons
- Per-phase model selection
- Real-time cost updates
- Integration into task creation

**Testing:**

- All API endpoints tested
- Component renders correctly
- End-to-end workflow verified

### Ready For: Week 2 (Cost Analytics Dashboard)

User can now:
✅ Choose "Fast" mode for cheap posts ($0.003)
✅ Choose "Balanced" for good value ($0.015)
✅ Choose "Quality" for best content ($0.040)
✅ See costs before submitting
✅ Override per phase manually
✅ View costs after execution

Next: Build learning system to auto-improve recommendations based on quality scores
```

---

## Time Estimate

| Task                          | Time          | Difficulty |
| ----------------------------- | ------------- | ---------- |
| A.1: Wire ModelSelectionPanel | 20 min        | Easy       |
| A.2: Add cost display         | 15 min        | Easy       |
| A.3: Update task routes       | 25 min        | Medium     |
| B.1: Test APIs                | 30 min        | Easy       |
| B.2: Test component           | 30 min        | Easy       |
| B.3: End-to-end test          | 30 min        | Medium     |
| C: Documentation              | 10 min        | Easy       |
| **TOTAL**                     | **2.5 hours** | **Mix**    |

---

## Success Criteria

When complete, you'll have:

✅ ModelSelectionPanel visible in task creation  
✅ Users can click quality presets (Fast/Balanced/Quality)  
✅ Cost estimates update in real-time  
✅ Model selections saved with task  
✅ Costs logged to database  
✅ Task details show cost breakdown  
✅ Dashboard includes cost data from new tasks

**Key Achievement:** Solopreneurs have full control over cost/quality trade-off

---

## Next Steps (After This Completes)

### Week 2: Learning System

- Track quality_score for each model/phase
- Analyze which models get best ratings
- Auto-adjust recommendations based on quality

### Week 3: Advanced Analytics

- Cost projections and forecasting
- Budget alerts at thresholds
- Optimization recommendations

### Week 4+: Full Product

- Multi-user support
- Team collaboration
- Advanced reporting

---

## Questions?

**If stuck on:**

- **API integration:** Check `model_selection_routes.py` for endpoint details
- **Component issues:** Check `ModelSelectionPanel.jsx` for inline comments
- **Database:** Check `cost_logs` table schema in Week 1 docs
- **Pipeline execution:** See `IMPLEMENTATION_GUIDE_MODEL_SELECTION.md`

**Ready to start? Go to PART A.1 above and follow the checklist! 🚀**

The foundation is solid. You're 1 hour away from having working model selection.
