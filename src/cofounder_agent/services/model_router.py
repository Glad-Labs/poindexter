"""
Smart Model Router — selects Ollama models based on task complexity.

Ollama-only policy: all inference runs locally on RTX 5090 32GB VRAM.
HuggingFace is the emergency fallback if Ollama is down.
"""

import os
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from services.logger_config import get_logger

from .model_constants import MODEL_COSTS

logger = get_logger(__name__)


class ModelProvider(str, Enum):
    """AI model provider types."""

    OLLAMA = "ollama"
    HUGGINGFACE = "huggingface"


class TaskComplexity(str, Enum):
    """Task complexity levels for model routing."""

    SIMPLE = "simple"  # qwen3:8b — extraction, summarization
    MEDIUM = "medium"  # gemma3:27b — analysis, critique
    COMPLEX = "complex"  # qwen3.5:35b — creative generation
    CRITICAL = "critical"  # qwen3.5:122b — top-tier (CPU offload)


# Token limits by task type (reduces over-generation)
MAX_TOKENS_BY_TASK = {
    # Simple tasks
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
    # Medium tasks
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
    # Complex tasks
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
    # Critical tasks
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
    """Routes AI requests to appropriate Ollama models based on task complexity."""

    # Task type complexity mapping
    TASK_COMPLEXITY = {
        "simple": [
            "summarize", "summary", "extract", "classify", "categorize",
            "format", "translate", "convert", "list", "count", "filter",
            "find", "search", "lookup", "get", "fetch", "retrieve",
        ],
        "medium": [
            "analyze", "compare", "review", "evaluate", "assess",
            "recommend", "suggest", "advise", "explain", "describe",
            "interpret", "clarify", "elaborate", "outline", "draft",
        ],
        "complex": [
            "create", "generate", "design", "architect", "plan",
            "strategize", "optimize", "refactor", "implement", "build",
            "develop", "engineer", "code", "program", "debug",
        ],
        "critical": [
            "legal", "contract", "compliance", "security", "audit",
            "financial", "regulatory", "risk", "sensitive", "confidential",
        ],
    }

    # Model recommendations by complexity (Ollama-only)
    MODEL_RECOMMENDATIONS = {
        TaskComplexity.SIMPLE: {
            "model": "ollama/qwen3:8b",
        },
        TaskComplexity.MEDIUM: {
            "model": "ollama/gemma3:27b",
        },
        TaskComplexity.COMPLEX: {
            "model": "ollama/qwen3.5:35b",
        },
        TaskComplexity.CRITICAL: {
            "model": "ollama/qwen3.5:122b",
        },
    }

    def __init__(self, default_model: str = "ollama/qwen3:8b", use_ollama: bool | None = None):
        self.default_model = default_model

        # Check USE_OLLAMA environment variable if not explicitly set
        if use_ollama is None:
            try:
                from services.site_config import site_config
                use_ollama = site_config.get("use_ollama", "false").lower() == "true"
            except Exception:
                use_ollama = False

        self.use_ollama = use_ollama

        # Metrics
        self.metrics = {
            "total_requests": 0,
            "budget_model_uses": 0,
            "premium_model_uses": 0,
            "estimated_cost_saved": 0.0,
            "estimated_cost_actual": 0.0,
            "estimated_cost_premium_baseline": 0.0,
            "ollama_uses": 0,
        }

        # Spending cap — tracks in-memory estimated spend (resets on restart).
        # For persistent tracking, use cost_aggregation_service.get_budget_status().
        try:
            from services.site_config import site_config
            self._monthly_spend_limit = float(site_config.get("monthly_spend_limit", "100.0"))
        except Exception:
            self._monthly_spend_limit = 100.0
        self._session_cloud_spend = 0.0
        self._budget_exceeded_logged = False

        # Runtime provider failure tracking.
        self._provider_consecutive_failures: Dict[str, int] = {}
        self._FAILURE_ALERT_THRESHOLD = 5

        logger.info(
            "Model router initialized", default_model=default_model, use_ollama=self.use_ollama
        )

    async def seed_spend_from_db(self, pool) -> None:
        """Seed the in-memory spend counter from the cost_logs table."""
        try:
            row = await pool.fetchrow(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total "
                "FROM cost_logs "
                "WHERE created_at >= date_trunc('month', NOW())"
            )
            if row:
                self._session_cloud_spend = float(row["total"])
                logger.info(
                    "[BUDGET] Seeded session spend from cost_logs",
                    month_to_date_spend=self._session_cloud_spend,
                    monthly_limit=self._monthly_spend_limit,
                )
                if self._session_cloud_spend > self._monthly_spend_limit:
                    logger.critical(
                        "[BUDGET_EXCEEDED] Month-to-date spend ($%.2f) already exceeds limit ($%.2f) at startup.",
                        self._session_cloud_spend,
                        self._monthly_spend_limit,
                    )
                    self._budget_exceeded_logged = True
        except Exception:
            logger.error(
                "[BUDGET] Failed to seed spend from cost_logs — "
                "starting with $0.00 (spend cap may be inaccurate)",
                exc_info=True,
            )

    def record_provider_failure(self, provider: str) -> None:
        """Record a runtime LLM call failure. Emits critical log at threshold."""
        count = self._provider_consecutive_failures.get(provider, 0) + 1
        self._provider_consecutive_failures[provider] = count
        if count >= self._FAILURE_ALERT_THRESHOLD:
            logger.critical(
                "[llm_provider] Provider %r has failed %d consecutive times -- possible outage or quota exhaustion",
                provider,
                count,
            )

    def record_provider_success(self, provider: str) -> None:
        """Record a successful LLM call. Resets consecutive failure counter."""
        if self._provider_consecutive_failures.get(provider, 0) > 0:
            logger.info(
                "[llm_provider] Provider %r recovered after %d consecutive failures",
                provider,
                self._provider_consecutive_failures[provider],
            )
        self._provider_consecutive_failures[provider] = 0

    def get_provider_health(self) -> Dict[str, Any]:
        """Return provider names mapped to their current failure counts."""
        return {
            provider: {"consecutive_failures": count}
            for provider, count in self._provider_consecutive_failures.items()
        }

    def route_request(
        self, task_type: str, context: Optional[Dict[str, Any]] = None, estimated_tokens: int = 1000
    ) -> Tuple[str, float, TaskComplexity]:
        """
        Route request to appropriate Ollama model based on task complexity.

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
        model = recommendation["model"]
        self.metrics["ollama_uses"] += 1

        # Calculate estimated cost (zero for Ollama)
        cost_per_1k = MODEL_COSTS.get(model, 0.0)
        estimated_cost = (estimated_tokens / 1000) * cost_per_1k

        # Update metrics
        self.metrics["budget_model_uses"] += 1
        self.metrics["estimated_cost_actual"] += estimated_cost

        logger.info(
            "Model routed",
            task_type=task_type,
            complexity=complexity.value,
            model=model,
            estimated_cost=round(estimated_cost, 4),
        )

        return model, estimated_cost, complexity

    def _assess_complexity(self, task_type: str, context: Dict[str, Any]) -> TaskComplexity:
        """Assess task complexity based on type and context."""
        task_lower = task_type.lower()

        # Check for critical keywords (highest priority)
        if any(keyword in task_lower for keyword in self.TASK_COMPLEXITY["critical"]):
            return TaskComplexity.CRITICAL

        # Check context for complexity hints
        if context.get("requires_reasoning"):
            return TaskComplexity.COMPLEX

        if context.get("max_tokens", 1000) > 2000:
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
        return MODEL_COSTS.get(model, 0.0)

    def get_max_tokens(self, task_type: str, context: Optional[Dict[str, Any]] = None) -> int:
        """Get maximum token limit for a task type."""
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
        """Get routing metrics."""
        total = self.metrics["total_requests"]
        return {
            "total_requests": total,
            "budget_model_uses": self.metrics["budget_model_uses"],
            "premium_model_uses": self.metrics["premium_model_uses"],
            "ollama_uses": self.metrics["ollama_uses"],
            "estimated_cost_actual": round(self.metrics["estimated_cost_actual"], 2),
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
            "ollama_uses": 0,
        }
        logger.info("Model router metrics reset")

    def recommend_model_for_budget(
        self, remaining_budget: float, estimated_tokens: int
    ) -> Optional[str]:
        """Recommend cheapest model that fits within budget."""
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
    """Initialize the global model router."""
    global _model_router
    _model_router = ModelRouter(default_model=default_model)
    logger.info("Global model router initialized")
    return _model_router


def get_model_for_phase(
    phase: str, model_selections: Dict[str, str], quality_preference: str
) -> str:
    """
    Get the appropriate Ollama model for a given generation phase.

    Hardware: RTX 5090 32GB VRAM + 64GB RAM (R9 9950X3D)
    Models: qwen3.5:35b (prose), gemma3:27b (critique), qwen3:8b (fast tasks)
    """
    # Phase-differentiated model tiers:
    # - research/assess/finalize: simple filtering/classification -> fast 8B model
    # - outline: structural planning -> fast 8B model
    # - draft/refine: creative generation & editing -> best available model
    # - assess: QA critique uses a DIFFERENT model family for genuine diversity
    defaults_by_phase = {
        "fast": {
            "research": "ollama/qwen3:8b",
            "outline": "ollama/qwen3:8b",
            "draft": "ollama/qwen3:8b",
            "assess": "ollama/qwen3:8b",
            "refine": "ollama/qwen3:8b",
            "finalize": "ollama/qwen3:8b",
        },
        "balanced": {
            "research": "ollama/qwen3:8b",
            "outline": "ollama/qwen3:8b",
            "draft": "ollama/qwen3.5:35b",
            "assess": "ollama/gemma3:27b",
            "refine": "ollama/qwen3.5:35b",
            "finalize": "ollama/qwen3:8b",
        },
        "quality": {
            "research": "ollama/qwen3:8b",
            "outline": "ollama/qwen3.5:35b",
            "draft": "ollama/qwen3.5:35b",
            "assess": "ollama/gemma3:27b",
            "refine": "ollama/qwen3.5:35b",
            "finalize": "ollama/qwen3:8b",
        },
    }

    if model_selections and phase in model_selections:
        selected = model_selections[phase]
        if selected and selected != "auto":
            logger.info("[MODEL_ROUTER] Using selected model for %s: %s", phase, selected)
            return selected

    quality = quality_preference or "balanced"
    if quality not in defaults_by_phase:
        quality = "balanced"

    model = defaults_by_phase[quality].get(phase, "ollama/qwen3:8b")
    logger.info("[MODEL_ROUTER] Using %s quality model for %s: %s", quality, phase, model)
    return model
