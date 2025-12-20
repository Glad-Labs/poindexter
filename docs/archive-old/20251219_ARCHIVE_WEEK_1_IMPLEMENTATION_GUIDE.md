# Week 1: Cost Tracking Foundation - Implementation Guide

**Status:** Ready for Implementation  
**Timeline:** Days 1-7  
**Goal:** Per-step model selection + transparent cost logging

---

## EXISTING INFRASTRUCTURE INVENTORY

✅ **ALREADY IMPLEMENTED:**

- `CostTrackingService` - Budget alerts, monthly tracking, projections ($100/month budget)
- `ModelRouter` - Smart model selection, cost-aware routing, token limits
- `UsageTracker` - Token/cost tracking per operation, model pricing database
- `metrics_routes.py` - GET `/api/metrics/costs` endpoint (cost breakdown)
- Database migration system (MigrationService)
- Cost calculation framework

**What's Missing:**

- ❌ Per-step (phase) model selection UI
- ❌ Task-level cost aggregation (individual task costs)
- ❌ Cost database table (`cost_logs`) for per-task granularity
- ❌ "Auto-select" logic for model choice
- ❌ Cost transparency dashboard
- ❌ Integration with LangGraph pipeline

---

## WEEK 1 TASKS (PRIORITIZED)

### Task 1.1: Create `cost_logs` Table Migration (2 hours)

**File:** `src/cofounder_agent/migrations/002_cost_logs_table.sql`

```sql
-- Create cost_logs table for per-API-call tracking
CREATE TABLE IF NOT EXISTS cost_logs (
    id SERIAL PRIMARY KEY,
    task_id UUID NOT NULL,
    user_id UUID,
    phase VARCHAR(50) NOT NULL,          -- research, outline, draft, assess, refine, finalize
    model VARCHAR(100) NOT NULL,         -- ollama, gpt-3.5-turbo, gpt-4, claude-3-opus, etc.
    provider VARCHAR(50) NOT NULL,       -- ollama, openai, anthropic, google

    -- Token tracking
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    total_tokens INT DEFAULT 0,

    -- Cost tracking
    cost_usd DECIMAL(10, 6),             -- Cost in USD ($0.000001 precision)

    -- Metadata
    quality_score FLOAT,                 -- Optional: 1-5 star rating
    duration_ms INT,                     -- Execution time in milliseconds
    success BOOLEAN DEFAULT TRUE,        -- Whether call succeeded
    error_message TEXT,                  -- Error details if failed

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX idx_cost_logs_task_id ON cost_logs(task_id);
CREATE INDEX idx_cost_logs_user_id ON cost_logs(user_id);
CREATE INDEX idx_cost_logs_created_at ON cost_logs(created_at);
CREATE INDEX idx_cost_logs_provider ON cost_logs(provider);
CREATE INDEX idx_cost_logs_model ON cost_logs(model);
CREATE INDEX idx_cost_logs_phase ON cost_logs(phase);

-- Composite index for cost aggregation queries
CREATE INDEX idx_cost_logs_user_date ON cost_logs(user_id, created_at);
```

**Why this table structure:**

- `task_id` - Links to specific content task
- `user_id` - For multi-tenant tracking (future SaaS feature)
- `phase` - Shows which pipeline step used which model
- `provider/model` - Tracks which AI provider was used
- `tokens` - For debugging and cost verification
- `cost_usd` - Exact cost of that API call
- Indexes - Fast queries for dashboards

---

### Task 1.2: Create `ModelSelector` Service (3 hours)

**File:** `src/cofounder_agent/services/model_selector_service.py`

**Purpose:** Per-step model selection + auto-selection logic

```python
"""
Model Selection Service

Provides per-step model selection for LangGraph pipeline.
Allows users to choose specific models or use auto-selection.

Features:
- Per-phase model selection (research, outline, draft, assess, refine, finalize)
- Auto-selection based on quality preference
- Cost estimation before execution
- Model availability checking
"""

from typing import Dict, List, Optional, Tuple
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class QualityPreference(str, Enum):
    """User's quality vs cost preference"""
    FAST = "fast"           # Cheapest models (Ollama)
    BALANCED = "balanced"   # Mix of cost and quality
    QUALITY = "quality"     # Best models (GPT-4, Claude Opus)


class ModelSelector:
    """
    Intelligent per-step model selection for content pipeline.

    Rules:
    - RESEARCH: Can use Ollama/GPT-3.5 (gathering info, cost matters)
    - OUTLINE: Can use Ollama/GPT-3.5 (structure, decent quality)
    - DRAFT: Prefers GPT-3.5/4 (writing quality important)
    - ASSESS: Must use GPT-4/Claude (quality evaluation critical)
    - REFINE: Should use GPT-4 (improve existing content)
    - FINALIZE: Should use GPT-4 (final polish, high stakes)
    """

    # Per-phase model options (cheapest → best quality)
    PHASE_MODELS = {
        "research": ["ollama", "gpt-3.5-turbo", "gpt-4"],
        "outline": ["ollama", "gpt-3.5-turbo", "gpt-4"],
        "draft": ["gpt-3.5-turbo", "gpt-4", "claude-3-opus"],
        "assess": ["gpt-4", "claude-3-opus"],        # Quality critical
        "refine": ["gpt-4", "claude-3-opus"],        # Important
        "finalize": ["gpt-4", "claude-3-opus"],      # Final output
    }

    # Token counts per phase (used for cost estimation)
    PHASE_TOKEN_ESTIMATES = {
        "research": 2000,    # Research produces ~2K tokens
        "outline": 1500,     # Outline produces ~1.5K tokens
        "draft": 3000,       # Draft produces ~3K tokens
        "assess": 500,       # Assessment produces ~500 tokens
        "refine": 2000,      # Refine produces ~2K tokens
        "finalize": 1000,    # Final polish produces ~1K tokens
    }

    # Pricing per 1K tokens (from UsageTracker)
    MODEL_COSTS = {
        "ollama": 0.0,                              # Free local
        "gpt-3.5-turbo": 0.0005,                   # $0.0005 per 1K input
        "gpt-4": 0.003,                             # $0.03 per 1K input (simplified)
        "claude-3-opus": 0.015,                    # $0.015 per 1K input
        "claude-3-sonnet": 0.003,
        "claude-3-haiku": 0.00025,
    }

    def __init__(self):
        """Initialize model selector"""
        logger.info("ModelSelector initialized")

    def auto_select(self, phase: str, quality: QualityPreference) -> str:
        """
        Auto-select best model for phase + quality combo.

        Args:
            phase: Pipeline phase (research, outline, draft, assess, refine, finalize)
            quality: User's quality preference (fast, balanced, quality)

        Returns:
            Model name (e.g., "ollama", "gpt-3.5-turbo", "gpt-4")
        """
        if phase not in self.PHASE_MODELS:
            logger.warning(f"Unknown phase: {phase}, defaulting to gpt-3.5-turbo")
            return "gpt-3.5-turbo"

        available_models = self.PHASE_MODELS[phase]

        if quality == QualityPreference.FAST:
            # Use cheapest available
            return available_models[0]
        elif quality == QualityPreference.BALANCED:
            # Use middle option
            mid_idx = len(available_models) // 2
            return available_models[mid_idx] if mid_idx < len(available_models) else available_models[-1]
        else:  # QUALITY
            # Use best available
            return available_models[-1]

    def estimate_cost(self, phase: str, model: str) -> float:
        """
        Estimate cost of using model for phase.

        Args:
            phase: Pipeline phase
            model: Model name

        Returns:
            Estimated cost in USD
        """
        if phase not in self.PHASE_TOKEN_ESTIMATES:
            logger.warning(f"Unknown phase: {phase}")
            return 0.0

        if model not in self.MODEL_COSTS:
            logger.warning(f"Unknown model: {model}, assuming $0")
            return 0.0

        tokens = self.PHASE_TOKEN_ESTIMATES[phase]
        cost_per_1k = self.MODEL_COSTS[model]
        total_cost = (tokens / 1000.0) * cost_per_1k

        return round(total_cost, 6)  # 6 decimal places for precision

    def estimate_full_task_cost(self, models_by_phase: Dict[str, str]) -> Dict[str, float]:
        """
        Estimate total cost of task with given model selections.

        Args:
            models_by_phase: {"research": "ollama", "outline": "gpt-3.5-turbo", ...}

        Returns:
            {"research": 0.0, "outline": 0.0075, ..., "total": 0.0175}
        """
        cost_breakdown = {}
        total_cost = 0.0

        for phase, model in models_by_phase.items():
            cost = self.estimate_cost(phase, model)
            cost_breakdown[phase] = cost
            total_cost += cost

        cost_breakdown["total"] = round(total_cost, 6)

        return cost_breakdown

    def get_available_models(self, phase: Optional[str] = None) -> Dict[str, List[str]]:
        """
        Get available models for a phase (or all phases).

        Args:
            phase: Optional specific phase

        Returns:
            Models available for selection
        """
        if phase:
            return {"models": self.PHASE_MODELS.get(phase, [])}
        else:
            return {"models": self.PHASE_MODELS}

    def validate_model_selection(self, phase: str, model: str) -> Tuple[bool, str]:
        """
        Validate that model is available for phase.

        Args:
            phase: Pipeline phase
            model: Model name to validate

        Returns:
            (is_valid, message)
        """
        if phase not in self.PHASE_MODELS:
            return False, f"Unknown phase: {phase}"

        available = self.PHASE_MODELS[phase]
        if model not in available:
            return False, f"Model {model} not available for {phase}. Available: {available}"

        return True, "OK"
```

---

### Task 1.3: Integrate Cost Logging into Pipeline (4 hours)

**File:** `src/cofounder_agent/services/langgraph_graphs/content_pipeline.py`

**Changes needed:**

1. Add `cost_logs` table logging after each phase
2. Track which model was used for each phase
3. Calculate actual tokens and costs
4. Store in database

```python
# IN content_pipeline.py, UPDATE THE STATE AND EXECUTION:

from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class ContentPipelineState(TypedDict):
    """State for content generation pipeline"""
    # ... existing fields ...

    # NEW: Model selection per phase
    models_by_phase: Dict[str, str] = field(default_factory=lambda: {
        "research": "auto",
        "outline": "auto",
        "draft": "auto",
        "assess": "auto",
        "refine": "auto",
        "finalize": "auto",
    })

    # NEW: Quality preference
    quality_preference: str = "balanced"  # fast, balanced, quality

    # NEW: Cost tracking
    cost_breakdown: Dict[str, float] = field(default_factory=dict)
    total_cost: float = 0.0

    # NEW: Models actually used (after auto-selection)
    resolved_models: Dict[str, str] = field(default_factory=dict)


# IN execute_content_pipeline():
async def execute_content_pipeline(
    state: ContentPipelineState,
    database_service: DatabaseService,
    model_selector: ModelSelector
) -> ContentPipelineState:
    """Execute full pipeline with cost tracking"""

    # Add this import at top
    from services.model_selector_service import ModelSelector

    # Initialize model selector
    if not hasattr(state, 'model_selector'):
        state.model_selector = ModelSelector()

    # Resolve auto-selections to actual models
    for phase in ["research", "outline", "draft", "assess", "refine", "finalize"]:
        selected = state.models_by_phase.get(phase, "auto")

        if selected == "auto":
            # Auto-select based on quality preference
            model = state.model_selector.auto_select(
                phase,
                state.quality_preference
            )
        else:
            model = selected

        state.resolved_models[phase] = model

    # Execute each phase with cost logging
    for phase in ["research", "outline", "draft", "assess", "refine", "finalize"]:
        model = state.resolved_models[phase]

        # Execute phase (existing code)
        if phase == "research":
            state["research_output"] = await research_phase(state["topic"], model=model)
        elif phase == "outline":
            state["outline_output"] = await outline_phase(state["research_output"], model=model)
        # ... etc for other phases ...

        # LOG COST TO DATABASE
        cost = state.model_selector.estimate_cost(phase, model)
        state.cost_breakdown[phase] = cost
        state.total_cost += cost

        # Record in database
        try:
            await database_service.execute(
                """
                INSERT INTO cost_logs
                (task_id, user_id, phase, model, provider, cost_usd, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                state.task_id,
                state.user_id,
                phase,
                model,
                get_provider(model),  # helper function
                cost,
                datetime.utcnow()
            )
        except Exception as e:
            logger.error(f"Failed to log cost for {phase}: {e}")

    return state


def get_provider(model: str) -> str:
    """Get provider from model name"""
    if "ollama" in model.lower():
        return "ollama"
    elif "gpt" in model.lower():
        return "openai"
    elif "claude" in model.lower():
        return "anthropic"
    elif "gemini" in model.lower():
        return "google"
    return "unknown"
```

---

### Task 1.4: Create Model Selection API Routes (2 hours)

**File:** `src/cofounder_agent/routes/model_selection_routes.py`

```python
"""
Model Selection Routes

Endpoints for selecting models per pipeline phase.
Allows users to manually choose or auto-select models.
Shows cost estimates before execution.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelSelection(BaseModel):
    """User's model selection for a task"""
    research: str = "auto"          # Model choice or "auto"
    outline: str = "auto"
    draft: str = "auto"
    assess: str = "auto"
    refine: str = "auto"
    finalize: str = "auto"
    quality_preference: str = "balanced"  # fast, balanced, quality


class CostEstimate(BaseModel):
    """Cost estimate for model selection"""
    phase: str
    model: str
    estimated_tokens: int
    estimated_cost: float


class FullTaskCostEstimate(BaseModel):
    """Full task cost estimate"""
    by_phase: Dict[str, float]
    total_cost: float
    budget_remaining: float
    within_budget: bool


# Initialize model selector
from services.model_selector_service import ModelSelector
model_selector = ModelSelector()


@router.post("/estimate-cost")
async def estimate_phase_cost(
    phase: str,
    model: str
) -> CostEstimate:
    """
    Estimate cost of using a specific model for a phase.

    Examples:
    - POST /api/models/estimate-cost?phase=research&model=ollama
      → {phase: "research", model: "ollama", estimated_cost: 0.0}

    - POST /api/models/estimate-cost?phase=draft&model=gpt-4
      → {phase: "draft", model: "gpt-4", estimated_cost: 0.009}
    """
    cost = model_selector.estimate_cost(phase, model)
    tokens = model_selector.PHASE_TOKEN_ESTIMATES.get(phase, 500)

    return CostEstimate(
        phase=phase,
        model=model,
        estimated_tokens=tokens,
        estimated_cost=cost
    )


@router.post("/estimate-full-task")
async def estimate_full_task(
    selection: ModelSelection
) -> FullTaskCostEstimate:
    """
    Estimate total cost of task with given model selections.

    Example:
    POST /api/models/estimate-full-task
    {
        "research": "ollama",
        "outline": "ollama",
        "draft": "gpt-3.5-turbo",
        "assess": "gpt-4",
        "refine": "gpt-4",
        "finalize": "gpt-4",
        "quality_preference": "balanced"
    }

    Response:
    {
        "by_phase": {
            "research": 0.0,
            "outline": 0.0,
            "draft": 0.00075,
            "assess": 0.001,
            "refine": 0.001,
            "finalize": 0.001,
            "total": 0.00375
        },
        "total_cost": 0.00375,
        "budget_remaining": 149.99625,
        "within_budget": true
    }
    """
    # Build models dict from selection
    models_dict = {
        "research": selection.research,
        "outline": selection.outline,
        "draft": selection.draft,
        "assess": selection.assess,
        "refine": selection.refine,
        "finalize": selection.finalize,
    }

    # Get cost estimate
    cost_breakdown = model_selector.estimate_full_task_cost(models_dict)
    total_cost = cost_breakdown.pop("total")

    # Check against budget ($150/month for solopreneurs)
    budget = 150.0
    budget_remaining = budget - total_cost
    within_budget = total_cost <= budget

    return FullTaskCostEstimate(
        by_phase=cost_breakdown,
        total_cost=total_cost,
        budget_remaining=budget_remaining,
        within_budget=within_budget
    )


@router.post("/auto-select")
async def auto_select_models(
    quality_preference: str = "balanced"
) -> Dict[str, any]:
    """
    Auto-select models for all phases based on quality preference.

    Query params:
    - quality_preference: "fast" | "balanced" | "quality"

    Example:
    POST /api/models/auto-select?quality_preference=balanced

    Response:
    {
        "quality_preference": "balanced",
        "selected_models": {
            "research": "gpt-3.5-turbo",
            "outline": "gpt-3.5-turbo",
            "draft": "gpt-3.5-turbo",
            "assess": "gpt-4",
            "refine": "gpt-4",
            "finalize": "gpt-4"
        },
        "estimated_total_cost": 0.00375,
        "budget_status": "✅ On track"
    }
    """
    from services.model_selector_service import QualityPreference

    try:
        quality = QualityPreference(quality_preference)
    except ValueError:
        return {
            "error": f"Invalid quality preference. Must be: fast, balanced, quality"
        }

    # Auto-select for each phase
    selected = {}
    for phase in ["research", "outline", "draft", "assess", "refine", "finalize"]:
        model = model_selector.auto_select(phase, quality)
        selected[phase] = model

    # Calculate cost
    cost_breakdown = model_selector.estimate_full_task_cost(selected)
    total_cost = cost_breakdown.pop("total")

    budget_remaining = 150 - total_cost
    budget_status = "✅ On track" if total_cost < 150 else "⚠️ Check budget"

    return {
        "quality_preference": quality_preference,
        "selected_models": selected,
        "cost_breakdown": cost_breakdown,
        "estimated_total_cost": total_cost,
        "budget_remaining": budget_remaining,
        "budget_status": budget_status
    }


@router.get("/available-models")
async def get_available_models(phase: Optional[str] = None):
    """
    Get available models for selection.

    Query params:
    - phase: Optional specific phase (research, outline, draft, assess, refine, finalize)

    Examples:

    GET /api/models/available-models
    → All models for all phases

    GET /api/models/available-models?phase=research
    → {models: ["ollama", "gpt-3.5-turbo", "gpt-4"]}
    """
    return model_selector.get_available_models(phase)


@router.post("/validate-selection")
async def validate_selection(
    phase: str,
    model: str
) -> Dict[str, any]:
    """
    Validate that a model is available for a phase.

    Returns:
    {
        "valid": true/false,
        "message": "OK or error message"
    }
    """
    is_valid, message = model_selector.validate_model_selection(phase, model)
    return {
        "valid": is_valid,
        "message": message,
        "phase": phase,
        "model": model
    }
```

---

### Task 1.5: Add Routes to Main FastAPI App (1 hour)

**File:** `src/cofounder_agent/main.py`

**Add to app initialization:**

```python
# In main.py, add this import:
from routes.model_selection_routes import router as model_selection_router

# In app setup (where other routers are included), add:
app.include_router(model_selection_router)
```

---

### Task 1.6: Add Cost Logging to Content Routes (2 hours)

**File:** `src/cofounder_agent/routes/content_routes.py`

**Update task creation to accept model selection:**

```python
# IN CreateBlogPostRequest model:
from typing import Optional, Dict

class CreateBlogPostRequest(BaseModel):
    topic: str
    # ... existing fields ...

    # NEW: Model selection per phase
    models_by_phase: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional: Choose specific models for each phase",
        example={
            "research": "ollama",
            "outline": "ollama",
            "draft": "gpt-3.5-turbo",
            "assess": "gpt-4",
            "refine": "gpt-4",
            "finalize": "gpt-4"
        }
    )

    quality_preference: Optional[str] = Field(
        default="balanced",
        description="Auto-select preference if models_by_phase not provided",
        example="balanced"  # or "fast" or "quality"
    )


# IN create_blog_post route:
async def create_blog_post(request: CreateBlogPostRequest):
    # ... existing code ...

    # NEW: Prepare model selection for pipeline
    pipeline_state = {
        # ... existing state ...
        "models_by_phase": request.models_by_phase or {
            "research": "auto",
            "outline": "auto",
            "draft": "auto",
            "assess": "auto",
            "refine": "auto",
            "finalize": "auto"
        },
        "quality_preference": request.quality_preference or "balanced",
    }

    # Execute pipeline (which will log costs)
    result = await langgraph_orchestrator.execute_content_pipeline(pipeline_state)

    return {
        "status": "success",
        "task_id": result.task_id,
        "cost_breakdown": result.cost_breakdown,
        "total_cost": result.total_cost,
        # ... other fields ...
    }
```

---

## IMPLEMENTATION CHECKLIST

### Day 1: Database & Service Setup

- [ ] Create migration file: `002_cost_logs_table.sql`
- [ ] Run migration: `python run_migration.py`
- [ ] Create `model_selector_service.py`
- [ ] Test `ModelSelector` class with sample inputs

### Day 2: API Routes

- [ ] Create `model_selection_routes.py` with 5 endpoints
- [ ] Add route to `main.py`
- [ ] Test endpoints with curl/Postman:

  ```bash
  # Test cost estimate
  curl -X POST "http://localhost:8000/api/models/estimate-cost?phase=research&model=ollama"

  # Test full task estimate
  curl -X POST "http://localhost:8000/api/models/estimate-full-task" \
    -H "Content-Type: application/json" \
    -d '{"research": "ollama", "outline": "ollama", "draft": "gpt-3.5-turbo", "assess": "gpt-4", "refine": "gpt-4", "finalize": "gpt-4"}'

  # Test auto-select
  curl -X POST "http://localhost:8000/api/models/auto-select?quality_preference=balanced"
  ```

### Day 3-4: Pipeline Integration

- [ ] Update `content_pipeline.py` to add cost tracking
- [ ] Add model selection fields to pipeline state
- [ ] Wire cost logging to database
- [ ] Test with real task execution

### Day 5-6: Content Routes Integration

- [ ] Update `CreateBlogPostRequest` with model selection fields
- [ ] Update task creation endpoint to accept models
- [ ] Pass models to pipeline
- [ ] Return cost breakdown in response

### Day 7: Testing & Polish

- [ ] End-to-end test:
  1. Create task with specific models
  2. Verify costs logged to database
  3. Check `/api/metrics/costs` shows the costs
  4. Verify budget calculations work
- [ ] Verify no duplicate logic:
  - ✅ `UsageTracker` for operation-level costs
  - ✅ `cost_logs` table for task-level costs
  - ✅ `CostTrackingService` for budget alerts
  - ✅ `ModelRouter` for intelligent selection
- [ ] Documentation:
  - [ ] Update API docs with new endpoints
  - [ ] Add examples to each route
  - [ ] Document model selection flow

---

## VERIFICATION TESTS

### Test 1: Cost Estimation Accuracy

```python
async def test_cost_estimation():
    selector = ModelSelector()

    # Ollama should be free
    assert selector.estimate_cost("research", "ollama") == 0.0

    # GPT-4 draft phase: 3000 tokens * $0.003/1K = $0.009
    assert selector.estimate_cost("draft", "gpt-4") == 0.009

    # Full task with Ollama everywhere
    full = selector.estimate_full_task_cost({
        "research": "ollama",
        "outline": "ollama",
        "draft": "ollama",
        "assess": "ollama",
        "refine": "ollama",
        "finalize": "ollama"
    })
    assert full["total"] == 0.0  # Should be free
```

### Test 2: Database Logging

```python
async def test_cost_logging(database_service):
    # Create a task with costs
    await database_service.execute(
        """
        INSERT INTO cost_logs
        (task_id, user_id, phase, model, provider, cost_usd)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        "test-task-123",
        "user-123",
        "research",
        "ollama",
        "ollama",
        0.0
    )

    # Verify it was logged
    result = await database_service.fetchrow(
        "SELECT * FROM cost_logs WHERE task_id = $1",
        "test-task-123"
    )
    assert result is not None
    assert result["cost_usd"] == 0.0
```

### Test 3: API Endpoints

```python
# Test auto-select endpoint
response = await client.post(
    "/api/models/auto-select?quality_preference=balanced"
)
assert response.status_code == 200
assert response.json()["selected_models"]["research"] == "gpt-3.5-turbo"
assert response.json()["estimated_total_cost"] > 0

# Test full task estimate
response = await client.post(
    "/api/models/estimate-full-task",
    json={
        "research": "ollama",
        "outline": "ollama",
        "draft": "gpt-3.5-turbo",
        "assess": "gpt-4",
        "refine": "gpt-4",
        "finalize": "gpt-4",
    }
)
assert response.status_code == 200
assert response.json()["total_cost"] == 0.00375
```

---

## SUCCESS CRITERIA

✅ **Week 1 Complete When:**

1. ✅ `cost_logs` table created and indexed
2. ✅ `ModelSelector` service working (all methods tested)
3. ✅ 5 API endpoints responding correctly
4. ✅ Pipeline logs costs to database after each phase
5. ✅ Cost estimation matches actual costs within 10%
6. ✅ "Auto-select" chooses reasonable models
7. ✅ Task creation accepts model selections
8. ✅ All tests passing
9. ✅ No regressions in existing features

**Result:** Users can see exact cost per post ($0-0.05 depending on models)

---

## NEXT STEPS (Week 2+)

Once Week 1 is complete, Week 2 will add:

- Cost transparency dashboard component
- Per-model cost breakdown UI
- Budget tracking on task detail page
- Cost vs quality comparison view

See `IMPLEMENTATION_ROADMAP_YOUR_VISION.md` for full 6-week plan.
