# Model Selection Integration Guide

**Status:** Week 1 Foundation Complete - Ready for Integration  
**Date:** December 19, 2025

---

## Current State Summary

### ✅ Already Built (Foundation)

**Backend:**

- `model_selector_service.py` (309 LOC) - Full service with all methods
  - `auto_select()` - Intelligent model selection by phase + quality
  - `estimate_cost()` - Single phase cost estimation
  - `estimate_full_task_cost()` - Full task cost breakdown
  - `validate_model_selection()` - Validate model/phase combo
  - `get_quality_summary()` - Explain quality presets

- `model_selection_routes.py` (475 LOC) - Complete API endpoints
  - `POST /api/models/estimate-cost` - Cost estimate
  - `POST /api/models/estimate-full-task` - Full task cost
  - `POST /api/models/auto-select` - Get auto-selection
  - `GET /api/models/available-models` - List models by phase
  - `POST /api/models/validate-selection` - Validate selection
  - `GET /api/models/quality-summary` - Quality info

- `cost_aggregation_service.py` (680 LOC) - Cost tracking
  - Queries cost_logs table from Week 2
  - Provides aggregations by phase, model, date

**Frontend:**

- `CostMetricsDashboard.jsx` (589 LOC) - Enhanced Week 2 dashboard
  - Displays phase/model/history breakdowns
  - Shows budget alerts
  - Auto-refreshes every 60 seconds

**Database:**

- `cost_logs` table created in Week 1
  - Tracks: task_id, phase, model, provider, cost_usd, quality_score
  - Properly indexed for fast queries

### 🆕 Just Created (Component)

**Frontend:**

- `ModelSelectionPanel.jsx` (NEW - 380 LOC)
  - Quality preset buttons (Fast/Balanced/Quality)
  - Per-phase model selection dropdown
  - Real-time cost estimation
  - Cost breakdown visualization
  - Model information cards

---

## Integration Steps (Do These Now)

### Step 1: Wire ModelSelectionPanel into TaskCreationModal

**File:** `web/oversight-hub/src/components/TaskCreationModal.jsx`

Find where the form is and add:

```jsx
import ModelSelectionPanel from './ModelSelectionPanel';

// Inside TaskCreationModal component:
const [modelSelection, setModelSelection] = useState({
  modelSelections: { research: 'auto', outline: 'auto', ... },
  qualityPreference: 'balanced',
  estimatedCost: 0.015,
});

// In your form JSX:
<ModelSelectionPanel
  onSelectionChange={(selection) => setModelSelection(selection)}
  initialQuality="balanced"
/>

// When submitting task:
const taskData = {
  // ... existing fields ...
  modelSelections: modelSelection.modelSelections,
  qualityPreference: modelSelection.qualityPreference,
  estimatedCost: modelSelection.estimatedCost,
};
```

### Step 2: Update Task Execution to Respect Model Selection

**File:** `src/cofounder_agent/routes/task_routes.py`

When executing a task, pass model selections to the pipeline:

```python
from services.model_selector_service import ModelSelector

@router.post("/api/tasks/{task_id}/execute")
async def execute_task(task_id: str, model_selections: dict = None):
    """Execute task with optional model selections"""

    task = await db.get_task(task_id)

    # Use provided selections or get from task
    selections = model_selections or task.get('modelSelections', {})

    # Execute pipeline with model control
    result = await execute_pipeline_with_models(
        task,
        selections,
        stream_callback=stream_queue.put
    )

    return result
```

### Step 3: Update LangGraph Pipeline to Use Selected Models

**File:** `src/cofounder_agent/services/langgraph_graphs/content_pipeline.py`

Modify the pipeline state and executors:

```python
from typing import TypedDict

class ContentPipelineState(TypedDict):
    # ... existing fields ...
    model_selections: dict  # NEW: {"research": "ollama", ...}
    quality_preference: str  # NEW: "fast", "balanced", or "quality"
    cost_breakdown: dict    # NEW: Track costs by phase

async def research_phase(state: ContentPipelineState):
    """Execute research with selected model"""

    # Get model for this phase
    model = state.get('model_selections', {}).get('research', 'auto')

    if model == 'auto':
        selector = ModelSelector()
        model = selector.auto_select('research', state.get('quality_preference'))

    # Use the selected model
    client = get_llm_client(model)
    result = await client.generate(prompt, ...)

    # Log cost
    cost = estimate_cost('research', model, len(result))
    if 'cost_breakdown' not in state:
        state['cost_breakdown'] = {}
    state['cost_breakdown']['research'] = cost

    return state

# Repeat for outline, draft, assess, refine, finalize
```

### Step 4: Integrate Cost Logging into Pipeline Execution

**File:** `src/cofounder_agent/routes/task_routes.py`

After task execution, log costs:

```python
from services.database_service import DatabaseService

async def log_task_costs(task_id: str, cost_breakdown: dict, quality_score: float):
    """Log all costs from task execution"""

    db = DatabaseService()

    for phase, cost in cost_breakdown.items():
        if phase != 'total':  # Skip total field
            await db.log_cost(
                task_id=task_id,
                phase=phase,
                model=selected_models.get(phase),
                provider=get_provider_for_model(selected_models.get(phase)),
                cost_usd=cost,
                quality_score=quality_score,  # From assessment phase
                success=True
            )
```

### Step 5: Update Frontend to Show Task Costs

**File:** `web/oversight-hub/src/components/TaskDetailModal.jsx`

Add cost display:

```jsx
import { getTaskCosts } from '../services/cofounderAgentClient';

export function TaskDetailModal({ taskId, onClose }) {
  const [taskCosts, setTaskCosts] = useState(null);

  useEffect(() => {
    const fetchCosts = async () => {
      const costs = await getTaskCosts(taskId);
      setTaskCosts(costs);
    };
    fetchCosts();
  }, [taskId]);

  return (
    <Modal>
      {/* ... existing task details ... */}

      {taskCosts && (
        <Card sx={{ mt: 2 }}>
          <CardHeader title="Cost Breakdown" />
          <CardContent>
            {Object.entries(taskCosts.breakdown).map(([phase, cost]) => (
              <Box
                key={phase}
                sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}
              >
                <Typography>{phase}</Typography>
                <Chip label={`$${cost.toFixed(4)}`} />
              </Box>
            ))}
            <Divider sx={{ my: 1 }} />
            <Box
              sx={{
                display: 'flex',
                justifyContent: 'space-between',
                fontWeight: 'bold',
              }}
            >
              <Typography>Total</Typography>
              <Typography>${taskCosts.total.toFixed(4)}</Typography>
            </Box>
          </CardContent>
        </Card>
      )}
    </Modal>
  );
}
```

---

## Testing Checklist (Before Week 2 Continues)

### API Endpoint Testing

```bash
# Test 1: Get available models
curl -H "Authorization: Bearer YOUR_JWT" \
  http://localhost:8001/api/models/available-models

# Test 2: Estimate cost for single phase
curl -H "Authorization: Bearer YOUR_JWT" \
  -X POST http://localhost:8001/api/models/estimate-cost \
  -H "Content-Type: application/json" \
  -d '{"phase": "draft", "model": "gpt-4"}'
# Expected: {"phase": "draft", "model": "gpt-4", "estimated_tokens": 3000, "estimated_cost": 0.009}

# Test 3: Estimate full task cost
curl -H "Authorization: Bearer YOUR_JWT" \
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
  }'
# Expected: {"research": 0.0, "outline": 0.0, "draft": 0.0015, ..., "total": 0.00375}

# Test 4: Auto-select models
curl -H "Authorization: Bearer YOUR_JWT" \
  -X POST http://localhost:8001/api/models/auto-select \
  -H "Content-Type: application/json" \
  -d '{"quality_preference": "balanced"}'
# Expected: {"research": "gpt-3.5-turbo", "outline": "gpt-3.5-turbo", ..., "quality": "balanced"}

# Test 5: Validate model for phase
curl -H "Authorization: Bearer YOUR_JWT" \
  -X POST http://localhost:8001/api/models/validate-selection \
  -H "Content-Type: application/json" \
  -d '{"phase": "assess", "model": "ollama"}'
# Expected: {"valid": false, "message": "Model ollama not available for assess"}
```

### Frontend Component Testing

1. **ModelSelectionPanel Component:**
   - [ ] Loads without errors
   - [ ] Quality preset buttons work
   - [ ] Clicking "Fast" sets appropriate models
   - [ ] Clicking "Balanced" sets appropriate models
   - [ ] Clicking "Quality" sets appropriate models
   - [ ] Manual model selection dropdowns work
   - [ ] Cost estimates update in real-time
   - [ ] Cost breakdown shows correct totals

2. **Integration with TaskCreationModal:**
   - [ ] ModelSelectionPanel displays when creating task
   - [ ] Model selections are captured in task data
   - [ ] Cost estimate is visible before submission
   - [ ] Task creation includes model selections

3. **Cost Display in Task Details:**
   - [ ] Task detail modal shows cost breakdown by phase
   - [ ] Total cost is displayed correctly
   - [ ] Cost matches what was estimated

### End-to-End Workflow

1. **Create Task with Model Selection:**
   - [ ] Open task creation modal
   - [ ] See ModelSelectionPanel
   - [ ] Click "Fast" preset (should show ~$0.003 cost)
   - [ ] Click "Balanced" preset (should show ~$0.015 cost)
   - [ ] Click "Quality" preset (should show ~$0.040 cost)
   - [ ] Manually select Ollama for research
   - [ ] See cost update to ~$0.014
   - [ ] Submit task

2. **Execute Task with Costs:**
   - [ ] Task executes normally
   - [ ] Each phase uses selected model
   - [ ] Costs are logged to cost_logs table
   - [ ] Total cost matches estimate (within 10%)

3. **View Costs in Dashboard:**
   - [ ] Navigate to Cost Metrics Dashboard
   - [ ] See phase breakdown includes your new task
   - [ ] See model breakdown includes used models
   - [ ] See cost history updated
   - [ ] Budget status reflects new costs

---

## Expected Behavior After Integration

### User Flow: "Write Blog Post with Cheap Models"

```
1. User clicks "Create Task"
2. Task creation modal opens
3. ModelSelectionPanel visible with "Balanced" selected
4. User clicks "Fast" preset
   → Ollama for research/outline, GPT-3.5 for draft, GPT-4 for assess
   → Cost estimate: $0.0037 per post
5. User enters "Blog post about AI" and clicks submit
6. Task starts executing
   → Research phase uses Ollama ($0.00)
   → Outline phase uses Ollama ($0.00)
   → Draft phase uses GPT-3.5 ($0.0015)
   → Assessment phase uses GPT-4 ($0.0015) with 4.5 stars quality
   → Refinement phase uses GPT-4 ($0.0015)
   → Finalize phase uses GPT-4 ($0.0015)
7. Task completes, costs logged
8. User navigates to dashboard
9. Dashboard shows:
   - Phase breakdown: Research $0.00, Outline $0.00, Draft $0.0015, Assess $0.0015...
   - Model breakdown: Ollama $0.00, GPT-3.5 $0.0015, GPT-4 $0.0045
   - History: Today's cost $0.0075 for 1 post
   - Budget: Used $0.0075 of $150, 7,490 posts remaining at this rate
```

### User Flow: "Quality Mode - Analyze Which Models Get Best Reviews"

```
1. User creates 10 posts with different quality settings
2. Each post gets assessed and rated (1-5 stars)
3. Dashboard tracks quality by model:
   - Ollama: 3.2 stars average
   - GPT-3.5: 3.8 stars average
   - GPT-4: 4.6 stars average
   - Claude Opus: 4.4 stars average
4. System learns: "User values GPT-4, willing to pay for quality"
5. Next time user selects "Quality" preset:
   → System recommends: "GPT-4 for most phases except research (use GPT-3.5)"
   → Saves $0.002 per post while keeping quality high
```

---

## What You Have Now vs What's Next

### Phase 1: Model Selection Foundation ✅

- [x] ModelSelector service with auto-selection
- [x] API routes for cost estimation
- [x] ModelSelectionPanel component
- [x] Integration points marked

### Phase 2: Pipeline Execution (Next)

- [ ] Update LangGraph to respect model selections
- [ ] Integrate cost logging into execution
- [ ] Test end-to-end task creation → execution → cost display

### Phase 3: Learning & Optimization (Week 3-4)

- [ ] Track quality_score for each model/phase combo
- [ ] Analyze which models get best ratings
- [ ] Auto-adjust recommendations based on quality
- [ ] Show optimization suggestions ("Try GPT-4 for assessment")

### Phase 4: Advanced Features (Week 5-6)

- [ ] Monthly summaries and ROI tracking
- [ ] Forecasting and budget alerts
- [ ] Cost comparison (what you saved vs premium options)
- [ ] Export reports for accounting

---

## File Organization

```
Backend:
  ✅ services/model_selector_service.py       (service)
  ✅ routes/model_selection_routes.py          (API endpoints)
  ✅ services/cost_aggregation_service.py      (cost tracking)
  ⏳ services/langgraph_graphs/content_pipeline.py (UPDATE: add model control)
  ⏳ routes/task_routes.py                     (UPDATE: add cost logging)

Frontend:
  ✅ components/ModelSelectionPanel.jsx        (NEW: created)
  ✅ components/CostMetricsDashboard.jsx       (Week 2: cost visualization)
  ⏳ components/TaskCreationModal.jsx          (UPDATE: integrate panel)
  ⏳ components/TaskDetailModal.jsx            (UPDATE: show costs)
  ⏳ services/cofounderAgentClient.js          (UPDATE: cost API calls)

Database:
  ✅ cost_logs table                           (Week 1-2)
  ⏳ Add quality_score analysis views          (Week 3)
```

---

## Quick Start Commands

```bash
# 1. Verify backend services are running
curl http://localhost:8001/api/health

# 2. Test model selection API
curl -H "Authorization: Bearer TOKEN" \
  http://localhost:8001/api/models/available-models

# 3. Test cost estimation
curl -H "Authorization: Bearer TOKEN" \
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
  }'

# 4. Create a test task with model selection
curl -H "Authorization: Bearer TOKEN" \
  -X POST http://localhost:8001/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test blog post",
    "modelSelections": {
      "research": "ollama",
      "outline": "ollama",
      "draft": "gpt-3.5-turbo",
      "assess": "gpt-4",
      "refine": "gpt-4",
      "finalize": "gpt-4"
    },
    "qualityPreference": "fast"
  }'

# 5. Check costs in dashboard
# Open http://localhost:3000/dashboard
# See Cost Metrics Dashboard with new task's costs
```

---

## Success Metrics

When this is complete, you'll have achieved:

✅ **Cost Control** - Users can choose cheap (Ollama) or quality (GPT-4) per phase  
✅ **Transparency** - Every phase shows exact cost before execution  
✅ **Flexibility** - Auto-select or manual override, per-task control  
✅ **Tracking** - Every cost logged and visible in dashboard  
✅ **Learning** - System learns which models get best reviews

This is **exactly what your solopreneurs want:**

- "I can see my $0.004 cost for this post"
- "I can choose Ollama to save money"
- "I can use GPT-4 only where it matters"
- "I can compare costs and quality over time"

---

## Need Help?

Check these files for examples:

- `model_selector_service.py` - Full docstrings with examples
- `model_selection_routes.py` - API endpoint implementations
- `ModelSelectionPanel.jsx` - Component with inline comments
- `CostMetricsDashboard.jsx` - How to fetch and display cost data

**Ready to proceed? Next steps:**

1. Test the API endpoints above
2. Integrate ModelSelectionPanel into TaskCreationModal
3. Update pipeline to use selected models
4. Test end-to-end with real task
5. Verify costs appear in dashboard

Let me know if you need help with any of these! 🚀
