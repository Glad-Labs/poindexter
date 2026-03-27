"""
Smart Model Router Service

Routes AI requests to cost-effective models based on task complexity.
Saves 60-80% on AI API costs by using cheaper models for simple tasks.

ZERO-COST OPTION: Use Ollama for 100% free local inference on desktop!

Cost Comparison (per 1K tokens):
- GPT-4 Turbo: $0.03 input / $0.06 output
- GPT-3.5 Turbo: $0.0015 input / $0.002 output (20x cheaper)
- Claude Opus: $0.015 input / $0.075 output
- Claude Instant: $0.0008 input / $0.0024 output (18x cheaper)
- Ollama (Local): $0.00 - COMPLETELY FREE! ⭐

Expected Savings: $10,000-$15,000/year with smart routing
Additional Savings: $2,400-$3,600/year with token limiting
Zero-Cost Option: 100% savings with Ollama local models
"""

import os
from enum import Enum
from typing import Any, Dict, Optional, Tuple

import structlog

from .model_constants import MODEL_COSTS

logger = structlog.get_logger(__name__)


class ModelProvider(str, Enum):
    """AI model provider types."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"  # Zero-cost local option


class TaskComplexity(str, Enum):
    """Task complexity levels for model routing."""

    SIMPLE = "simple"  # GPT-3.5, Claude Instant, Ollama Phi
    MEDIUM = "medium"  # Claude Haiku, GPT-3.5 Turbo, Ollama Mistral
    COMPLEX = "complex"  # Claude Opus, GPT-4, Ollama Mixtral
    CRITICAL = "critical"  # GPT-4 Turbo only (or Llama2-70B locally)


class ModelTier(str, Enum):
    """AI model tiers by cost and capability."""

    FREE = "free"  # Zero cost: Ollama models
    BUDGET = "budget"  # Cheapest API: GPT-3.5, Claude Instant
    STANDARD = "standard"  # Mid-tier: Claude Haiku
    PREMIUM = "premium"  # Advanced: Claude Opus
    FLAGSHIP = "flagship"  # Most capable: GPT-4 Turbo


# Token limits by task type (reduces over-generation costs)
MAX_TOKENS_BY_TASK = {
    # Simple tasks - minimal output needed
    "summary": 150,
    "summarize": 150,
    "extract": 100,
    "classify": 50,
    "categorize": 50,
    "format": 200,
    "translate": 300,
    "convert": 200,
    "list": 200,
    "count": 50,
    "filter": 100,
    "find": 100,
    "search": 100,
    "lookup": 100,
    "get": 100,
    "fetch": 100,
    "retrieve": 100,
    # Medium tasks - moderate output
    "analyze": 500,
    "compare": 400,
    "review": 500,
    "evaluate": 500,
    "assess": 500,
    "recommend": 400,
    "suggest": 300,
    "advise": 400,
    "explain": 500,
    "describe": 400,
    "interpret": 500,
    "clarify": 300,
    "elaborate": 500,
    "outline": 300,
    "draft": 600,
    # Complex tasks - detailed output
    "create": 1000,
    "generate": 1000,
    "design": 800,
    "architect": 800,
    "plan": 700,
    "strategize": 700,
    "optimize": 800,
    "refactor": 1000,
    "implement": 1200,
    "build": 1200,
    "develop": 1200,
    "engineer": 1200,
    "code": 2000,
    "program": 2000,
    "debug": 1000,
    # Critical tasks - comprehensive output
    "legal": 1500,
    "contract": 1500,
    "compliance": 1200,
    "security": 1200,
    "audit": 1200,
    "financial": 1200,
    "regulatory": 1200,
    "risk": 1000,
    "sensitive": 1000,
    "confidential": 1000,
    # Default fallback
    "default": 800,
}


class ModelRouter:
    """
    Smart router that selects cost-effective AI models based on task requirements.

    Routing Strategy:
    1. Analyze task type and complexity
    2. Check token budget and priority
    3. Select cheapest model that meets requirements
    4. Track cost savings vs. always using premium models

    Example:
        router = ModelRouter()
        model, estimated_cost = router.route_request(
            task_type="summarize",
            context={"priority": "low", "max_tokens": 200}
        )
        # Returns: ("gpt-3.5-turbo", 0.0003) instead of ("gpt-4", 0.006)
    """

    # Task type complexity mapping
    TASK_COMPLEXITY = {
        # Simple tasks (budget models)
        "simple": [
            "summarize",
            "summary",
            "extract",
            "classify",
            "categorize",
            "format",
            "translate",
            "convert",
            "list",
            "count",
            "filter",
            "find",
            "search",
            "lookup",
            "get",
            "fetch",
            "retrieve",
        ],
        # Medium tasks (standard models)
        "medium": [
            "analyze",
            "compare",
            "review",
            "evaluate",
            "assess",
            "recommend",
            "suggest",
            "advise",
            "explain",
            "describe",
            "interpret",
            "clarify",
            "elaborate",
            "outline",
            "draft",
        ],
        # Complex tasks (premium models)
        "complex": [
            "create",
            "generate",
            "design",
            "architect",
            "plan",
            "strategize",
            "optimize",
            "refactor",
            "implement",
            "build",
            "develop",
            "engineer",
            "code",
            "program",
            "debug",
        ],
        # Critical tasks (flagship models only)
        "critical": [
            "legal",
            "contract",
            "compliance",
            "security",
            "audit",
            "financial",
            "regulatory",
            "risk",
            "sensitive",
            "confidential",
        ],
    }

    # Model recommendations by tier (with Ollama support for zero-cost operation)
    MODEL_RECOMMENDATIONS = {
        TaskComplexity.SIMPLE: {
            "primary": "gpt-3.5-turbo",
            "fallback": "claude-haiku-3",
            "tier": ModelTier.BUDGET,
            "ollama": "ollama/qwen3:8b",  # Fast, good at extraction/summarization
        },
        TaskComplexity.MEDIUM: {
            "primary": "claude-haiku-3",
            "fallback": "gpt-3.5-turbo",
            "tier": ModelTier.BUDGET,
            "ollama": "ollama/gemma3:27b",  # Strong analysis and critique
        },
        TaskComplexity.COMPLEX: {
            "primary": "claude-sonnet-3",
            "fallback": "gpt-4",
            "tier": ModelTier.PREMIUM,
            "ollama": "ollama/qwen3.5:35b",  # Best prose quality in VRAM budget
        },
        TaskComplexity.CRITICAL: {
            "primary": "gpt-4-turbo",
            "fallback": "claude-opus-3",
            "tier": ModelTier.FLAGSHIP,
            "ollama": "ollama/qwen3.5:122b",  # Top-tier quality (CPU offload needed)
        },
    }

    def __init__(self, default_model: str = "ollama/qwen3:8b", use_ollama: bool | None = None):
        """
        Initialize model router.

        Args:
            default_model: Fallback model if routing fails
            use_ollama: Use Ollama for zero-cost local inference.
                       If None, checks USE_OLLAMA environment variable.
        """
        self.default_model = default_model

        # Check USE_OLLAMA environment variable if not explicitly set
        if use_ollama is None:
            use_ollama = os.getenv("USE_OLLAMA", "false").lower() == "true"

        self.use_ollama = use_ollama

        # Metrics
        self.metrics = {
            "total_requests": 0,
            "budget_model_uses": 0,
            "premium_model_uses": 0,
            "estimated_cost_saved": 0.0,
            "estimated_cost_actual": 0.0,
            "estimated_cost_premium_baseline": 0.0,
            "ollama_uses": 0,  # Track zero-cost local inference usage
        }

        # Spending cap — block cloud API calls when monthly budget exceeded.
        # Tracks in-memory estimated spend (resets on restart). For persistent
        # tracking, use cost_aggregation_service.get_budget_status().
        self._monthly_spend_limit = float(os.getenv("MONTHLY_SPEND_LIMIT", "100.0"))
        self._session_cloud_spend = 0.0  # Accumulated since last restart
        self._budget_exceeded_logged = False

        # Runtime provider failure tracking.
        # Keyed by provider name; value is count of consecutive failures.
        # Reset to 0 on any success from that provider.
        self._provider_consecutive_failures: Dict[str, int] = {}
        self._FAILURE_ALERT_THRESHOLD = 5  # logger.critical fires at this count

        logger.info(
            "Model router initialized", default_model=default_model, use_ollama=self.use_ollama
        )

    def record_provider_failure(self, provider: str) -> None:
        """
        Record a runtime LLM call failure for a provider.

        Increments the consecutive failure counter. When the counter reaches
        _FAILURE_ALERT_THRESHOLD, emits a logger.critical so that log-based
        alerting can fire without requiring database queries.

        Call this from LLM client code after a failed API call.
        """
        count = self._provider_consecutive_failures.get(provider, 0) + 1
        self._provider_consecutive_failures[provider] = count
        if count >= self._FAILURE_ALERT_THRESHOLD:
            logger.critical(
                f"[llm_provider] Provider {provider!r} has failed {count} consecutive times — "
                f"possible outage or quota exhaustion"
            )

    def record_provider_success(self, provider: str) -> None:
        """
        Record a successful LLM call for a provider.

        Resets the consecutive failure counter so that a single success clears
        a prior alert state.
        """
        if self._provider_consecutive_failures.get(provider, 0) > 0:
            logger.info(
                f"[llm_provider] Provider {provider!r} recovered after "
                f"{self._provider_consecutive_failures[provider]} consecutive failures"
            )
        self._provider_consecutive_failures[provider] = 0

    def get_provider_health(self) -> Dict[str, Any]:
        """Return a dict of provider names to their current failure counts."""
        return {
            provider: {"consecutive_failures": count}
            for provider, count in self._provider_consecutive_failures.items()
        }

    def route_request(
        self, task_type: str, context: Optional[Dict[str, Any]] = None, estimated_tokens: int = 1000
    ) -> Tuple[str, float, TaskComplexity]:
        """
        Route request to appropriate model based on task complexity.

        Args:
            task_type: Type of task (e.g., "summarize", "analyze", "create")
            context: Additional context (priority, max_tokens, etc.)
            estimated_tokens: Estimated token usage

        Returns:
            Tuple of (model_name, estimated_cost, complexity_level)
        """
        context = context or {}
        self.metrics["total_requests"] += 1

        # Determine task complexity
        complexity = self._assess_complexity(task_type, context)

        # Check for priority overrides
        if context.get("priority") == "critical":
            complexity = TaskComplexity.CRITICAL
        elif context.get("force_premium"):
            complexity = TaskComplexity.COMPLEX

        # Get model recommendation
        recommendation = self.MODEL_RECOMMENDATIONS[complexity]

        # Use Ollama if enabled (100% FREE local inference!)
        if self.use_ollama and "ollama" in recommendation:
            model = recommendation["ollama"]
            self.metrics["ollama_uses"] += 1
            logger.info(
                "Using Ollama for zero-cost local inference",
                model=model,
                complexity=complexity.value,
            )
        else:
            model = recommendation["primary"]

            # Check if fallback needed (e.g., API unavailable)
            if context.get("prefer_fallback"):
                model = recommendation["fallback"]

        # Calculate estimated cost
        cost_per_1k = MODEL_COSTS.get(model, 0.045)
        estimated_cost = (estimated_tokens / 1000) * cost_per_1k

        # ── Spending cap enforcement ──────────────────────────────────
        # If using a paid cloud model and budget is exceeded, fall back
        # to Ollama to prevent runaway costs.
        is_cloud_model = not model.startswith("ollama/") and cost_per_1k > 0
        if is_cloud_model:
            self._session_cloud_spend += estimated_cost
            if self._session_cloud_spend > self._monthly_spend_limit:
                if not self._budget_exceeded_logged:
                    logger.critical(
                        f"[BUDGET] Monthly spend limit (${self._monthly_spend_limit:.2f}) "
                        f"exceeded — session spend: ${self._session_cloud_spend:.2f}. "
                        f"Blocking cloud API calls; falling back to Ollama."
                    )
                    self._budget_exceeded_logged = True
                # Fall back to Ollama if available
                if "ollama" in recommendation:
                    model = recommendation["ollama"]
                    estimated_cost = 0.0
                    self.metrics["ollama_uses"] += 1
                else:
                    logger.error(
                        "[BUDGET] Spend limit exceeded and no Ollama fallback. "
                        "Request will proceed but may incur charges."
                    )

        # Calculate savings vs. always using GPT-4
        premium_cost = (estimated_tokens / 1000) * MODEL_COSTS["gpt-4-turbo"]
        cost_saved = premium_cost - estimated_cost

        # Update metrics
        if recommendation["tier"] == ModelTier.BUDGET:
            self.metrics["budget_model_uses"] += 1
        else:
            self.metrics["premium_model_uses"] += 1

        self.metrics["estimated_cost_actual"] += estimated_cost
        self.metrics["estimated_cost_premium_baseline"] += premium_cost
        self.metrics["estimated_cost_saved"] += cost_saved

        logger.info(
            "Model routed",
            task_type=task_type,
            complexity=complexity.value,
            model=model,
            estimated_cost=round(estimated_cost, 4),
            cost_saved=round(cost_saved, 4),
            tier=recommendation["tier"].value,
        )

        return model, estimated_cost, complexity

    def _assess_complexity(self, task_type: str, context: Dict[str, Any]) -> TaskComplexity:
        """
        Assess task complexity based on type and context.

        Args:
            task_type: Task type string
            context: Task context

        Returns:
            TaskComplexity enum value
        """
        task_lower = task_type.lower()

        # Check for critical keywords (highest priority)
        if any(keyword in task_lower for keyword in self.TASK_COMPLEXITY["critical"]):
            return TaskComplexity.CRITICAL

        # Check context for complexity hints
        if context.get("requires_reasoning"):
            return TaskComplexity.COMPLEX

        if context.get("max_tokens", 1000) > 2000:
            # Long outputs usually need better models
            return TaskComplexity.COMPLEX

        # Check task type keywords
        if any(keyword in task_lower for keyword in self.TASK_COMPLEXITY["simple"]):
            return TaskComplexity.SIMPLE

        if any(keyword in task_lower for keyword in self.TASK_COMPLEXITY["medium"]):
            return TaskComplexity.MEDIUM

        if any(keyword in task_lower for keyword in self.TASK_COMPLEXITY["complex"]):
            return TaskComplexity.COMPLEX

        # Default to medium complexity
        return TaskComplexity.MEDIUM

    def get_model_cost(self, model: str) -> float:
        """Get cost per 1K tokens for a model."""
        return MODEL_COSTS.get(model, 0.045)

    def get_max_tokens(self, task_type: str, context: Optional[Dict[str, Any]] = None) -> int:
        """
        Get maximum token limit for a task type.

        Prevents over-generation and reduces costs by limiting output length
        based on task requirements.

        Args:
            task_type: Type of task (e.g., "summarize", "analyze", "create")
            context: Additional context that may override limits

        Returns:
            Maximum token limit for the task

        Example:
            >>> router.get_max_tokens("summarize")
            150
            >>> router.get_max_tokens("create", {"override_tokens": 2000})
            2000
        """
        context = context or {}

        # Check for explicit override
        if "max_tokens" in context:
            return context["max_tokens"]
        if "override_tokens" in context:
            return context["override_tokens"]

        # Extract task keyword from task_type string
        task_lower = task_type.lower()

        # Find matching task type
        for task_keyword, max_tokens in MAX_TOKENS_BY_TASK.items():
            if task_keyword in task_lower:
                logger.debug(
                    "Token limit applied",
                    task_type=task_type,
                    max_tokens=max_tokens,
                    reason=f"matched_{task_keyword}",
                )
                return max_tokens

        # Return default if no match found
        default = MAX_TOKENS_BY_TASK["default"]
        logger.debug(
            "Token limit applied", task_type=task_type, max_tokens=default, reason="default"
        )
        return default

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get routing metrics and cost savings.

        Returns:
            Dictionary with routing statistics
        """
        total = self.metrics["total_requests"]
        budget_pct = (self.metrics["budget_model_uses"] / total * 100) if total > 0 else 0

        return {
            "total_requests": total,
            "budget_model_uses": self.metrics["budget_model_uses"],
            "budget_model_percentage": round(budget_pct, 1),
            "premium_model_uses": self.metrics["premium_model_uses"],
            "estimated_cost_actual": round(self.metrics["estimated_cost_actual"], 2),
            "estimated_cost_premium_baseline": round(
                self.metrics["estimated_cost_premium_baseline"], 2
            ),
            "estimated_cost_saved": round(self.metrics["estimated_cost_saved"], 2),
            "savings_percentage": round(
                (
                    (
                        self.metrics["estimated_cost_saved"]
                        / self.metrics["estimated_cost_premium_baseline"]
                        * 100
                    )
                    if self.metrics["estimated_cost_premium_baseline"] > 0
                    else 0
                ),
                1,
            ),
        }

    def reset_metrics(self):
        """Reset metrics counters."""
        self.metrics = {
            "total_requests": 0,
            "budget_model_uses": 0,
            "premium_model_uses": 0,
            "estimated_cost_saved": 0.0,
            "estimated_cost_actual": 0.0,
            "estimated_cost_premium_baseline": 0.0,
        }
        logger.info("Model router metrics reset")

    def recommend_model_for_budget(
        self, remaining_budget: float, estimated_tokens: int
    ) -> Optional[str]:
        """
        Recommend cheapest model that fits within budget.

        Args:
            remaining_budget: Remaining budget in dollars
            estimated_tokens: Estimated tokens needed

        Returns:
            Model name or None if budget insufficient
        """
        # Sort models by cost
        sorted_models = sorted(MODEL_COSTS.items(), key=lambda x: x[1])

        for model, cost_per_1k in sorted_models:
            estimated_cost = (estimated_tokens / 1000) * cost_per_1k
            if estimated_cost <= remaining_budget:
                logger.info(
                    "Budget-friendly model recommended",
                    model=model,
                    estimated_cost=estimated_cost,
                    remaining_budget=remaining_budget,
                )
                return model

        logger.warning(
            "No model fits budget",
            remaining_budget=remaining_budget,
            estimated_tokens=estimated_tokens,
        )
        return None


# Singleton instance
_model_router: Optional[ModelRouter] = None


def get_model_router() -> Optional[ModelRouter]:
    """Get the global model router instance."""
    return _model_router


def initialize_model_router(default_model: str = "ollama/qwen3:8b") -> ModelRouter:
    """
    Initialize the global model router.

    Args:
        default_model: Default fallback model

    Returns:
        Initialized ModelRouter instance
    """
    global _model_router
    _model_router = ModelRouter(default_model=default_model)
    logger.info("Global model router initialized")
    return _model_router


def get_model_for_phase(
    phase: str, model_selections: Dict[str, str], quality_preference: str
) -> str:
    """
    Get the appropriate LLM model for a given generation phase.

    Moved from routes.task_routes to break the backwards service→route dependency.

    Args:
        phase: Generation phase ('draft', 'assess', 'refine', 'finalize')
        model_selections: User's per-phase model selections (e.g., {"draft": "gpt-4"})
        quality_preference: Fallback preference (fast, balanced, quality)

    Returns:
        Model identifier string (e.g., "gpt-4", "ollama/gpt-oss:20b")
    """
    # Phase-differentiated model tiers (#196):
    # - research/assess/finalize: simple filtering/classification → fast 8B model
    # - outline: structural planning → fast 8B model
    # - draft/refine: creative generation & editing → best available model
    # - assess: QA critique uses a DIFFERENT model family for genuine diversity
    #
    # Hardware: RTX 5090 32GB VRAM + 64GB RAM (R9 9950X3D)
    # Models: qwen3.5:35b (prose), gemma3:27b (critique), qwen3:8b (fast tasks)
    defaults_by_phase = {
        "fast": {
            # All phases use the smallest model for maximum speed
            "research": "ollama/qwen3:8b",
            "outline": "ollama/qwen3:8b",
            "draft": "ollama/qwen3:8b",
            "assess": "ollama/qwen3:8b",
            "refine": "ollama/qwen3:8b",
            "finalize": "ollama/qwen3:8b",
        },
        "balanced": {
            # Draft/refine get best prose model; assess uses different family for diversity
            "research": "ollama/qwen3:8b",  # SIMPLE: filtering/ranking snippets
            "outline": "ollama/qwen3:8b",  # SIMPLE: structural planning
            "draft": "ollama/qwen3.5:35b",  # COMPLEX: primary creative generation (best prose)
            "assess": "ollama/gemma3:27b",  # QA: different model family catches different issues
            "refine": "ollama/qwen3.5:35b",  # COMPLEX: editing needs same quality as draft
            "finalize": "ollama/qwen3:8b",  # SIMPLE: cleanup and formatting
        },
        "quality": {
            # All creative phases get best model; assess uses large alternative
            "research": "ollama/qwen3:8b",  # SIMPLE: filtering/ranking snippets
            "outline": "ollama/qwen3.5:35b",  # Better outlines → better drafts
            "draft": "ollama/qwen3.5:35b",  # COMPLEX: primary creative generation
            "assess": "ollama/gemma3:27b",  # QA: different model family for genuine critique
            "refine": "ollama/qwen3.5:35b",  # COMPLEX: polish and improve draft
            "finalize": "ollama/qwen3:8b",  # SIMPLE: cleanup and formatting
        },
    }

    if model_selections and phase in model_selections:
        selected = model_selections[phase]
        if selected and selected != "auto":
            logger.info(f"[MODEL_ROUTER] Using selected model for {phase}: {selected}")
            return selected

    quality = quality_preference or "balanced"
    if quality not in defaults_by_phase:
        quality = "balanced"

    model = defaults_by_phase[quality].get(phase, "ollama/qwen3:8b")
    logger.info(f"[MODEL_ROUTER] Using {quality} quality model for {phase}: {model}")
    return model
