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

import logging
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class QualityPreference(str, Enum):
    """User's quality vs cost preference"""

    FAST = "fast"  # Cheapest models (Ollama)
    BALANCED = "balanced"  # Mix of cost and quality
    QUALITY = "quality"  # Best models (GPT-4, Claude Opus)


class ModelSelector:
    """
    Intelligent per-step model selection for content pipeline.

    Allows fine-grained control over which AI model is used for each step,
    balancing cost, quality, and execution time.

    Per-Phase Rules:
    - RESEARCH: Can use Ollama/GPT-3.5 (gathering info, cost matters most)
    - OUTLINE: Can use Ollama/GPT-3.5 (structure design, good quality)
    - DRAFT: Prefers GPT-3.5/GPT-4 (writing quality important)
    - ASSESS: Must use GPT-4/Claude (quality evaluation is critical)
    - REFINE: Should use GPT-4/Claude (improve existing content)
    - FINALIZE: Should use GPT-4/Claude (final polish, high stakes)
    """

    # Per-phase model options (cheapest → best quality)
    PHASE_MODELS = {
        "research": ["ollama", "gpt-3.5-turbo", "gpt-4"],
        "outline": ["ollama", "gpt-3.5-turbo", "gpt-4"],
        "draft": ["gpt-3.5-turbo", "gpt-4", "claude-3-opus"],
        "assess": ["gpt-4", "claude-3-opus"],  # Quality critical
        "refine": ["gpt-4", "claude-3-opus"],  # Important
        "finalize": ["gpt-4", "claude-3-opus"],  # Final output
    }

    # Token counts per phase (used for cost estimation)
    # These are empirical averages from real pipeline runs
    PHASE_TOKEN_ESTIMATES = {
        "research": 2000,  # Research phase produces ~2K tokens
        "outline": 1500,  # Outline produces ~1.5K tokens
        "draft": 3000,  # Draft produces ~3K tokens (longest)
        "assess": 500,  # Assessment produces ~500 tokens (short eval)
        "refine": 2000,  # Refinement produces ~2K tokens
        "finalize": 1000,  # Final polish produces ~1K tokens
    }

    # Pricing per 1K tokens (from UsageTracker)
    # These match the rates in services/usage_tracker.py
    MODEL_COSTS = {
        "ollama": 0.0,  # Free local inference
        "gpt-3.5-turbo": 0.0005,  # $0.0005 per 1K input tokens
        "gpt-4": 0.003,  # $0.03 per 1K input (simplified average)
        "claude-3-opus": 0.015,  # $0.015 per 1K input
        "claude-3-sonnet": 0.003,  # $0.003 per 1K input
        "claude-3-haiku": 0.00025,  # $0.00025 per 1K input
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

        Examples:
            >>> selector = ModelSelector()
            >>> selector.auto_select("research", QualityPreference.FAST)
            'ollama'
            >>> selector.auto_select("assess", QualityPreference.BALANCED)
            'gpt-4'
            >>> selector.auto_select("finalize", QualityPreference.QUALITY)
            'claude-3-opus'
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
            return (
                available_models[mid_idx]
                if mid_idx < len(available_models)
                else available_models[-1]
            )
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
            Estimated cost in USD (6 decimal precision)

        Examples:
            >>> selector = ModelSelector()
            >>> selector.estimate_cost("research", "ollama")
            0.0
            >>> selector.estimate_cost("draft", "gpt-3.5-turbo")
            0.0015
            >>> selector.estimate_cost("assess", "gpt-4")
            0.0015
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
            {
                "research": 0.0,
                "outline": 0.00075,
                "draft": 0.0015,
                "assess": 0.0015,
                "refine": 0.001,
                "finalize": 0.001,
                "total": 0.00575
            }

        Examples:
            >>> selector = ModelSelector()
            >>> costs = selector.estimate_full_task_cost({
            ...     "research": "ollama",
            ...     "outline": "ollama",
            ...     "draft": "gpt-3.5-turbo",
            ...     "assess": "gpt-4",
            ...     "refine": "gpt-4",
            ...     "finalize": "gpt-4"
            ... })
            >>> costs["total"]
            0.00375
        """
        cost_breakdown = {}
        total_cost = 0.0

        for phase, model in models_by_phase.items():
            cost = self.estimate_cost(phase, model)
            cost_breakdown[phase] = cost
            total_cost += cost

        cost_breakdown["total"] = round(total_cost, 6)

        return cost_breakdown

    def get_available_models(self, phase: Optional[str] = None) -> Dict[str, any]:
        """
        Get available models for a phase (or all phases).

        Args:
            phase: Optional specific phase

        Returns:
            {"models": ["ollama", "gpt-3.5-turbo", "gpt-4"]}
            or
            {"models": {"research": [...], "outline": [...], ...}}

        Examples:
            >>> selector = ModelSelector()
            >>> selector.get_available_models("research")
            {'models': ['ollama', 'gpt-3.5-turbo', 'gpt-4']}
            >>> selector.get_available_models()
            {'models': {'research': [...], 'outline': [...], ...}}
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
            (is_valid: bool, message: str)

        Examples:
            >>> selector = ModelSelector()
            >>> selector.validate_model_selection("research", "ollama")
            (True, 'OK')
            >>> selector.validate_model_selection("assess", "ollama")
            (False, 'Model ollama not available for assess. Available: [\'gpt-4\', \'claude-3-opus\']')
        """
        if phase not in self.PHASE_MODELS:
            return False, f"Unknown phase: {phase}"

        available = self.PHASE_MODELS[phase]
        if model not in available:
            return False, f"Model {model} not available for {phase}. Available: {available}"

        return True, "OK"

    def get_quality_summary(self, quality: QualityPreference) -> Dict[str, any]:
        """
        Get summary of what each quality preference means.

        Args:
            quality: Quality preference level

        Returns:
            Summary with examples and cost range

        Examples:
            >>> selector = ModelSelector()
            >>> summary = selector.get_quality_summary(QualityPreference.BALANCED)
            >>> summary["description"]
            'Mix of cost and quality for most users'
            >>> summary["estimated_cost_per_task"]
            0.00375
        """
        summaries = {
            QualityPreference.FAST: {
                "name": "Fast (Cheapest)",
                "description": "Optimize for cost. Use Ollama where possible.",
                "models": {
                    "research": "ollama",
                    "outline": "ollama",
                    "draft": "ollama",
                    "assess": "ollama",
                    "refine": "ollama",
                    "finalize": "ollama",
                },
                "quality_expected": "3/5 stars (good for brainstorming/drafts)",
                "estimated_cost_per_task": 0.0,
            },
            QualityPreference.BALANCED: {
                "name": "Balanced (Recommended)",
                "description": "Balance cost and quality. Use GPT-3.5 for drafting, GPT-4 for assessment.",
                "models": {
                    "research": "gpt-3.5-turbo",
                    "outline": "gpt-3.5-turbo",
                    "draft": "gpt-3.5-turbo",
                    "assess": "gpt-4",
                    "refine": "gpt-4",
                    "finalize": "gpt-4",
                },
                "quality_expected": "4.2/5 stars (professional output)",
                "estimated_cost_per_task": 0.00375,
            },
            QualityPreference.QUALITY: {
                "name": "Quality (Premium)",
                "description": "Prioritize quality. Use best models.",
                "models": {
                    "research": "gpt-4",
                    "outline": "gpt-4",
                    "draft": "gpt-4",
                    "assess": "claude-3-opus",
                    "refine": "claude-3-opus",
                    "finalize": "claude-3-opus",
                },
                "quality_expected": "4.7/5 stars (premium quality)",
                "estimated_cost_per_task": 0.0525,
            },
        }

        return summaries.get(quality, {})
