"""
Task Planning Service - Generate Visible Execution Plans

Extends UnifiedOrchestrator to create visible, user-confirmable execution plans.

Purpose:
- Transform task_intent_request + business metrics into detailed execution plan
- Show user: estimated time, cost, resource allocation, stage breakdown
- Store plan in tasks.metadata before execution begins
- Allow user to confirm, modify, or reject plan before execution

Phase 3 of Unified Task Orchestration System.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .model_router import ModelRouter
from .task_intent_router import TaskIntentRequest
from .unified_orchestrator import UnifiedOrchestrator

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES FOR EXECUTION PLANNING
# ============================================================================


@dataclass
class StageCost:
    """Cost breakdown for a single stage."""

    stage: str
    model: str
    estimated_tokens: int
    estimated_cost: float  # USD
    actual_cost: Optional[float] = None


@dataclass
class ExecutionPlanStage:
    """Details for one stage in the execution plan."""

    stage_number: int  # 1, 2, 3, etc.
    stage_name: str  # "Research", "Creative", etc.
    description: str
    required_inputs: List[str]
    estimated_duration_ms: int
    estimated_cost: float
    model: str
    parallelizable_with: List[str] = None  # Which other stages can run in parallel
    depends_on: List[str] = None  # Which stages must complete first
    quality_metrics: Dict[str, Any] = None  # Metrics to measure quality


@dataclass
class ExecutionPlan:
    """Complete execution plan for a task."""

    task_id: str
    task_type: str
    total_estimated_duration_ms: int
    total_estimated_cost: float
    total_estimated_tokens: int
    stages: List[ExecutionPlanStage]
    parallelization_strategy: str  # "sequential", "parallel", "mixed"
    resource_requirements: Dict[str, Any]  # GPU, memory, etc.
    alternative_strategies: List["ExecutionPlan"] = None  # Other ways to accomplish goal
    estimated_quality_score: float  # 0-100
    success_probability: float  # 0-1 based on historical data
    created_at: str = None
    user_confirmed: bool = False


@dataclass
class ExecutionPlanSummary:
    """Human-readable summary for UI display."""

    title: str
    description: str
    estimated_time: str  # "45 minutes"
    estimated_cost: str  # "$1.25"
    confidence: str  # "High", "Medium", "Low"
    key_stages: List[str]  # Top-level stages to show
    warnings: List[str] = None  # "May require manual image selection", etc.
    opportunities: List[str] = None  # "Can save $0.50 by skipping QA", etc.


# ============================================================================
# TASK PLANNING SERVICE
# ============================================================================


class TaskPlanningService:
    """
    Generate and manage execution plans for tasks.

    Workflow:
    1. User provides task via NL or form → TaskIntentRequest created
    2. TaskPlanningService.generate_plan() creates ExecutionPlan
    3. ExecutionPlanSummary shown to user for confirmation
    4. User confirms or requests alternative strategy
    5. ExecutionPlan stored in tasks.metadata
    6. Task executor follows plan during execution
    """

    # Estimated costs and durations (can be updated based on actual data)
    STAGE_DURATIONS_MS = {
        "research": 15000,
        "creative": 25000,
        "qa": 12000,
        "images": 8000,
        "format": 3000,
    }

    STAGE_COSTS_USD = {
        # Cost per stage execution (based on token usage)
        "research": 0.05,  # GPT-4 ~100 tokens
        "creative": 0.15,  # GPT-4 ~300 tokens
        "qa": 0.08,  # GPT-4 ~160 tokens
        "images": 0.03,  # Vision ~60 tokens
        "format": 0.02,  # GPT-3.5 ~40 tokens
    }

    STAGE_MODELS = {
        # Recommended model for each stage
        "research": "gpt-4",
        "creative": "claude-opus",  # Best for writing
        "qa": "gpt-4",
        "images": "gpt-4-vision",
        "format": "gpt-3.5-turbo",  # Fast, cheap
    }

    def __init__(self):
        """Initialize planning service."""
        self.orchestrator = UnifiedOrchestrator()
        self.model_router = ModelRouter()

    async def generate_plan(
        self,
        task_intent_request: TaskIntentRequest,
        business_metrics: Optional[Dict[str, Any]] = None,
    ) -> ExecutionPlan:
        """
        Generate an execution plan for a task.

        Args:
            task_intent_request: Parsed task intent with task_type, subtasks, parameters
            business_metrics: Budget, deadline, quality_preference, etc.

        Returns:
            ExecutionPlan with all stages, estimated time/cost, and strategy
        """

        business_metrics = business_metrics or {}
        budget = business_metrics.get("budget", 10.0)  # Max budget in USD
        deadline = business_metrics.get("deadline")  # datetime
        quality_preference = business_metrics.get("quality_preference", "balanced")

        # Generate stages based on suggested_subtasks
        stages = self._generate_stages(task_intent_request.suggested_subtasks, quality_preference)

        # Calculate total cost and duration
        total_cost = sum(stage.estimated_cost for stage in stages)
        total_duration = sum(stage.estimated_duration_ms for stage in stages)
        total_tokens = sum(
            self.STAGE_COSTS_USD.get(stage.stage_name.lower(), 0) * 20  # Rough token estimate
            for stage in stages
        )

        # Optimize strategy based on business metrics
        parallelization_strategy = self._optimize_strategy(
            stages, total_duration, deadline, budget, quality_preference
        )

        # If parallel strategy, reduce estimated duration
        if parallelization_strategy == "parallel":
            # Can run research + format in parallel, creative + QA serial
            total_duration = (
                max(stage.estimated_duration_ms for stage in stages) * 2
            )  # Rough estimate
        elif parallelization_strategy == "mixed":
            total_duration = int(total_duration * 0.7)  # Some parallelization

        # Estimate quality score
        quality_score = self._estimate_quality_score(
            task_intent_request, stages, quality_preference
        )

        # Calculate success probability based on historical data
        success_probability = self._estimate_success_probability(
            task_intent_request.task_type, len(stages), quality_preference
        )

        # Check budget feasibility
        resource_requirements = self._determine_resource_requirements(stages, quality_preference)

        plan = ExecutionPlan(
            task_id=task_intent_request.task_id or str(__import__("uuid").uuid4()),
            task_type=task_intent_request.task_type,
            total_estimated_duration_ms=total_duration,
            total_estimated_cost=total_cost,
            total_estimated_tokens=int(total_tokens),
            stages=stages,
            parallelization_strategy=parallelization_strategy,
            resource_requirements=resource_requirements,
            estimated_quality_score=quality_score,
            success_probability=success_probability,
            created_at=datetime.utcnow().isoformat(),
        )

        return plan

    def _generate_stages(
        self, subtasks: List[str], quality_preference: str
    ) -> List[ExecutionPlanStage]:
        """
        Convert list of subtasks into detailed ExecutionPlanStage objects.

        Args:
            subtasks: List of stage names ["research", "creative", "qa", "images", "format"]
            quality_preference: "draft" | "balanced" | "high"

        Returns:
            List of ExecutionPlanStage with dependencies and resource requirements
        """

        stages = []
        stage_number = 1

        # Stage descriptions
        descriptions = {
            "research": "Gather information and context about the topic",
            "creative": "Generate initial draft content",
            "qa": "Review content for quality and accuracy",
            "images": "Search and select relevant images",
            "format": "Format content for publication",
        }

        # Dependencies (stages must complete before others can start)
        dependencies = {
            "creative": ["research"],
            "qa": ["creative"],
            "images": [],  # Can run in parallel
            "format": ["qa", "images"],  # Needs both qa and images
        }

        # Parallelizable stages
        parallelizable = {
            "research": ["images", "format"],
            "creative": ["images"],
            "qa": ["images"],
            "images": ["research", "creative", "qa"],
            "format": [],  # Must be last
        }

        for subtask in subtasks:
            subtask_lower = subtask.lower()

            if subtask_lower not in self.STAGE_DURATIONS_MS:
                logger.warning("Unknown subtask: %s", subtask)
                continue

            # Adjust duration based on quality preference
            duration = self.STAGE_DURATIONS_MS[subtask_lower]
            if quality_preference == "high":
                duration = int(duration * 1.5)  # 50% longer for quality
            elif quality_preference == "draft":
                duration = int(duration * 0.7)  # 30% faster for draft

            # Adjust cost similarly
            cost = self.STAGE_COSTS_USD[subtask_lower]
            if quality_preference == "high":
                cost = cost * 1.3
            elif quality_preference == "draft":
                cost = cost * 0.8

            stage = ExecutionPlanStage(
                stage_number=stage_number,
                stage_name=subtask,
                description=descriptions.get(subtask_lower, f"Execute {subtask}"),
                required_inputs=self._determine_required_inputs(subtask_lower),
                estimated_duration_ms=duration,
                estimated_cost=cost,
                model=self.STAGE_MODELS.get(subtask_lower, "gpt-4"),
                parallelizable_with=parallelizable.get(subtask_lower, []),
                depends_on=dependencies.get(subtask_lower, []),
                quality_metrics=self._determine_quality_metrics(subtask_lower, quality_preference),
            )

            stages.append(stage)
            stage_number += 1

        return stages

    def _determine_required_inputs(self, stage: str) -> List[str]:
        """Determine what inputs a stage needs."""
        inputs = {
            "research": ["topic", "keywords"],
            "creative": ["topic", "research_output", "style", "tone"],
            "qa": ["topic", "creative_output", "research_output"],
            "images": ["topic", "content"],
            "format": ["topic", "content", "featured_image_url", "tags", "category"],
        }
        return inputs.get(stage, [])

    def _determine_quality_metrics(self, stage: str, quality_preference: str) -> Dict[str, Any]:
        """Determine quality metrics to measure for a stage."""

        metrics = {
            "research": {
                "completeness": 0.8 if quality_preference == "high" else 0.6,
                "accuracy": 0.9,
                "source_quality": 0.7,
            },
            "creative": {
                "readability": 0.8,
                "engagement": 0.7,
                "seo_score": 0.75,
                "word_count_accuracy": 0.9,
            },
            "qa": {
                "grammar": 0.95,
                "clarity": 0.85,
                "accuracy": 0.9,
                "consistency": 0.85,
            },
            "images": {
                "relevance": 0.8,
                "quality": 0.7,
                "format": 1.0,
            },
            "format": {
                "markup_validity": 1.0,
                "metadata_completeness": 0.9,
                "publication_readiness": 0.95,
            },
        }

        return metrics.get(stage, {})

    def _optimize_strategy(
        self,
        stages: List[ExecutionPlanStage],
        total_duration: int,
        deadline: Optional[datetime] = None,
        budget: float = 10.0,
        quality_preference: str = "balanced",
    ) -> str:
        """
        Determine optimal execution strategy based on constraints.

        Returns: "sequential" | "parallel" | "mixed"
        """

        # If deadline is tight, prefer parallel
        if deadline:
            time_until_deadline = (deadline - datetime.utcnow()).total_seconds() * 1000

            if time_until_deadline < total_duration * 1.2:
                # Not enough time for sequential - must parallelize
                return "parallel"

            if time_until_deadline < total_duration * 1.8:
                # Some time pressure - use mixed strategy
                return "mixed"

        # If budget is tight, prefer sequential (cheaper)
        cost_per_stage = budget / max(len(stages), 1)
        if cost_per_stage < 0.1:
            return "sequential"  # Can't afford parallel overhead

        # If high quality is required, prefer sequential (better control)
        if quality_preference == "high":
            return "sequential"

        # Default: mixed strategy (good balance)
        return "mixed"

    def _estimate_quality_score(
        self,
        task_intent: TaskIntentRequest,
        stages: List[ExecutionPlanStage],
        quality_preference: str,
    ) -> float:
        """
        Estimate quality score (0-100) for the execution plan.

        Based on: stages included, quality_preference, model selection
        """

        base_score = 70.0

        # Add points for important stages
        if any(s.stage_name.lower() == "qa" for s in stages):
            base_score += 15  # QA review adds quality

        if any(s.stage_name.lower() == "images" for s in stages):
            base_score += 5  # Images improve quality

        # Quality preference multiplier
        quality_multipliers = {
            "draft": 0.7,
            "balanced": 1.0,
            "high": 1.3,
        }

        base_score *= quality_multipliers.get(quality_preference, 1.0)

        # Cap at 100
        return min(100.0, base_score)

    def _estimate_success_probability(
        self, task_type: str, num_stages: int, quality_preference: str
    ) -> float:
        """
        Estimate probability of successful execution (0-1).

        Based on: historical data, number of stages, complexity
        """

        # Base success rate by task type
        base_rates = {
            "blog_post": 0.92,
            "social_media": 0.95,
            "email": 0.94,
            "newsletter": 0.90,
            "generic": 0.85,
        }

        base = base_rates.get(task_type, 0.85)

        # Each stage adds risk: 2% failure per stage
        risk_per_stage = 0.02
        success = base * (1 - (num_stages - 1) * risk_per_stage)

        # Quality preference affects success
        if quality_preference == "high":
            success *= 0.95  # Stricter requirements = more likely to fail
        elif quality_preference == "draft":
            success *= 1.05  # More lenient = more likely to succeed

        # Clamp to valid range
        return max(0.0, min(1.0, success))

    def _determine_resource_requirements(
        self, stages: List[ExecutionPlanStage], quality_preference: str
    ) -> Dict[str, Any]:
        """Determine computational resource requirements."""

        has_vision = any(s.model == "gpt-4-vision" for s in stages)

        return {
            "gpu_required": has_vision,
            "estimated_memory_mb": 2048 if has_vision else 512,
            "parallel_tasks": len([s for s in stages if s.parallelizable_with]),
            "max_concurrent_cost": sum(s.estimated_cost for s in stages if not s.depends_on),
        }

    def plan_to_summary(self, plan: ExecutionPlan) -> ExecutionPlanSummary:
        """
        Convert ExecutionPlan to human-readable ExecutionPlanSummary for UI.
        """

        # Convert milliseconds to readable time
        total_secs = plan.total_estimated_duration_ms / 1000
        if total_secs < 60:
            estimated_time = f"{int(total_secs)} seconds"
        elif total_secs < 3600:
            estimated_time = f"{int(total_secs / 60)} minutes"
        else:
            hours = int(total_secs / 3600)
            mins = int((total_secs % 3600) / 60)
            estimated_time = f"{hours}h {mins}m"

        # Format cost
        estimated_cost = f"${plan.total_estimated_cost:.2f}"

        # Confidence based on success probability
        if plan.success_probability > 0.9:
            confidence = "High"
        elif plan.success_probability > 0.75:
            confidence = "Medium"
        else:
            confidence = "Low"

        # Top-level stages
        key_stages = [s.stage_name for s in plan.stages]

        # Warnings and opportunities
        warnings = []
        opportunities = []

        if plan.total_estimated_cost > 5.0:
            warnings.append(f"High cost estimate (${plan.total_estimated_cost:.2f})")

        if not any(s.stage_name.lower() == "qa" for s in plan.stages):
            warnings.append("No QA review - consider adding for quality assurance")

        if plan.parallelization_strategy == "sequential":
            opportunities.append("Can parallelize some stages to save time")

        if plan.estimated_quality_score < 75:
            opportunities.append("Add QA stage to improve quality score")

        return ExecutionPlanSummary(
            title=f"{plan.task_type.replace('_', ' ').title()} Execution Plan",
            description=f"Create content through {len(plan.stages)} stages: "
            + ", ".join(s.stage_name for s in plan.stages),
            estimated_time=estimated_time,
            estimated_cost=estimated_cost,
            confidence=confidence,
            key_stages=key_stages,
            warnings=warnings if warnings else None,
            opportunities=opportunities if opportunities else None,
        )

    def serialize_plan(self, plan: ExecutionPlan) -> Dict[str, Any]:
        """Convert ExecutionPlan to dict for database storage."""

        plan_dict = asdict(plan)

        # Convert datetime strings
        if plan_dict.get("created_at"):
            plan_dict["created_at"] = plan.created_at

        # Convert stages to dicts
        plan_dict["stages"] = [asdict(s) for s in plan.stages]

        return plan_dict

    async def get_alternative_strategies(
        self,
        task_intent_request: TaskIntentRequest,
        business_metrics: Optional[Dict[str, Any]] = None,
        num_alternatives: int = 2,
    ) -> List[ExecutionPlan]:
        """
        Generate alternative execution strategies for the same task.

        Example alternatives:
        - Draft quality (fast, cheap) vs High quality (slow, expensive)
        - Parallel (fast) vs Sequential (cheap)
        - With images vs Without images
        """

        alternatives = []
        business_metrics = business_metrics or {}

        # Strategy 1: Draft quality (fast, cheap)
        draft_metrics = {**business_metrics, "quality_preference": "draft"}
        plan_draft = await self.generate_plan(task_intent_request, draft_metrics)
        plan_draft.title = "Fast & Budget-Friendly"
        alternatives.append(plan_draft)

        # Strategy 2: High quality (slower, more expensive)
        high_metrics = {**business_metrics, "quality_preference": "high"}
        plan_high = await self.generate_plan(task_intent_request, high_metrics)
        plan_high.title = "Premium Quality"
        alternatives.append(plan_high)

        # Strategy 3: Minimal (skip images and QA)
        if len(task_intent_request.suggested_subtasks) > 2:
            minimal_subtasks = [
                s
                for s in task_intent_request.suggested_subtasks
                if s.lower() not in ["qa", "images"]
            ]
            if minimal_subtasks != task_intent_request.suggested_subtasks:
                minimal_intent = TaskIntentRequest(
                    raw_input=task_intent_request.raw_input,
                    intent_type=task_intent_request.intent_type,
                    task_type=task_intent_request.task_type,
                    confidence=task_intent_request.confidence,
                    parameters=task_intent_request.parameters,
                    suggested_subtasks=minimal_subtasks,
                    requires_confirmation=task_intent_request.requires_confirmation,
                    execution_strategy=task_intent_request.execution_strategy,
                )
                plan_minimal = await self.generate_plan(minimal_intent, business_metrics)
                alternatives.append(plan_minimal)

        return alternatives[:num_alternatives]
