# Phase 3 Task 1 - Quick Start Guide

**Status:** 🚀 **READY TO BEGIN**  
**Date:** October 30, 2025  
**Estimated Duration:** 2 weeks (Weeks 1-2 of Phase 3)  
**Target:** 15+ tests, zero lint errors, production-ready model selection

---

## 🎯 One-Sentence Goal

**Enable agents to intelligently select optimal AI models based on task requirements, performance history, cost, and speed constraints.**

---

## 📋 Quick Overview

### What We're Building

A **ModelSelector service** that acts as a smart broker between agents and AI models. Instead of hardcoding "use GPT-4 for everything", agents will:

1. **Describe their task** (e.g., "I need to write a blog post")
2. **Specify requirements** (accuracy: 70, creativity: 85, speed: 50, cost limit: $0.05)
3. **Get optimal model recommended** based on:
   - Model capabilities (accuracy, speed, cost)
   - Performance history (how well this model performed on similar tasks before)
   - Available models and providers (fallback chain from consolidation service)
   - Selected strategy (balanced, accuracy-first, speed-first, cost-first, history-first)

### Why This Matters

- **Cost Optimization:** Don't use expensive GPT-4 when Mistral works fine for simple tasks
- **Performance Improvement:** Learn from past executions and improve model selection
- **Flexibility:** Different strategies for different scenarios
- **Scalability:** Foundation for future agent specialization

---

## 🚀 Phase 3 Task 1 Implementation Plan

### Step 1: Create Task Types & Requirements (Days 1-2)

**File:** `src/cofounder_agent/models/task_types.py` (~100 lines)

```python
# What to implement:
# 1. AgentType enum (CONTENT, FINANCIAL, MARKET_INSIGHT, COMPLIANCE)
# 2. ContentTaskType enum (BLOG, SOCIAL_MEDIA, EMAIL, SEO, SUMMARY)
# 3. FinancialTaskType enum (COST_ANALYSIS, PROJECTION, BUDGET, REVENUE)
# 4. MarketTaskType enum (TRENDS, COMPETITORS, OPPORTUNITY, AUDIENCE)
# 5. ComplianceTaskType enum (MODERATION, GDPR, RISK, POLICY)
# 6. TaskRequirements dataclass with:
#    - accuracy_required (0-100)
#    - creativity_required (0-100)
#    - speed_required (0-100)
#    - context_length_required (tokens)
#    - requires_local_execution (bool)
#    - max_cost_per_call (float or None)
#    - output_length_preference ("short", "medium", "long")
#    - custom_requirements (dict)
# 7. TASK_REQUIREMENTS_MAP: Map TaskType → TaskRequirements
```

**Test:** `src/cofounder_agent/tests/test_task_types.py` (2 tests)

```python
def test_content_task_types():
    """Verify all content task types are defined"""

def test_task_requirements_mapping():
    """Verify requirements map is complete"""
```

### Step 2: Create Model Scoring System (Days 3-4)

**Files:**

- `src/cofounder_agent/models/model_scoring.py` (~150 lines)
- `src/cofounder_agent/models/performance_models.py` (~100 lines)

```python
# model_scoring.py:
# 1. ModelScore dataclass with:
#    - accuracy_score (0-100)
#    - speed_score (0-100)
#    - cost_score (0-100)
#    - suitability_score (0-100)
#    - performance_history_score (0-100)
#    - final_score property: weighted calculation → 0-1000
# 2. ScoringStrategy enum:
#    - BALANCED (default 30% accuracy, 15% speed, 15% cost, 40% history)
#    - ACCURACY_FIRST (50% accuracy, 25% history)
#    - SPEED_FIRST (50% speed, 25% history)
#    - COST_FIRST (50% cost, 25% history)
#    - HISTORY_FIRST (60% history)

# performance_models.py:
# 1. ExecutionMetric dataclass:
#    - model_name, provider, task_type
#    - success, response_time, tokens, cost, user_rating, timestamp
# 2. ModelPerformance dataclass:
#    - model_name, provider, task_type
#    - executions list
#    - Aggregate metrics: success_rate, avg_response_time, avg_cost
#    - Derived metrics: reliability_score, speed_score, cost_effectiveness
```

**Test:** `src/cofounder_agent/tests/test_model_scoring.py` (3 tests)

```python
async def test_model_score_calculation():
    """Weighted score calculation is correct"""

async def test_scoring_strategy_weights():
    """Scoring strategies apply correct weights"""

async def test_performance_metrics_aggregation():
    """Performance metrics are aggregated correctly"""
```

### Step 3: Create Model Selector Service (Days 5-7)

**File:** `src/cofounder_agent/services/model_selector.py` (~300 lines)

```python
# ModelSelector class methods:
# 1. select_model_for_task(task_type, requirements?, strategy?, prefer_provider?)
#    - Get available models from consolidation service
#    - Score each model using multi-factor algorithm
#    - Return top-scoring model
# 2. _score_model(model_name, provider, task_type, requirements, strategy)
#    - Calculate accuracy_score (from known model capabilities)
#    - Calculate speed_score (from known response times)
#    - Calculate cost_score (from known pricing)
#    - Get performance_history_score (from past executions)
#    - Calculate suitability (custom requirements matching)
#    - Apply strategy weights and return ModelScore
# 3. _calculate_accuracy_score() - Map model to known accuracy (GPT-4: 95, Claude: 93, etc.)
# 4. _calculate_speed_score() - Map model to known response time
# 5. _calculate_cost_score() - Map model to known cost per 1K tokens
# 6. _calculate_suitability() - Match custom requirements
# 7. record_execution() - Record execution result and update performance history
# 8. Global get_model_selector() singleton pattern
```

**Test:** `src/cofounder_agent/tests/test_model_selector.py` (8 tests)

```python
async def test_select_model_for_blog():
    """Basic model selection for blog generation"""

async def test_select_model_with_requirements():
    """Select model respecting custom requirements"""

async def test_select_model_with_cost_limit():
    """Cost constraint prevents expensive models"""

async def test_select_model_prefers_provider():
    """Provider preference is respected"""

async def test_select_model_applies_strategy():
    """Different strategies select different models"""

async def test_score_calculation_accuracy():
    """Scoring algorithm produces correct scores"""

async def test_performance_influences_selection():
    """Past performance affects model selection"""

async def test_no_suitable_model_raises_error():
    """ValueError if no model matches requirements"""
```

### Step 4: Create Performance Tracker (Day 8)

**File:** `src/cofounder_agent/services/performance_tracker.py` (~150 lines - optional, or integrate into ModelSelector)

```python
# Optional separate file or integrate into ModelSelector
# Responsibility: Manage performance history storage and retrieval
# Methods:
# 1. store_execution() - Persist execution metric
# 2. get_performance_history() - Retrieve performance for model+task
# 3. get_best_model_for_task() - Get top model based on history
```

### Step 5: Integration Tests (Days 9-10)

**File:** `src/cofounder_agent/tests/test_phase3_task1_integration.py` (5 tests)

```python
async def test_full_workflow_select_execute_record():
    """End-to-end: select model → use model → record performance → select again"""

async def test_consolidation_service_integration():
    """ModelSelector correctly uses consolidation service"""

async def test_multiple_tasks_different_models():
    """Different tasks select appropriate models"""

async def test_performance_improvement_over_time():
    """Model selection improves with more performance data"""

async def test_error_handling_graceful_degradation():
    """If preferred model unavailable, fallback works"""
```

---

## 📊 File Checklist

### New Files to Create (6 files, ~850 lines code + tests)

- [ ] `src/cofounder_agent/models/task_types.py` (100 lines)
- [ ] `src/cofounder_agent/models/model_scoring.py` (150 lines)
- [ ] `src/cofounder_agent/models/performance_models.py` (100 lines)
- [ ] `src/cofounder_agent/services/model_selector.py` (300 lines)
- [ ] `src/cofounder_agent/tests/test_task_types.py` (50 lines)
- [ ] `src/cofounder_agent/tests/test_model_selector.py` (200 lines)
- [ ] `src/cofounder_agent/tests/test_model_scoring.py` (100 lines)
- [ ] `src/cofounder_agent/tests/test_phase3_task1_integration.py` (150 lines)

### Files to Modify (Optional, for Week 2 integration)

- `src/cofounder_agent/services/model_selector.py` - Add `get_model_selector()` singleton
- Each agent file (`content_agent.py`, etc.) - Integrate ModelSelector

---

## ⏱️ Day-by-Day Timeline

| Day  | Task                                                | Target                      |
| ---- | --------------------------------------------------- | --------------------------- |
| 1-2  | Create task types and requirements enums            | 2 files, 200 lines, 2 tests |
| 3-4  | Create scoring model and performance tracking       | 2 files, 250 lines, 3 tests |
| 5-7  | Create ModelSelector service with scoring algorithm | 1 file, 300 lines, 8 tests  |
| 8    | Create performance tracker or integrate             | Done or integrated          |
| 9-10 | Integration tests and verification                  | 1 file, 150 lines, 5 tests  |
| 11   | Code review, fixes, documentation                   | Polish                      |
| 12   | Final testing, lint cleanup, deployment             | Ready for Phase 3 Task 2    |

---

## ✅ Success Criteria

- **Code:**
  - ✅ 850+ lines of production-ready code
  - ✅ Zero lint errors
  - ✅ All imports resolve
  - ✅ Type hints on all functions
  - ✅ Docstrings on all classes/methods

- **Tests:**
  - ✅ 15+ tests total
  - ✅ All tests passing (15/15)
  - ✅ 80%+ code coverage
  - ✅ Async tests working correctly
  - ✅ Edge cases covered

- **Functionality:**
  - ✅ ModelSelector can select optimal model for any task
  - ✅ Performance history influences selections
  - ✅ Strategies produce different results
  - ✅ Cost constraints respected
  - ✅ Fallback chain working

---

## 🚀 Ready to Start

### To begin Day 1

1. **Review:** Read full specification in `docs/PHASE_3_TASK_1_SPECIFICATION.md`
2. **Create:** Start with `src/cofounder_agent/models/task_types.py`
3. **Test:** Write tests in `src/cofounder_agent/tests/test_task_types.py`
4. **Verify:** Run `pytest tests/test_task_types.py -v` to confirm all pass

### Example Task Types to Start

```python
class ContentTaskType(str, Enum):
    BLOG_GENERATION = "blog_generation"
    SOCIAL_MEDIA = "social_media"
    EMAIL_CAMPAIGN = "email_campaign"

class FinancialTaskType(str, Enum):
    COST_ANALYSIS = "cost_analysis"
    FINANCIAL_PROJECTION = "financial_projection"
```

### First Test to Write

```python
def test_content_task_types():
    """Verify content task types are defined"""
    assert ContentTaskType.BLOG_GENERATION.value == "blog_generation"
    assert len(ContentTaskType) >= 5  # At least 5 content task types
```

---

## 📚 Reference Documentation

- **Full Spec:** `docs/PHASE_3_TASK_1_SPECIFICATION.md` (detailed architecture)
- **Phase 3 Plan:** `docs/PHASE_3_PLAN.md` (overall roadmap)
- **Phase 2 Completion:** `docs/PHASE_2_TASK_4_COMPLETION.md` (what we built before)
- **Consolidation Service:** `src/cofounder_agent/services/model_consolidation_service.py` (our dependency)

---

## 💡 Key Design Decisions

1. **Scoring Scale:** 0-1000 for fine granularity (vs 0-100)
2. **Weights:** 40% performance history is heaviest (trust past performance)
3. **Strategy Pattern:** Allow different scoring strategies for different scenarios
4. **Singleton Pattern:** `get_model_selector()` for global access
5. **Performance Cache:** In-memory dict for fast lookups (persist in Phase 4 if needed)
6. **Provider Mapping:** Hardcoded scores (can be learned in Phase 4)

---

## 🔗 Integration with Other Components

### Week 1 (Standalone)

- ModelSelector works independently
- Tests use mock consolidation service data
- No agent integration yet

### Week 2 (Integration Phase)

- Integrate with each agent type
- Each agent calls `select_model_for_task()`
- Performance recorded via `record_execution()`
- Will loop back to ModelSelector for better selections next time

---

## 🎓 What You'll Learn

- Multi-factor scoring algorithms
- Async/await patterns in Python
- Dataclass usage and composition
- Enum patterns for task types
- Weighted scoring implementations
- Performance tracking patterns
- Factory/Singleton patterns

---

**🚀 Ready to begin Phase 3 Task 1!**

Next step: **Create `src/cofounder_agent/models/task_types.py`** with all TaskType enums and TaskRequirements dataclass.
