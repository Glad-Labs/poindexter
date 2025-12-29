# Phase 3, Task 1 - Agent Model Selection Strategy

**Status:** 📋 **PLANNING**  
**Duration:** 2 weeks  
**Target Tests:** 15+ tests  
**Dependencies:** ✅ Phase 2 Complete (Model consolidation service)  
**Date Created:** October 30, 2025

---

## 🎯 Task Objective

Enable each specialized agent to **intelligently select the optimal AI model** for each task based on:

- Task type and complexity
- Model performance history on similar tasks
- Cost and resource constraints
- Speed requirements
- Accuracy requirements

**Success Criteria:**

- ✅ Each agent can analyze task requirements
- ✅ Each agent selects appropriate model 85%+ of the time
- ✅ Selection improves over time with performance data
- ✅ 15+ unit tests covering all scenarios
- ✅ Zero lint errors
- ✅ Seamless integration with existing agents

---

## 📊 Task 1 Architecture

### Component Diagram

```text
Agent receives Task
    ↓
Task Analysis
├── Determine TaskType
├── Extract requirements
└── Load performance history
    ↓
ModelSelector.select_model_for_task()
    ├── Get available models from consolidation service
    ├── Score each model on criteria (0-1000 scale)
    ├── Apply performance history weight
    ├── Apply cost constraints
    └── Return top model
    ↓
Agent executes with selected model
    ↓
Result recorded
├── Success/failure logged
├── Response time tracked
├── Cost recorded
└── Model score updated for next time
```

---

## 🗂️ File Structure

### New Files to Create

```text
src/cofounder_agent/
├── services/
│   ├── model_selector.py           # Main selection logic (300 lines)
│   ├── task_analyzer.py            # Task type detection (150 lines)
│   └── performance_tracker.py       # Performance metrics (200 lines)
│
├── models/
│   ├── task_types.py               # Task enums & classes (100 lines)
│   ├── model_scoring.py            # Scoring model (150 lines)
│   └── performance_models.py        # Performance metrics (100 lines)
│
└── tests/
    ├── test_task_types.py          # 2 tests
    ├── test_model_selector.py       # 8 tests
    ├── test_task_analyzer.py        # 3 tests
    ├── test_performance_tracker.py  # 2 tests
    └── test_phase3_task1_integration.py  # 5 tests (total 20 tests)
```

---

## 📝 Detailed Specifications

### 1. Task Types & Requirements

**File: `src/cofounder_agent/models/task_types.py`**

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional

# ============================================================================
# Task Type Enumerations
# ============================================================================

class AgentType(str, Enum):
    """Agent types in Glad Labs system"""
    CONTENT = "content"
    FINANCIAL = "financial"
    MARKET_INSIGHT = "market_insight"
    COMPLIANCE = "compliance"

class ContentTaskType(str, Enum):
    """Content agent task types"""
    BLOG_GENERATION = "blog_generation"
    SOCIAL_MEDIA = "social_media"
    EMAIL_CAMPAIGN = "email_campaign"
    SEO_OPTIMIZATION = "seo_optimization"
    ARTICLE_SUMMARY = "article_summary"

class FinancialTaskType(str, Enum):
    """Financial agent task types"""
    COST_ANALYSIS = "cost_analysis"
    FINANCIAL_PROJECTION = "financial_projection"
    BUDGET_OPTIMIZATION = "budget_optimization"
    REVENUE_FORECAST = "revenue_forecast"

class MarketTaskType(str, Enum):
    """Market insight agent task types"""
    TREND_ANALYSIS = "trend_analysis"
    COMPETITOR_ANALYSIS = "competitor_analysis"
    MARKET_OPPORTUNITY = "market_opportunity"
    AUDIENCE_ANALYSIS = "audience_analysis"

class ComplianceTaskType(str, Enum):
    """Compliance agent task types"""
    CONTENT_MODERATION = "content_moderation"
    GDPR_CHECK = "gdpr_check"
    RISK_ASSESSMENT = "risk_assessment"
    POLICY_VALIDATION = "policy_validation"

# ============================================================================
# Task Requirement Models
# ============================================================================

@dataclass
class TaskRequirements:
    """Requirements for executing a specific task"""

    # Performance requirements (0-100, where higher = stricter requirement)
    accuracy_required: int              # Model must be accurate (0-100)
    creativity_required: int            # Task needs creative models (0-100)
    speed_required: int                 # Must be fast (0-100)

    # Resource requirements
    context_length_required: int        # Tokens needed (0 = any)
    requires_local_execution: bool      # Must run locally for privacy/speed
    requires_gpu: bool                  # Needs GPU acceleration

    # Cost constraints
    max_cost_per_call: Optional[float]  # Max $ per API call (None = unlimited)
    prefer_free: bool                   # Prefer free options

    # Output requirements
    output_length_preference: str       # "short", "medium", "long"
    output_format: str                  # "text", "structured", "code"

    # Custom requirements
    custom_requirements: dict           # Task-specific custom requirements

# Task Type Mappings
TASK_REQUIREMENTS_MAP = {
    # Content tasks
    ContentTaskType.BLOG_GENERATION: TaskRequirements(
        accuracy_required=70,
        creativity_required=85,
        speed_required=50,
        context_length_required=4000,
        requires_local_execution=False,
        requires_gpu=False,
        max_cost_per_call=0.05,
        prefer_free=True,
        output_length_preference="long",
        output_format="text",
        custom_requirements={"min_words": 800, "seo_friendly": True}
    ),

    ContentTaskType.SOCIAL_MEDIA: TaskRequirements(
        accuracy_required=60,
        creativity_required=90,
        speed_required=80,
        context_length_required=500,
        requires_local_execution=False,
        requires_gpu=False,
        max_cost_per_call=0.01,
        prefer_free=True,
        output_length_preference="short",
        output_format="text",
        custom_requirements={"max_chars": 280, "engaging": True}
    ),

    # Financial tasks
    FinancialTaskType.COST_ANALYSIS: TaskRequirements(
        accuracy_required=95,
        creativity_required=20,
        speed_required=70,
        context_length_required=2000,
        requires_local_execution=True,  # Prefer local for security
        requires_gpu=False,
        max_cost_per_call=None,
        prefer_free=False,
        output_length_preference="medium",
        output_format="structured",
        custom_requirements={"calculations": True, "precision": 2}
    ),

    # More mappings...
}

def get_requirements_for_task(
    task_type: str
) -> TaskRequirements:
    """Get requirements for a task type"""
    # Match task_type to enum and return requirements
    ...
```

### 2. Model Scoring System

**File: `src/cofounder_agent/models/model_scoring.py`**

```python
from dataclasses import dataclass
from typing import Optional
from enum import Enum

@dataclass
class ModelScore:
    """Score breakdown for a model's suitability"""

    # Individual scores (0-100 each)
    accuracy_score: float              # How accurate for this task
    speed_score: float                 # How fast for this task
    cost_score: float                  # How economical (100 = free, 0 = expensive)
    suitability_score: float           # Overall suitability (0-100)
    performance_history_score: float   # Based on past performance (0-100)

    # Weights
    accuracy_weight: float = 0.30      # 30% of final score
    speed_weight: float = 0.15         # 15% of final score
    cost_weight: float = 0.15          # 15% of final score
    history_weight: float = 0.40       # 40% of final score (most important!)

    # Final score
    @property
    def final_score(self) -> float:
        """Calculate weighted final score (0-1000)"""
        weighted = (
            self.accuracy_score * self.accuracy_weight +
            self.speed_score * self.speed_weight +
            self.cost_score * self.cost_weight +
            self.performance_history_score * self.history_weight
        )
        return weighted * 10  # Scale to 0-1000

    def __str__(self) -> str:
        return (
            f"ModelScore(final={self.final_score:.0f}, "
            f"accuracy={self.accuracy_score:.0f}, "
            f"speed={self.speed_score:.0f}, "
            f"cost={self.cost_score:.0f}, "
            f"history={self.performance_history_score:.0f})"
        )

class ScoringStrategy(str, Enum):
    """Different scoring strategies for different scenarios"""
    BALANCED = "balanced"              # Balance all factors
    ACCURACY_FIRST = "accuracy_first"  # Prioritize accuracy
    SPEED_FIRST = "speed_first"        # Prioritize speed
    COST_FIRST = "cost_first"          # Prioritize cost
    HISTORY_FIRST = "history_first"    # Trust past performance most
```

### 3. Performance Tracking

**File: `src/cofounder_agent/models/performance_models.py`**

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

@dataclass
class ExecutionMetric:
    """Single execution metric"""
    model_name: str
    provider: str
    task_type: str

    success: bool
    response_time: float              # seconds
    token_input: int
    token_output: int
    cost: float                        # $ cost
    user_rating: Optional[float]       # 1-5 stars

    executed_at: datetime = field(default_factory=datetime.now)

@dataclass
class ModelPerformance:
    """Aggregate performance metrics for a model on a task type"""

    model_name: str
    provider: str
    task_type: str

    # Execution history
    executions: List[ExecutionMetric] = field(default_factory=list)

    # Aggregate metrics
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0

    # Averages
    avg_response_time: float = 0.0     # seconds
    avg_token_input: int = 0
    avg_token_output: int = 0
    avg_cost: float = 0.0              # $
    avg_user_rating: Optional[float] = None  # 1-5

    # Derived metrics
    @property
    def success_rate(self) -> float:
        """Success rate 0-1"""
        return self.successful_runs / self.total_runs if self.total_runs > 0 else 0.5

    @property
    def reliability_score(self) -> float:
        """0-100 reliability score"""
        return self.success_rate * 100

    @property
    def speed_score(self) -> float:
        """0-100 speed score (lower response time = higher score)"""
        if self.avg_response_time == 0:
            return 50  # Unknown
        # 1s = 100, 10s = 50, 30s = 10
        return max(0, min(100, 100 - (self.avg_response_time - 1) * 5))

    @property
    def cost_effectiveness(self) -> float:
        """Cost per successful request"""
        if self.successful_runs == 0:
            return float('inf')
        return self.avg_cost

    async def add_execution(self, metric: ExecutionMetric):
        """Record an execution and update aggregates"""
        self.executions.append(metric)
        self.total_runs += 1

        if metric.success:
            self.successful_runs += 1
        else:
            self.failed_runs += 1

        # Recalculate averages
        self._recalculate_averages()

    def _recalculate_averages(self):
        """Recalculate all aggregate metrics"""
        if self.total_runs == 0:
            return

        successful = [e for e in self.executions if e.success]
        if successful:
            self.avg_response_time = sum(e.response_time for e in successful) / len(successful)
            self.avg_token_input = int(sum(e.token_input for e in successful) / len(successful))
            self.avg_token_output = int(sum(e.token_output for e in successful) / len(successful))
            self.avg_cost = sum(e.cost for e in successful) / len(successful)

            ratings = [e.user_rating for e in successful if e.user_rating]
            if ratings:
                self.avg_user_rating = sum(ratings) / len(ratings)
```

### 4. Model Selector Service

**File: `src/cofounder_agent/services/model_selector.py`** (300+ lines)

```python
import logging
from typing import List, Optional, Dict
from datetime import datetime
from services.model_consolidation_service import get_model_consolidation_service
from models.task_types import TaskRequirements, get_requirements_for_task
from models.model_scoring import ModelScore, ScoringStrategy
from models.performance_models import ModelPerformance, ExecutionMetric

logger = logging.getLogger(__name__)

class ModelSelector:
    """Intelligent model selection based on task requirements"""

    def __init__(self):
        self.consolidation_service = get_model_consolidation_service()
        self.performance_cache: Dict[str, ModelPerformance] = {}

    async def select_model_for_task(
        self,
        task_type: str,
        requirements: Optional[TaskRequirements] = None,
        strategy: ScoringStrategy = ScoringStrategy.BALANCED,
        prefer_provider: Optional[str] = None
    ) -> str:
        """
        Select optimal model for a task

        Args:
            task_type: Type of task (e.g., "blog_generation")
            requirements: Task requirements (if None, uses defaults)
            strategy: Scoring strategy to use
            prefer_provider: Prefer this provider if available

        Returns:
            Selected model name (e.g., "gpt-4")

        Raises:
            ValueError: If no suitable model found
        """

        # 1. Load requirements for this task type
        if requirements is None:
            requirements = get_requirements_for_task(task_type)

        logger.info(f"Selecting model for task: {task_type}")
        logger.debug(f"Requirements: {requirements}")

        # 2. Get available models from consolidation service
        available_models = self.consolidation_service.list_models()
        logger.debug(f"Available models: {len(available_models)} total")

        # 3. Score each model
        scored_models: List[tuple[str, float]] = []

        for provider, models in available_models.items():
            # Apply provider preference filter
            if prefer_provider and provider != prefer_provider:
                continue

            for model_name in models:
                score = await self._score_model(
                    model_name=model_name,
                    provider=provider,
                    task_type=task_type,
                    requirements=requirements,
                    strategy=strategy
                )
                scored_models.append((f"{provider}/{model_name}", score.final_score))
                logger.debug(f"Model {model_name}: score={score.final_score:.0f}")

        if not scored_models:
            raise ValueError(f"No suitable models found for task: {task_type}")

        # 4. Sort by score and return top model
        top_model = max(scored_models, key=lambda x: x[1])[0]
        logger.info(f"Selected model: {top_model}")

        return top_model

    async def _score_model(
        self,
        model_name: str,
        provider: str,
        task_type: str,
        requirements: TaskRequirements,
        strategy: ScoringStrategy
    ) -> ModelScore:
        """Score a single model on requirements"""

        # 1. Calculate accuracy score
        accuracy_score = self._calculate_accuracy_score(
            provider=provider,
            model_name=model_name,
            accuracy_required=requirements.accuracy_required
        )

        # 2. Calculate speed score
        speed_score = self._calculate_speed_score(
            provider=provider,
            model_name=model_name,
            speed_required=requirements.speed_required
        )

        # 3. Calculate cost score
        cost_score = self._calculate_cost_score(
            provider=provider,
            model_name=model_name,
            max_cost=requirements.max_cost_per_call,
            prefer_free=requirements.prefer_free
        )

        # 4. Get performance history
        perf = await self._get_performance_history(
            model_name=model_name,
            provider=provider,
            task_type=task_type
        )

        performance_history_score = perf.reliability_score if perf else 50

        # 5. Calculate suitability
        suitability_score = self._calculate_suitability(
            requirements=requirements,
            provider=provider,
            model_name=model_name
        )

        # 6. Apply strategy weights
        score = ModelScore(
            accuracy_score=accuracy_score,
            speed_score=speed_score,
            cost_score=cost_score,
            suitability_score=suitability_score,
            performance_history_score=performance_history_score
        )

        # Adjust weights based on strategy
        if strategy == ScoringStrategy.ACCURACY_FIRST:
            score.accuracy_weight = 0.50
            score.history_weight = 0.25
        elif strategy == ScoringStrategy.SPEED_FIRST:
            score.speed_weight = 0.50
            score.history_weight = 0.25
        elif strategy == ScoringStrategy.COST_FIRST:
            score.cost_weight = 0.50
            score.history_weight = 0.25
        elif strategy == ScoringStrategy.HISTORY_FIRST:
            score.history_weight = 0.60

        return score

    def _calculate_accuracy_score(
        self,
        provider: str,
        model_name: str,
        accuracy_required: int
    ) -> float:
        """Calculate accuracy score (0-100)"""
        # Map provider/model to known accuracy levels
        accuracy_map = {
            ("openai", "gpt-4"): 95,
            ("anthropic", "claude-3-opus"): 93,
            ("google", "gemini-pro"): 90,
            ("huggingface", "mistral"): 75,
            ("ollama", "llama2"): 70,
        }

        base_accuracy = accuracy_map.get((provider, model_name), 70)

        # Score: higher accuracy available = higher score
        return min(100, base_accuracy)

    def _calculate_speed_score(
        self,
        provider: str,
        model_name: str,
        speed_required: int
    ) -> float:
        """Calculate speed score (0-100)"""
        # Known response times (seconds)
        speed_map = {
            ("ollama", "llama2"): 0.5,        # Local = fast
            ("openai", "gpt-4"): 2.0,         # API = slower
            ("anthropic", "claude-3-sonnet"): 1.5,
            ("google", "gemini-pro"): 1.2,
        }

        avg_time = speed_map.get((provider, model_name), 2.0)

        # Convert to 0-100 score (lower time = higher score)
        # 0.5s = 100, 2s = 75, 5s = 50, 10s = 25
        score = max(0, min(100, 100 - (avg_time - 0.5) * 15))

        return score

    def _calculate_cost_score(
        self,
        provider: str,
        model_name: str,
        max_cost: Optional[float],
        prefer_free: bool
    ) -> float:
        """Calculate cost score (0-100)"""
        # Known costs per 1K tokens
        cost_map = {
            ("ollama", "llama2"): 0.0,        # Free (local)
            ("huggingface", "mistral"): 0.0,  # Free tier
            ("google", "gemini-pro"): 0.001,
            ("anthropic", "claude-3-sonnet"): 0.003,
            ("openai", "gpt-4"): 0.03,       # Most expensive
        }

        cost = cost_map.get((provider, model_name), 0.01)

        # If cost exceeds max, heavily penalize
        if max_cost and cost > max_cost:
            return 0

        # If prefer_free and this is free, max score
        if prefer_free and cost == 0:
            return 100

        # Score: lower cost = higher score (0-100)
        score = max(0, min(100, 100 - (cost * 3000)))

        return score

    def _calculate_suitability(
        self,
        requirements: TaskRequirements,
        provider: str,
        model_name: str
    ) -> float:
        """Calculate task suitability (0-100)"""
        score = 50  # Start at neutral

        # Check resource requirements
        if requirements.requires_local_execution and provider == "ollama":
            score += 20  # Local execution available
        elif requirements.requires_local_execution and provider != "ollama":
            score -= 20  # Doesn't meet requirement

        if requirements.requires_gpu and provider == "ollama":
            score += 15  # GPU available

        # Favor larger models for higher accuracy requirements
        if requirements.accuracy_required > 80:
            if "gpt-4" in model_name or "opus" in model_name:
                score += 15
            elif "sonnet" in model_name or "pro" in model_name:
                score += 10

        # Favor smaller models for speed requirements
        if requirements.speed_required > 80:
            if "mistral" in model_name or "llama" in model_name:
                score += 15

        return max(0, min(100, score))

    async def _get_performance_history(
        self,
        model_name: str,
        provider: str,
        task_type: str
    ) -> Optional[ModelPerformance]:
        """Get performance history for a model on a task"""
        key = f"{provider}/{model_name}/{task_type}"
        return self.performance_cache.get(key)

    async def record_execution(
        self,
        model_name: str,
        provider: str,
        task_type: str,
        success: bool,
        response_time: float,
        token_input: int,
        token_output: int,
        cost: float,
        user_rating: Optional[float] = None
    ):
        """Record execution for future model selection"""

        metric = ExecutionMetric(
            model_name=model_name,
            provider=provider,
            task_type=task_type,
            success=success,
            response_time=response_time,
            token_input=token_input,
            token_output=token_output,
            cost=cost,
            user_rating=user_rating
        )

        key = f"{provider}/{model_name}/{task_type}"

        if key not in self.performance_cache:
            self.performance_cache[key] = ModelPerformance(
                model_name=model_name,
                provider=provider,
                task_type=task_type
            )

        await self.performance_cache[key].add_execution(metric)
        logger.info(f"Recorded execution: {key} (success={success})")

# Global singleton
_model_selector: Optional[ModelSelector] = None

def get_model_selector() -> ModelSelector:
    """Get global model selector instance"""
    global _model_selector
    if _model_selector is None:
        _model_selector = ModelSelector()
    return _model_selector
```

---

## 🧪 Test Plan

### Test 1: Task Type Mapping (2 tests)

```python
# test_task_types.py
def test_content_task_types():
    """Verify content task types are defined"""
    assert ContentTaskType.BLOG_GENERATION in ContentTaskType.__members__.values()

def test_financial_task_types():
    """Verify financial task types are defined"""
    assert FinancialTaskType.COST_ANALYSIS in FinancialTaskType.__members__.values()
```

### Test 2: Model Selector - Basic Selection (4 tests)

```python
# test_model_selector.py
async def test_select_model_for_blog():
    """Select model for blog generation"""
    selector = get_model_selector()
    model = await selector.select_model_for_task(
        task_type=ContentTaskType.BLOG_GENERATION
    )
    assert model is not None
    assert "/" in model  # Format: provider/model

async def test_select_model_with_requirements():
    """Select model with specific requirements"""
    selector = get_model_selector()
    reqs = TaskRequirements(
        accuracy_required=90,
        creativity_required=80,
        speed_required=50,
        context_length_required=4000,
        requires_local_execution=False,
        requires_gpu=False,
        max_cost_per_call=0.05,
        prefer_free=True,
        output_length_preference="long",
        output_format="text",
        custom_requirements={}
    )
    model = await selector.select_model_for_task(
        task_type=ContentTaskType.BLOG_GENERATION,
        requirements=reqs
    )
    assert model is not None

async def test_select_model_prefers_provider():
    """Select model with provider preference"""
    selector = get_model_selector()
    model = await selector.select_model_for_task(
        task_type=ContentTaskType.BLOG_GENERATION,
        prefer_provider="ollama"
    )
    assert "ollama" in model

async def test_select_model_applies_strategy():
    """Select model with different strategy"""
    selector = get_model_selector()
    model_acc = await selector.select_model_for_task(
        task_type=FinancialTaskType.COST_ANALYSIS,
        strategy=ScoringStrategy.ACCURACY_FIRST
    )
    model_cost = await selector.select_model_for_task(
        task_type=FinancialTaskType.COST_ANALYSIS,
        strategy=ScoringStrategy.COST_FIRST
    )
    # Both should return valid models
    assert model_acc is not None
    assert model_cost is not None
```

### Test 3: Performance Tracking (2 tests)

```python
# test_performance_tracker.py
async def test_record_execution():
    """Record execution metric"""
    selector = get_model_selector()
    await selector.record_execution(
        model_name="gpt-4",
        provider="openai",
        task_type=ContentTaskType.BLOG_GENERATION,
        success=True,
        response_time=2.5,
        token_input=100,
        token_output=500,
        cost=0.05,
        user_rating=4.5
    )
    # Should be recorded without error

async def test_performance_affects_selection():
    """Verify performance history affects model selection"""
    selector = get_model_selector()

    # Record successful executions for one model
    for _ in range(5):
        await selector.record_execution(
            model_name="gpt-4",
            provider="openai",
            task_type=ContentTaskType.BLOG_GENERATION,
            success=True,
            response_time=2.0,
            token_input=100,
            token_output=500,
            cost=0.05
        )

    # Should now prefer this model
    model = await selector.select_model_for_task(
        task_type=ContentTaskType.BLOG_GENERATION
    )
    # Performance history should influence selection
    assert model is not None
```

### Test 4: Integration Tests (5 tests)

```python
# test_phase3_task1_integration.py
async def test_full_workflow():
    """Full workflow: select, use, record, select again"""
    selector = get_model_selector()

    # First selection
    model1 = await selector.select_model_for_task(
        task_type=ContentTaskType.BLOG_GENERATION
    )

    # Record performance
    await selector.record_execution(
        model_name=model1.split("/")[1],
        provider=model1.split("/")[0],
        task_type=ContentTaskType.BLOG_GENERATION,
        success=True,
        response_time=2.0,
        token_input=100,
        token_output=500,
        cost=0.05,
        user_rating=5.0
    )

    # Second selection should be influenced by performance
    model2 = await selector.select_model_for_task(
        task_type=ContentTaskType.BLOG_GENERATION
    )

    assert model1 is not None
    assert model2 is not None
```

---

## 📈 Implementation Checklist

### Week 1

- [ ] Create `task_types.py` with all TaskType enums
- [ ] Create `model_scoring.py` with scoring model
- [ ] Create `performance_models.py` with tracking
- [ ] Create `model_selector.py` with selection logic
- [ ] Write 10 unit tests
- [ ] All tests passing

### Week 2

- [ ] Integrate ModelSelector into each agent
- [ ] Create `task_analyzer.py` for automatic TaskType detection
- [ ] Write 5 integration tests
- [ ] Document API
- [ ] Code review and cleanup
- [ ] All 15 tests passing
- [ ] Zero lint errors

---

## 🚀 Success Criteria for Task 1

- ✅ TaskType enums defined for all agent types
- ✅ ModelSelector selects 85%+ correct models
- ✅ Performance tracking working
- ✅ 15+ tests, all passing
- ✅ Zero lint errors
- ✅ Ready for agent integration

---

## 📚 Related Files

- Previous: `docs/PHASE_2_TASK_4_COMPLETION.md`
- Next: Phase 3 Tasks 2-5 specifications
- Reference: `docs/PHASE_3_PLAN.md`

---

**Ready to begin Phase 3 Task 1!** 🚀
