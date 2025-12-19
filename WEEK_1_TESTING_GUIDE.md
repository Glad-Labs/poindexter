# Week 1 Testing Guide

**Status:** Task 1.7 - Testing & Verification
**Target:** Validate all cost tracking and model selection functionality

## Pre-Test Checklist

### 1. Database Preparation ✅

```bash
# Apply cost_logs migration
cd /c/Users/mattm/glad-labs-website/src/cofounder_agent

# Option A: Using psql directly
psql -h localhost -U cofounder -d cofounder_db -f migrations/002a_cost_logs_table.sql

# Option B: Using Python
python << 'EOF'
import asyncio
from services.database_service import DatabaseService

async def apply_migration():
    db = DatabaseService()
    await db.initialize()

    # Read and execute migration
    with open('migrations/002a_cost_logs_table.sql', 'r') as f:
        sql = f.read()

    async with db.pool.acquire() as conn:
        await conn.execute(sql)

    print("✅ Migration applied")
    await db.close()

asyncio.run(apply_migration())
EOF
```

### 2. Verify Imports

```bash
cd /c/Users/mattm/glad-labs-website/src/cofounder_agent

python << 'EOF'
import sys
sys.path.insert(0, '.')

print("Testing imports...")
try:
    from services.model_selector_service import ModelSelector, QualityPreference
    print("✅ ModelSelector imported")

    from services.langgraph_graphs.content_pipeline import create_content_pipeline_graph
    print("✅ LangGraph pipeline imported")

    from services.database_service import DatabaseService
    print("✅ DatabaseService imported")

    from routes.model_selection_routes import router as model_selection_router
    print("✅ Model selection routes imported")

    # Verify ModelSelector works
    selector = ModelSelector()
    cost = selector.estimate_cost("draft", "gpt-4")
    print(f"✅ ModelSelector.estimate_cost('draft', 'gpt-4') = ${cost:.6f}")

    print("\n✅ ALL IMPORTS SUCCESSFUL")
except Exception as e:
    print(f"❌ Import error: {e}")
    import traceback
    traceback.print_exc()
EOF
```

## Unit Tests

### 1. ModelSelector Tests

```python
# Run in: src/cofounder_agent/
python << 'EOF'
import asyncio
from services.model_selector_service import ModelSelector, QualityPreference

print("\n" + "="*60)
print("UNIT TEST 1: ModelSelector.estimate_cost()")
print("="*60)

selector = ModelSelector()

test_cases = [
    ("research", "ollama", 0.0),
    ("research", "gpt-3.5-turbo", 0.001),
    ("draft", "gpt-4", 0.003),
    ("assess", "gpt-4", 0.0015),
    ("finalize", "claude-3-opus", 0.015),
]

passed = 0
failed = 0

for phase, model, expected_min in test_cases:
    try:
        cost = selector.estimate_cost(phase, model)
        if cost >= expected_min:
            print(f"✅ estimate_cost('{phase}', '{model}') = ${cost:.6f}")
            passed += 1
        else:
            print(f"❌ estimate_cost('{phase}', '{model}') = ${cost:.6f} (expected >=${expected_min})")
            failed += 1
    except Exception as e:
        print(f"❌ Error: {e}")
        failed += 1

print(f"\nResult: {passed} passed, {failed} failed")

print("\n" + "="*60)
print("UNIT TEST 2: ModelSelector.auto_select()")
print("="*60)

for quality in ["fast", "balanced", "quality"]:
    quality_enum = QualityPreference[quality.upper()]
    print(f"\nQuality: {quality}")
    for phase in ["research", "outline", "draft", "assess"]:
        model = selector.auto_select(phase, quality_enum)
        print(f"  {phase:12} → {model}")

print("\n" + "="*60)
print("UNIT TEST 3: ModelSelector.estimate_full_task_cost()")
print("="*60)

models_by_phase = {
    "research": "ollama",
    "outline": "ollama",
    "draft": "gpt-3.5-turbo",
    "assess": "gpt-4",
    "refine": "gpt-4",
    "finalize": "gpt-4"
}

cost_breakdown = selector.estimate_full_task_cost(models_by_phase)
print(f"\nWith models: {models_by_phase}")
print(f"\nCost breakdown:")
for phase, cost in cost_breakdown.items():
    if phase != "total":
        print(f"  {phase:12} ${cost:.6f}")

print(f"\nTotal: ${cost_breakdown['total']:.6f}")

if cost_breakdown['total'] > 0:
    print("✅ Total cost calculation works")
else:
    print("❌ Total cost is zero")
EOF
```

### 2. DatabaseService Tests

```python
# Run in: src/cofounder_agent/
python << 'EOF'
import asyncio
from uuid import uuid4
from services.database_service import DatabaseService

async def test_cost_logging():
    print("\n" + "="*60)
    print("UNIT TEST 4: DatabaseService.log_cost()")
    print("="*60)

    db = DatabaseService()
    await db.initialize()

    try:
        task_id = str(uuid4())
        user_id = str(uuid4())

        # Test 1: Log a cost entry
        print(f"\nLogging cost for task {task_id[:8]}...")
        cost_log = {
            "task_id": task_id,
            "user_id": user_id,
            "phase": "draft",
            "model": "gpt-4",
            "provider": "openai",
            "cost_usd": 0.0015,
            "duration_ms": 2500,
            "success": True
        }

        result = await db.log_cost(cost_log)
        print(f"✅ Cost logged: ID={result.get('id')}")

        # Test 2: Retrieve task costs
        print(f"\nRetrieving costs for task {task_id[:8]}...")
        costs = await db.get_task_costs(task_id)

        print(f"Cost breakdown:")
        for phase, breakdown in costs.items():
            if phase != "total" and phase != "entries":
                print(f"  {phase}: ${breakdown.get('cost', 0):.6f} using {breakdown.get('model')}")

        print(f"Total: ${costs['total']:.6f}")

        if costs['total'] == 0.0015:
            print("✅ Cost retrieval and calculation works")
        else:
            print(f"❌ Expected total $0.0015, got ${costs['total']:.6f}")

        # Test 3: Log multiple phases
        print(f"\nLogging multiple phases...")
        for phase, cost in [("research", 0.0), ("outline", 0.00075), ("assess", 0.0015)]:
            await db.log_cost({
                "task_id": task_id,
                "user_id": user_id,
                "phase": phase,
                "model": "gpt-4" if phase != "research" else "ollama",
                "provider": "ollama" if phase == "research" else "openai",
                "cost_usd": cost,
                "duration_ms": 1000,
                "success": True
            })

        costs = await db.get_task_costs(task_id)
        total_expected = 0.0015 + 0.0 + 0.00075 + 0.0015

        print(f"Total cost after multiple logs: ${costs['total']:.6f}")
        print(f"Expected: ${total_expected:.6f}")

        if abs(costs['total'] - total_expected) < 0.000001:
            print("✅ Multiple phase logging works")
        else:
            print("❌ Cost totals don't match")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await db.close()

asyncio.run(test_cost_logging())
EOF
```

## Integration Tests

### 3. Pipeline State Tests

```python
# Run in: src/cofounder_agent/
python << 'EOF'
import asyncio
from datetime import datetime
from uuid import uuid4
from services.langgraph_graphs.content_pipeline import create_content_pipeline_graph
from services.langgraph_graphs.states import ContentPipelineState

print("\n" + "="*60)
print("INTEGRATION TEST 5: ContentPipelineState")
print("="*60)

# Test state initialization
task_id = str(uuid4())
state = {
    "topic": "Test Topic",
    "keywords": ["test", "keyword"],
    "audience": "general",
    "tone": "professional",
    "word_count": 1500,
    "request_id": task_id,
    "user_id": str(uuid4()),
    "models_by_phase": {
        "research": "ollama",
        "outline": "gpt-3.5-turbo",
        "draft": "gpt-4",
        "assess": "gpt-4",
        "refine": "gpt-4",
        "finalize": "gpt-4"
    },
    "quality_preference": "balanced",
    "research_notes": "",
    "outline": "",
    "draft": "",
    "final_content": "",
    "quality_score": 0.0,
    "quality_feedback": "",
    "passed_quality": False,
    "refinement_count": 0,
    "max_refinements": 3,
    "seo_score": 0.0,
    "metadata": {},
    "tags": [],
    "cost_breakdown": {},
    "total_cost": 0.0,
    "task_id": None,
    "status": "pending",
    "created_at": datetime.now(),
    "completed_at": None,
    "messages": [],
    "errors": []
}

print("\nState initialized:")
print(f"  Topic: {state['topic']}")
print(f"  Models by phase: {state['models_by_phase']}")
print(f"  Quality preference: {state['quality_preference']}")
print(f"  Cost breakdown: {state['cost_breakdown']}")
print(f"  Total cost: ${state['total_cost']:.6f}")

print("\n✅ State structure validated")
EOF
```

## API Integration Tests

### 4. Model Selection Routes

```bash
# Start the server first (in another terminal)
cd /c/Users/mattm/glad-labs-website/src/cofounder_agent
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

# Then run these tests (in main terminal)
echo ""
echo "=========================================="
echo "API TEST 6: GET /api/models/available-models"
echo "=========================================="
curl -s http://localhost:8000/api/models/available-models | python -m json.tool

echo ""
echo "=========================================="
echo "API TEST 7: POST /api/models/auto-select"
echo "=========================================="
curl -s -X POST http://localhost:8000/api/models/auto-select?quality_preference=balanced \
  -H "Content-Type: application/json" | python -m json.tool

echo ""
echo "=========================================="
echo "API TEST 8: POST /api/models/estimate-cost"
echo "=========================================="
curl -s -X POST http://localhost:8000/api/models/estimate-cost?phase=draft\&model=gpt-4 \
  -H "Content-Type: application/json" | python -m json.tool

echo ""
echo "=========================================="
echo "API TEST 9: POST /api/models/estimate-full-task"
echo "=========================================="
curl -s -X POST http://localhost:8000/api/models/estimate-full-task \
  -H "Content-Type: application/json" \
  -d '{
    "models_by_phase": {
      "research": "ollama",
      "outline": "gpt-3.5-turbo",
      "draft": "gpt-4",
      "assess": "gpt-4",
      "refine": "gpt-4",
      "finalize": "gpt-4"
    }
  }' | python -m json.tool
```

### 5. Content Routes Integration

```bash
echo ""
echo "=========================================="
echo "API TEST 10: POST /api/content/tasks (with models_by_phase)"
echo "=========================================="
curl -s -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "The Future of AI",
    "task_type": "blog_post",
    "style": "technical",
    "tone": "professional",
    "target_length": 2000,
    "models_by_phase": {
      "research": "ollama",
      "outline": "ollama",
      "draft": "gpt-4",
      "assess": "gpt-4",
      "refine": "gpt-4",
      "finalize": "gpt-4"
    }
  }' | python -m json.tool

echo ""
echo "=========================================="
echo "API TEST 11: POST /api/content/tasks (with quality_preference)"
echo "=========================================="
curl -s -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "E-commerce Best Practices",
    "task_type": "blog_post",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500,
    "quality_preference": "balanced"
  }' | python -m json.tool
```

## Validation Checklist

### Database Level ✅

- [ ] `cost_logs` table exists with all columns
- [ ] Indexes created on task_id, user_id, phase, model, provider
- [ ] Can insert cost entries
- [ ] Can retrieve and aggregate costs

### Service Level ✅

- [ ] ModelSelector estimates costs correctly
- [ ] ModelSelector auto-selects appropriate models
- [ ] DatabaseService logs costs to database
- [ ] DatabaseService aggregates costs by phase

### API Level ✅

- [ ] All 7 model selection endpoints return correct data
- [ ] Content routes accept models_by_phase parameter
- [ ] Content routes accept quality_preference parameter
- [ ] Content routes return estimated_cost in response
- [ ] Content routes return cost_breakdown in response
- [ ] Content routes return models_used in response

### Pipeline Level ✅

- [ ] State includes cost tracking fields
- [ ] Each phase accepts models_by_phase
- [ ] Each phase estimates cost before execution
- [ ] Each phase logs cost after execution
- [ ] Costs accumulate in state.total_cost
- [ ] Failed phases still log costs
- [ ] Multiple refinements accumulate costs

## Cost Validation

### Expected Costs:

```
Ollama phases:        $0.00
GPT-3.5-turbo:        $0.00075 (outline)
GPT-4 phases:         $0.003 (research/outline/draft/refine/finalize)
                      $0.0015 (assess)
Claude-3-Opus:        $0.015 per phase
```

### Example Task Costs:

**Fast Quality:**

- Research: $0.00 (ollama)
- Outline: $0.00 (ollama)
- Draft: $0.0015 (gpt-3.5-turbo)
- Assess: $0.0015 (gpt-4)
- Refine: $0.003 (gpt-4)
- Finalize: $0.0015 (gpt-4)
- **Total: $0.0075**

**Balanced Quality:**

- Research: $0.00 (ollama)
- Outline: $0.00075 (gpt-3.5-turbo)
- Draft: $0.003 (gpt-4)
- Assess: $0.0015 (gpt-4)
- Refine: $0.003 (gpt-4)
- Finalize: $0.0015 (gpt-4)
- **Total: $0.01025**

**Quality:**

- Research: $0.001 (gpt-3.5-turbo)
- Outline: $0.003 (gpt-4)
- Draft: $0.015 (claude-3-opus)
- Assess: $0.015 (claude-3-opus)
- Refine: $0.015 (claude-3-opus)
- Finalize: $0.015 (claude-3-opus)
- **Total: $0.079**

## Success Criteria

✅ **All tests must pass:**

1. ModelSelector calculates costs correctly
2. DatabaseService logs costs to database
3. Database queries aggregate costs properly
4. API endpoints return cost information
5. Content routes accept model selections
6. Pipeline integrates cost tracking
7. Failed phases still log costs
8. Multiple refinements accumulate costs

## Test Execution Order

1. **Setup** (5 min)
   - Apply database migration
   - Verify imports

2. **Unit Tests** (10 min)
   - ModelSelector tests
   - DatabaseService tests

3. **Integration Tests** (10 min)
   - State structure
   - Pipeline initialization

4. **API Tests** (15 min)
   - Model selection endpoints
   - Content creation with costs

5. **Validation** (10 min)
   - Cost calculations
   - Database aggregations

**Total Time: ~50 minutes**

## Troubleshooting

### Issue: `cost_logs` table not found

```
Solution: Re-apply migration
psql -h localhost -U cofounder -d cofounder_db -f migrations/002a_cost_logs_table.sql
```

### Issue: ModelSelector import fails

```
Solution: Verify file location
ls -la src/cofounder_agent/services/model_selector_service.py
```

### Issue: Cost values are 0

```
Solution: Check PHASE_TOKEN_ESTIMATES and MODEL_COSTS in ModelSelector
grep -n "PHASE_TOKEN_ESTIMATES\|MODEL_COSTS" services/model_selector_service.py
```

### Issue: Database connection fails

```
Solution: Verify PostgreSQL running
psql -h localhost -U cofounder -d cofounder_db -c "SELECT 1;"
```

## Next Steps After Testing

If all tests pass ✅:

1. Document any deviations from expected behavior
2. Create brief test summary
3. Proceed to Week 2 dashboard integration
4. Begin cost threshold alerts

If any tests fail ❌:

1. Identify which test failed
2. Check logs for error messages
3. Review code changes
4. Fix issue and re-run test
5. Document root cause

---

**Note:** Keep this guide for future reference and regression testing.
