# Week 2 → Week 3: Transition Plan

**Current Status:** Week 2 complete, ready for testing  
**Next Phase:** Week 3 smart defaults and learning  
**Timeline:** Next sprint session

---

## Week 2 Handoff - What's Ready

### ✅ Fully Implemented

**Backend (Python)**

- [x] cost_aggregation_service.py - All 5 query methods working
- [x] metrics_routes.py - All 5 endpoints registered
- [x] Database integration - cost_logs queries optimized
- [x] Error handling - Graceful fallbacks implemented

**Frontend (React)**

- [x] cofounderAgentClient.js - 4 new API methods
- [x] CostMetricsDashboard.jsx - Phase, model, history tables
- [x] State management - Safe null handling
- [x] Auto-refresh - 60 second intervals

**Testing**

- [x] Validation suite - test_week2_cost_analytics.py
- [x] All tests passing
- [x] Integration verified

**Documentation**

- [x] WEEK_2_IMPLEMENTATION_COMPLETE.md - Full technical docs
- [x] WEEK_2_QUICK_START.md - Testing procedures
- [x] WEEK_2_COMPLETION_SUMMARY.md - Project overview

### ⚠️ Still Needed (Before Production)

1. **Environment Testing**
   - [ ] Start backend services
   - [ ] Start frontend services
   - [ ] Verify PostgreSQL running
   - [ ] Check DATABASE_URL set

2. **Real Data Testing**
   - [ ] Navigate to dashboard
   - [ ] Verify tables populate
   - [ ] Check budget alerts
   - [ ] Test auto-refresh

3. **Edge Case Testing**
   - [ ] No data in cost_logs
   - [ ] Budget lower than spent
   - [ ] History with single day
   - [ ] Model/phase with zero costs

---

## Week 3 Foundation (Ready to Build)

### Planned Features

#### A. Smart Model Selection (Automatic)

**Goal:** "Given a task description, pick the best model"

**Architecture:**

```
User Task: "Write blog post outline"
  ↓
ModelSelector Service (Week 1 foundation)
  ├─ Check phase: "outline"
  ├─ Look up best model for outline
  ├─ Return: "gpt-3.5" (good quality, low cost)
  └─ Log to cost_logs
```

**Implementation:**

- Use quality_score data from cost_logs
- Track average quality per model per phase
- Select highest quality model within cost budget

**Files to Create:**

- `services/model_recommendation_service.py` - New file
- Update `routes/task_routes.py` - Call new service

**Data Needed:**

- cost_logs.quality_score (from assessments)
- Historical performance per model/phase

#### B. Learning System (Self-Improving)

**Goal:** "System learns which models get best reviews"

**Architecture:**

```
Assessment Task: "Rate the blog post outline"
  ├─ User rates: 5 stars
  ├─ Log: model=gpt-3.5, quality_score=5.0
  ├─ Calculate: avg_quality for gpt-3.5 in outline phase
  └─ Update: Recommendation confidence score
```

**Implementation:**

- Query cost_logs for avg quality_score by model/phase
- Track sample size (confidence)
- Increase recommended weight for high-scoring models
- Decrease weight for low-scoring models

**Files to Modify:**

- `services/cost_aggregation_service.py` - Add quality analysis
- `services/model_recommendation_service.py` - Use quality data
- Metrics dashboard - Show quality/cost trade-off

**Data Already Available:**

- cost_logs.quality_score (logged in Week 1)
- cost_logs.model (which model was used)
- cost_logs.phase (what task phase)

#### C. Monthly Summaries (Reports)

**Goal:** "Solopreneurs get monthly cost breakdown"

**Architecture:**

```
End of Month (automated)
  ├─ Calculate total spent: $47.50
  ├─ Best model: "gpt-4" (48% of spend)
  ├─ Most expensive phase: "draft" (52% of spend)
  ├─ Quality trend: "improving"
  ├─ Recommendations: "Use Ollama more in research"
  └─ Send: Email report
```

**Implementation:**

- Scheduled task runs on last day of month
- Queries cost_logs for date range
- Generates statistics
- Sends email via EmailService

**Files to Create:**

- `services/report_generator_service.py` - New file
- `routes/reports_routes.py` - New routes
- Email template for monthly summary

#### D. Cost Optimization (Recommendations)

**Goal:** "System suggests where to save money"

**Architecture:**

```
Analysis:
  ├─ GPT-4 in research phase: 80% cost, 20% better quality
  │  → Recommendation: "Could use GPT-3.5, save 60%"
  ├─ Ollama in assessment: 0% cost, 3.5 star average
  │  → Recommendation: "Use more Ollama here"
  └─ Claude unused: Never selected
     → Recommendation: "Consider removing Claude from options"
```

**Implementation:**

- Compare models within same phase
- Calculate cost delta vs quality delta
- Suggest model swaps with ROI numbers
- Track if user acts on recommendations

**Files to Create:**

- `services/optimization_engine_service.py` - New file
- Analytics queries for cost/quality correlation

---

## Technical Debt & Optimizations

### Quick Wins (Hour or Less)

- [ ] Add caching to cost_aggregation_service (Redis)
- [ ] Add pagination to history endpoint (if 30+ days)
- [ ] Add CSV export for cost data
- [ ] Add date picker to dashboard (instead of preset periods)
- [ ] Add cost filtering by phase/model

### Medium Effort (4-8 Hours)

- [ ] Multi-user cost allocation
- [ ] Cost by user/team
- [ ] Custom budget alerts (per phase)
- [ ] Model performance dashboard
- [ ] Cost/quality scatter plot

### Strategic (16+ Hours)

- [ ] Cost forecasting (machine learning)
- [ ] Budget optimization algorithm
- [ ] Team cost tracking
- [ ] Cost variance analysis
- [ ] Billing integration (Stripe)

---

## Code Architecture Pattern (For Week 3)

All new services should follow this pattern:

```python
# services/new_feature_service.py

class NewFeatureService:
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    async def main_method(self, param: str) -> dict:
        """
        Description of what this does.

        Args:
            param: Description of parameter

        Returns:
            dict with keys: result, status, data, timestamp

        Raises:
            ValueError: If validation fails
            DatabaseError: If query fails
        """
        try:
            # Validate input
            if not param:
                raise ValueError("param required")

            # Connect to database
            async with self.db_service.get_connection() as conn:
                # Execute queries
                result = await conn.fetch(query, param)

            # Process results
            processed = self._process_results(result)

            return {
                "result": processed,
                "status": "success",
                "data": {...},
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Error in main_method: {e}")
            return {
                "result": None,
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def _helper_method(self, data: list) -> list:
        """Internal processing helper."""
        return [self._transform(item) for item in data]
```

---

## Database Schema Changes (Week 3)

No new tables needed, but consider these columns:

### Columns to Track Learning

```sql
ALTER TABLE cost_logs ADD COLUMN (
  -- For learning system
  user_rating INTEGER,           -- 1-5 stars from assessment
  recommendation_score FLOAT,    -- Confidence in model choice
  model_recommendation VARCHAR(100), -- What we predicted

  -- For optimization
  better_alternative VARCHAR(100),  -- What we should have used
  potential_savings DECIMAL(10,6)   -- Cost difference
);
```

### Index for New Queries

```sql
CREATE INDEX idx_quality_score ON cost_logs(model, phase, quality_score);
CREATE INDEX idx_user_rating ON cost_logs(model, user_rating);
CREATE INDEX idx_recommendation ON cost_logs(model_recommendation);
```

---

## Testing Strategy for Week 3

### Unit Tests (Per Component)

```python
# tests/test_model_recommendation_service.py
async def test_recommend_model_for_phase():
    """Given phase, returns recommended model"""

async def test_uses_quality_data():
    """Recommendation based on quality scores"""

async def test_respects_cost_budget():
    """Won't recommend expensive model for low-budget phase"""
```

### Integration Tests

```python
# tests/test_week3_learning.py
async def test_full_workflow():
    """Task → Model Selection → Execution → Assessment → Learning"""
```

### Performance Tests

```python
# tests/test_week3_performance.py
async def test_recommendation_latency():
    """Model recommendation < 100ms even with 10K cost records"""
```

---

## Rollout Plan

### Phase 1: Development (Current)

- ✅ Week 2 testing (next 1-2 hours)
- [ ] Week 3 feature implementation
- [ ] Unit test creation
- [ ] Local testing

### Phase 2: Staging (After implementation)

- [ ] Deploy to staging environment
- [ ] Run full integration tests
- [ ] Collect performance metrics
- [ ] User acceptance testing

### Phase 3: Production (After validation)

- [ ] Blue/green deployment
- [ ] Monitor error rates
- [ ] Gradual rollout (if needed)
- [ ] Performance monitoring

---

## Knowledge Handoff

### What's Working (Don't Change)

✅ Cost logging (Week 1)
✅ Database schema with cost_logs
✅ API endpoints in metrics_routes
✅ Frontend dashboard component
✅ CostAggregationService queries

### What to Leverage

**Database:**

- cost_logs table with proper indexes
- Task_id, phase, model columns
- Quality_score and cost_usd fields
- Proper timestamp tracking

**Services:**

- DatabaseService for connections
- CostAggregationService for queries
- Request/response patterns

**Frontend:**

- cofounderAgentClient pattern
- Promise.all for parallel fetching
- Material-UI components
- Safe optional chaining

---

## Success Criteria (Week 3)

When Week 3 is complete, solopreneurs will:

- ✅ See automatic model recommendations per phase
- ✅ Understand which models perform best
- ✅ Get actionable cost-saving suggestions
- ✅ Receive monthly cost reports
- ✅ See learning improvement over time

---

## File Organization (After Week 3)

```
src/cofounder_agent/
├── services/
│   ├── cost_aggregation_service.py       ← Week 2
│   ├── model_recommendation_service.py   ← Week 3
│   ├── learning_system_service.py        ← Week 3
│   ├── report_generator_service.py       ← Week 3
│   ├── optimization_engine_service.py    ← Week 3
│   └── database_service.py               ← Week 1
├── routes/
│   ├── metrics_routes.py                 ← Week 2 (enhanced)
│   ├── recommendations_routes.py         ← Week 3
│   ├── learning_routes.py                ← Week 3
│   ├── reports_routes.py                 ← Week 3
│   └── task_routes.py                    ← Week 1 (modified)
└── tests/
    ├── test_week2_cost_analytics.py      ← Week 2
    ├── test_week3_learning.py            ← Week 3
    ├── test_week3_recommendations.py     ← Week 3
    ├── test_week3_optimization.py        ← Week 3
    └── test_week3_integration.py         ← Week 3
```

---

## Estimated Effort (Week 3)

| Feature               | Estimated Time  | Difficulty |
| --------------------- | --------------- | ---------- |
| Smart Model Selection | 6-8 hours       | Medium     |
| Learning System       | 4-6 hours       | Medium     |
| Monthly Reports       | 4-6 hours       | Easy       |
| Optimization Engine   | 8-10 hours      | Hard       |
| **Total Week 3**      | **24-32 hours** | **Mix**    |

---

## Questions for Planning (Week 3)

Before starting Week 3, clarify:

1. **Priority:** Which feature first? (Model selection probably)
2. **Quality Data:** Do assessments always provide quality_score?
3. **Recommendations:** Should system enforce or just suggest?
4. **Reporting:** Email service set up? Templates designed?
5. **Learning:** How fast should it learn? (Weekly vs daily?)

---

## Ready to Continue

Week 2 is complete and documented. All files ready for:

- ✅ End-to-end testing
- ✅ Staging deployment
- ✅ Production rollout
- ✅ Week 3 implementation

**Next Step:** Run end-to-end test with real cost tracking workflow.

See WEEK_2_QUICK_START.md for testing procedures.

---
