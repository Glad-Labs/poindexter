# Integration Validation Checklist

**Status:** Just completed integration of ModelSelectionPanel into TaskCreationModal ✅  
**Date:** December 19, 2025  
**Next Step:** Run these tests to verify integration

---

## Code Changes Made

### 1. TaskCreationModal.jsx

**File:** `web/oversight-hub/src/components/TaskCreationModal.jsx`

**Changes:**

- ✅ Added import: `import ModelSelectionPanel from './ModelSelectionPanel';`
- ✅ Added state: `const [modelSelection, setModelSelection] = useState({...})`
- ✅ Added component: `<ModelSelectionPanel onSelectionChange={...} />`
- ✅ Updated submit: Pass modelSelections, qualityPreference, estimatedCost to createBlogPost

**Verification:**

```bash
grep -n "import ModelSelectionPanel" web/oversight-hub/src/components/TaskCreationModal.jsx
# Should show import line present

grep -n "modelSelection" web/oversight-hub/src/components/TaskCreationModal.jsx
# Should show state + usage + pass to API
```

---

## Test Sequence

### Phase 1: Syntax Check (5 minutes)

**1.1 Check frontend builds**

```bash
cd web/oversight-hub
npm run build 2>&1 | head -50
# Should show: "Compiled successfully" or no errors
```

**Expected:** No build errors

---

### Phase 2: Component Rendering (10 minutes)

**2.1 Start frontend**

```bash
cd web/oversight-hub
npm start
# Should start on http://localhost:3000
```

**2.2 Visual check**

- [ ] Navigate to task creation page
- [ ] Click "Create Task" button
- [ ] Verify ModelSelectionPanel visible below Category field
- [ ] Verify 3 quality preset buttons visible
- [ ] Verify 6 phase dropdowns present
- [ ] No console errors (F12 → Console tab)

**Success criteria:** All UI elements render correctly

---

### Phase 3: API Integration (15 minutes)

**3.1 Check backend is running**

```bash
# Check if backend started
lsof -i :8001
# Should show python process listening on port 8001

# If not running:
cd src/cofounder_agent
python main.py
```

**3.2 Test cost estimation API**

```bash
# Get available models
curl -s http://localhost:8001/api/models/available-models | head -20

# Estimate full task cost (Fast mode)
curl -X POST http://localhost:8001/api/models/estimate-full-task \
  -H "Content-Type: application/json" \
  -d '{
    "models": {
      "research": "ollama",
      "outline": "ollama",
      "draft": "gpt-3.5-turbo",
      "assess": "gpt-4",
      "refine": "gpt-4",
      "finalize": "gpt-4"
    }
  }' 2>/dev/null | python -m json.tool

# Expected response should include: "total": 0.006 (or similar)
```

**Success criteria:** API responds with cost data

---

### Phase 4: End-to-End Workflow (20 minutes)

**4.1 Create a test task**

1. Open http://localhost:3000
2. Click "Create Task"
3. Fill in:
   - Blog Topic: "Testing Model Selection"
   - Primary Keyword: "testing"
   - Target Audience: "developers"
   - Category: "technology"
4. In ModelSelectionPanel, click "Fast" preset
5. Verify cost shows ~$0.006
6. Click "Create Task" button
7. Wait for execution (should show progress)

**4.2 Verify in database**

```bash
# Connect to database and check cost_logs
sqlite3 oversight.db  # or your database file

SELECT task_id, phase, model, cost_usd
FROM cost_logs
WHERE task_id = 'TASK_ID_FROM_STEP_6'
ORDER BY phase;

# Expected: 6 rows (research, outline, draft, assess, refine, finalize)
# Each with corresponding model and cost
```

**Success criteria:**

- [ ] Task created successfully
- [ ] 6 cost_logs entries in database
- [ ] Costs logged correctly per phase

---

### Phase 5: Integration Tests (30 minutes)

**5.1 Test Quality Presets**

```
In modal:
[ ] Fast button → cost ~$0.006
[ ] Balanced button → cost ~$0.015
[ ] Quality button → cost ~$0.040
```

**5.2 Test Model Selection Dropdowns**

```
For each phase dropdown:
[ ] Can select different models
[ ] Cost updates when changed
[ ] Selected value persists
```

**5.3 Test Form Submission**

```
[ ] Can't submit without blog topic
[ ] Can't submit without keyword
[ ] Can't submit without audience
[ ] Can submit with all required fields + model selection
[ ] Submitted data includes model selections
```

**5.4 Test Error Handling**

```
[ ] No console errors during rendering
[ ] No console errors when changing presets
[ ] No console errors on submit
[ ] Network errors handled gracefully
```

---

## Debugging Checklist

**If ModelSelectionPanel doesn't appear:**

```bash
# Check import is correct
grep "import ModelSelectionPanel" web/oversight-hub/src/components/TaskCreationModal.jsx

# Check file exists
ls -la web/oversight-hub/src/components/ModelSelectionPanel.jsx

# Check component is actually rendered
grep -A 5 "<ModelSelectionPanel" web/oversight-hub/src/components/TaskCreationModal.jsx

# Clear cache and rebuild
rm -rf web/oversight-hub/node_modules/.cache
npm run build
```

**If costs don't estimate correctly:**

```bash
# Check API is responding
curl http://localhost:8001/api/models/estimate-full-task

# Check backend service has the endpoint
grep -n "estimate-full-task" src/cofounder_agent/routes/model_selection_routes.py

# Check model costs in service
grep -n "MODEL_COSTS" src/cofounder_agent/services/model_selector_service.py
```

**If task creation fails:**

```bash
# Check createBlogPost accepts model selections
grep -n "modelSelections" web/oversight-hub/src/services/cofounderAgentClient.js

# Check backend task_routes accepts the data
grep -n "modelSelections" src/cofounder_agent/routes/task_routes.py
```

---

## Quick Verification Commands

**One-liner to check integration:**

```bash
# Frontend: Check ModelSelectionPanel imported
grep -q "import ModelSelectionPanel" web/oversight-hub/src/components/TaskCreationModal.jsx && echo "✅ Import found" || echo "❌ Import missing"

# Frontend: Check component used
grep -q "<ModelSelectionPanel" web/oversight-hub/src/components/TaskCreationModal.jsx && echo "✅ Component used" || echo "❌ Component not used"

# Frontend: Check state initialization
grep -q "modelSelection" web/oversight-hub/src/components/TaskCreationModal.jsx && echo "✅ State found" || echo "❌ State missing"

# Backend: Check route exists
grep -q "estimate-full-task" src/cofounder_agent/routes/model_selection_routes.py && echo "✅ Route exists" || echo "❌ Route missing"
```

---

## Test Results Template

**Date:** [DATE]  
**Tester:** [YOUR NAME]

**Phase 1: Syntax Check**

- [ ] Frontend builds without errors

**Phase 2: Component Rendering**

- [ ] ModelSelectionPanel visible
- [ ] Quality presets visible
- [ ] Phase dropdowns visible
- [ ] No console errors

**Phase 3: API Integration**

- [ ] Backend API responds
- [ ] Cost estimation works
- [ ] Models list correct

**Phase 4: End-to-End**

- [ ] Task created successfully
- [ ] Cost logged in database
- [ ] Correct number of cost_logs entries

**Phase 5: Integration Tests**

- [ ] Quality presets update cost
- [ ] Model dropdowns functional
- [ ] Form validation works
- [ ] No errors on submission

**Overall:** [ ] PASS [ ] FAIL

**Notes:**

```
[Add any issues or observations here]
```

---

## Next Steps After Testing

**If all tests PASS:**

1. ✅ Integration is complete
2. Move to Week 3: Quality Learning System
3. See: TESTING_AND_WEEK3_ROADMAP.md

**If tests FAIL:**

1. Check debugging checklist above
2. Review READY_TO_COPY_CODE_CHANGES.md
3. Verify all changes were made correctly
4. Check for typos in filenames/imports

---

## Time Estimates

| Phase                | Time       | Status   |
| -------------------- | ---------- | -------- |
| Phase 1: Syntax      | 5 min      | ⏳ Ready |
| Phase 2: Rendering   | 10 min     | ⏳ Ready |
| Phase 3: API         | 15 min     | ⏳ Ready |
| Phase 4: E2E         | 20 min     | ⏳ Ready |
| Phase 5: Integration | 30 min     | ⏳ Ready |
| **Total**            | **80 min** | ⏳ Ready |

**Recommended:** Run all phases today, takes ~1.5 hours  
**Then:** Move to Week 3 (6-8 hours)

---

**Status:** Ready for testing ✅  
**Questions?** See TESTING_AND_WEEK3_ROADMAP.md for detailed test procedures
