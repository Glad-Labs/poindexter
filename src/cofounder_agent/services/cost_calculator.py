"""
Cost Calculator Service - Calculates task costs using model-aware pricing.

Uses MODEL_COSTS from model_router.py for accurate cost estimates based on:
- Model selection (Ollama, GPT-3.5, GPT-4, Claude, etc.)
- Token usage (input + output tokens)
- Task complexity tier (affects model selection)

Replaces hardcoded cost values ($0.03, $0.02) with real LLM pricing.
"""

import logging
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)


@dataclass
class CostBreakdown:
    """Cost breakdown for a task"""

    by_phase: Dict[str, float]  # {phase: cost, ...}
    by_model: Dict[str, float]  # {model: total_cost, ...}
    total_cost: float
    token_count: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "by_phase": self.by_phase,
            "by_model": self.by_model,
            "total_cost": round(self.total_cost, 6),
            "token_count": self.token_count,
        }


class CostCalculator:
    """
    Cost calculator for tasks using real LLM pricing.

    Integrates with model_router.py MODEL_COSTS for accurate pricing.
    Supports all models: Ollama (free), budget models, standard, premium.
    """

    # Estimated tokens per phase (based on typical content generation)
    PHASE_TOKEN_ESTIMATES = {
        "research": 2000,  # Research phase uses 2K tokens avg
        "outline": 1000,  # Outline is shorter
        "draft": 3000,  # Draft generates most content
        "assess": 1500,  # Assessment/QA
        "refine": 2000,  # Refinement
        "finalize": 1000,  # Final polish
        "image_selection": 500,  # Image description generation
    }

    # Model costs per 1K tokens (from model_router.py)
    MODEL_COSTS = {
        # OpenAI models
        "gpt-4-turbo": 0.045,
        "gpt-4": 0.045,
        "gpt-3.5-turbo": 0.00175,
        # Anthropic Claude models
        "claude-opus-3": 0.045,
        "claude-sonnet-3": 0.015,
        "claude-haiku-3": 0.0010,
        "claude-instant": 0.0016,
        # Ollama models (FREE)
        "ollama/llama2": 0.0,
        "ollama/llama2:13b": 0.0,
        "ollama/llama2:70b": 0.0,
        "ollama/mistral": 0.0,
        "ollama/mixtral": 0.0,
        "ollama/codellama": 0.0,
        "ollama/phi": 0.0,
        # Default fallback
        "unknown": 0.001,
    }

    def __init__(self):
        """Initialize cost calculator with model pricing"""
        logger.info("CostCalculator initialized with MODEL_COSTS")

    def calculate_phase_cost(
        self, phase: str, model: str, token_count: Optional[int] = None
    ) -> float:
        """
        Calculate cost for a single phase.

        Args:
            phase: Phase name (research, draft, etc.)
            model: Model name (gpt-4, ollama/mistral, etc.)
            token_count: Actual token count (uses estimate if None)

        Returns:
            Cost in USD (float)
        """
        # Use provided token count or estimate
        tokens = token_count or self.PHASE_TOKEN_ESTIMATES.get(phase, 1000)

        # Get cost per 1K tokens
        cost_per_1k = self._get_cost_per_1k(model)

        # Calculate total cost
        total_cost = (tokens / 1000) * cost_per_1k

        logger.debug(
            f"Phase cost: phase={phase}, model={model}, tokens={tokens}, "
            f"cost_per_1k=${cost_per_1k:.6f}, total=${total_cost:.6f}"
        )

        return round(total_cost, 6)

    def calculate_task_cost(
        self,
        models_by_phase: Dict[str, str],
        token_counts_by_phase: Optional[Dict[str, int]] = None,
    ) -> CostBreakdown:
        """
        Calculate total cost for a task with per-phase model selections.

        Args:
            models_by_phase: {phase: model_name, ...}
            token_counts_by_phase: Optional {phase: token_count, ...}

        Returns:
            CostBreakdown with total, by_phase, and by_model breakdowns
        """
        by_phase = {}
        by_model = {}
        total_cost = 0.0
        total_tokens = 0

        # Calculate cost for each phase
        for phase, model in models_by_phase.items():
            token_count = None
            if token_counts_by_phase and phase in token_counts_by_phase:
                token_count = token_counts_by_phase[phase]

            phase_cost = self.calculate_phase_cost(phase, model, token_count)
            by_phase[phase] = phase_cost
            total_cost += phase_cost

            # Use estimate for token counting if not provided
            tokens = token_count or self.PHASE_TOKEN_ESTIMATES.get(phase, 1000)
            total_tokens += tokens

            # Track by model
            if model in by_model:
                by_model[model] += phase_cost
            else:
                by_model[model] = phase_cost

        logger.info(
            f"Task cost calculated: total=${total_cost:.6f}, phases={len(by_phase)}, "
            f"tokens={total_tokens}, by_model={by_model}"
        )

        return CostBreakdown(
            by_phase=by_phase, by_model=by_model, total_cost=total_cost, token_count=total_tokens
        )

    def calculate_cost_with_defaults(
        self, quality_preference: str, content_type: str = "blog_post"
    ) -> CostBreakdown:
        """
        Calculate cost using default model selections based on quality preference.

        Args:
            quality_preference: "fast", "balanced", or "quality"
            content_type: Type of content (blog_post, social, email, etc.)

        Returns:
            CostBreakdown with estimated costs
        """
        # Select default models based on quality preference
        models_by_phase = self._select_default_models(quality_preference, content_type)
        return self.calculate_task_cost(models_by_phase)

    def estimate_cost_range(self, quality_preference: str) -> Tuple[float, float]:
        """
        Get cost range (min/max) for a quality preference.

        Args:
            quality_preference: "fast", "balanced", or "quality"

        Returns:
            Tuple of (min_cost, max_cost)
        """
        breakdown = self.calculate_cost_with_defaults(quality_preference)
        total = breakdown.total_cost

        # Return range with ±20% variance
        min_cost = max(0.0, total * 0.8)
        max_cost = total * 1.2

        return (round(min_cost, 6), round(max_cost, 6))

    def _get_cost_per_1k(self, model: str) -> float:
        """Get cost per 1K tokens for a model"""
        # Normalize model name (remove version suffixes)
        normalized_model = model.split(":")[0] if ":" in model else model

        # Look up in MODEL_COSTS
        if normalized_model in self.MODEL_COSTS:
            return self.MODEL_COSTS[normalized_model]

        # Try full model name
        if model in self.MODEL_COSTS:
            return self.MODEL_COSTS[model]

        # Log warning and return default
        logger.warning(f"Model {model} not found in MODEL_COSTS, using default $0.001")
        return self.MODEL_COSTS.get("unknown", 0.001)

    def _select_default_models(
        self, quality_preference: str, content_type: str = "blog_post"
    ) -> Dict[str, str]:
        """
        Select default models based on quality preference.

        Args:
            quality_preference: "fast" (Ollama), "balanced" (GPT-3.5), "quality" (GPT-4)
            content_type: Type of content (affects phase selection)

        Returns:
            Dict of {phase: model_name}
        """
        phases = ["research", "outline", "draft", "assess", "refine", "finalize"]

        if quality_preference == "fast":
            # All Ollama (zero cost)
            return {phase: "ollama/mistral" for phase in phases}

        elif quality_preference == "balanced":
            # Mix: Ollama for simple, GPT-3.5 for complex
            return {
                "research": "ollama/mistral",  # Free research
                "outline": "ollama/mistral",  # Free outline
                "draft": "gpt-3.5-turbo",  # Budget draft
                "assess": "ollama/mistral",  # Free QA
                "refine": "gpt-3.5-turbo",  # Budget refinement
                "finalize": "ollama/mistral",  # Free finalize
            }

        else:  # "quality"
            # Use GPT-4 for complex, GPT-3.5 for simple
            return {
                "research": "gpt-3.5-turbo",  # Budget research
                "outline": "gpt-3.5-turbo",  # Budget outline
                "draft": "gpt-4",  # Premium draft (most important)
                "assess": "gpt-3.5-turbo",  # Budget QA
                "refine": "gpt-4",  # Premium refinement
                "finalize": "gpt-3.5-turbo",  # Budget finalize
            }


# Singleton instance for use throughout the application
_cost_calculator = None


def get_cost_calculator() -> CostCalculator:
    """Get or create singleton cost calculator instance"""
    global _cost_calculator
    if _cost_calculator is None:
        _cost_calculator = CostCalculator()
    return _cost_calculator
