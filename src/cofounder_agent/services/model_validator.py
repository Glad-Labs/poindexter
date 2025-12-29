"""
Model Validation Service

Validates that selected LLM models are available before tasks are created.
Ensures users can't select models that don't exist or aren't properly configured.
"""

import logging
from typing import Dict, List, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    """Information about an available model"""

    name: str
    provider: str  # ollama, openai, anthropic, google, huggingface
    available: bool
    cost_per_token: float  # or per-use cost
    latency_ms: Optional[int] = None


class ModelValidator:
    """Validates model selections and checks availability"""

    # Known models and their providers
    KNOWN_MODELS = {
        # Ollama (local)
        "llama2": {"provider": "ollama", "cost": 0.0},
        "llama2:13b": {"provider": "ollama", "cost": 0.0},
        "llama2:70b": {"provider": "ollama", "cost": 0.0},
        "mistral": {"provider": "ollama", "cost": 0.0},
        "neural-chat": {"provider": "ollama", "cost": 0.0},
        "qwen2.5": {"provider": "ollama", "cost": 0.0},
        "qwen": {"provider": "ollama", "cost": 0.0},
        "dolphin-mixtral": {"provider": "ollama", "cost": 0.0},
        "starling-lm": {"provider": "ollama", "cost": 0.0},
        # OpenAI
        "gpt-4": {"provider": "openai", "cost": 0.00003},
        "gpt-4-turbo": {"provider": "openai", "cost": 0.00001},
        "gpt-3.5-turbo": {"provider": "openai", "cost": 0.0000005},
        # Anthropic
        "claude-3-opus": {"provider": "anthropic", "cost": 0.000015},
        "claude-3-sonnet": {"provider": "anthropic", "cost": 0.000003},
        "claude-3-haiku": {"provider": "anthropic", "cost": 0.00000025},
        "claude-2.1": {"provider": "anthropic", "cost": 0.000008},
        # Google
        "gemini-pro": {"provider": "google", "cost": 0.00000125},
        "palm-2": {"provider": "google", "cost": 0.000005},
    }

    # Pipeline phases and their default models
    DEFAULT_MODELS_BY_PHASE = {
        "research": "llama2",
        "outline": "llama2",
        "draft": "mistral",
        "assess": "neural-chat",
        "refine": "mistral",
        "finalize": "llama2",
    }

    PIPELINE_PHASES = {"research", "outline", "draft", "assess", "refine", "finalize"}

    def __init__(self, available_models: Optional[Dict[str, ModelInfo]] = None):
        """
        Initialize validator with available models.

        Args:
            available_models: Dict of model_name -> ModelInfo
                             If None, only checks against KNOWN_MODELS
        """
        self.available_models = available_models or {}
        logger.info(
            f"🔧 ModelValidator initialized with {len(self.available_models)} available models"
        )

    def set_available_models(self, models: List[str]) -> None:
        """Update the list of available models"""
        self.available_models = {
            model: ModelInfo(name=model, provider="unknown", available=True) for model in models
        }
        logger.info(f"✅ Available models updated: {models}")

    def is_model_available(self, model_name: str) -> bool:
        """Check if a model is available"""
        # Check runtime availability first
        if model_name in self.available_models:
            return self.available_models[model_name].available

        # Fall back to known models
        if model_name in self.KNOWN_MODELS:
            return True

        # Check if it's a model tag (e.g., "llama2:13b")
        base_name = model_name.split(":")[0]
        return base_name in self.KNOWN_MODELS

    def validate_model_selection(self, model_name: str) -> tuple[bool, Optional[str]]:
        """
        Validate a single model selection.

        Returns:
            (is_valid, error_message)
        """
        if not model_name or not isinstance(model_name, str):
            return False, f"Model name must be a non-empty string, got: {model_name}"

        model_name = model_name.strip().lower()

        if not self.is_model_available(model_name):
            available = list(self.available_models.keys()) + list(self.KNOWN_MODELS.keys())
            return (
                False,
                f"Model '{model_name}' is not available. Available models: {', '.join(available[:5])}...",
            )

        return True, None

    def validate_models_by_phase(
        self, models_by_phase: Dict[str, str]
    ) -> tuple[bool, Dict[str, str]]:
        """
        Validate all models in a phase-based selection.

        Args:
            models_by_phase: Dict like {
                "research": "llama2",
                "outline": "mistral",
                ...
            }

        Returns:
            (all_valid, errors_dict) where errors_dict maps phase -> error_message
        """
        if not models_by_phase:
            return True, {}

        if not isinstance(models_by_phase, dict):
            return False, {"format": f"Expected dict, got {type(models_by_phase)}"}

        errors = {}
        all_valid = True

        for phase, model in models_by_phase.items():
            # Validate phase name
            if phase not in self.PIPELINE_PHASES:
                errors[phase] = (
                    f"Unknown phase '{phase}'. Valid phases: {', '.join(self.PIPELINE_PHASES)}"
                )
                all_valid = False
                continue

            # Validate model
            is_valid, error_msg = self.validate_model_selection(model)
            if not is_valid:
                errors[phase] = error_msg
                all_valid = False

        return all_valid, errors

    def get_default_models_for_phase(self, phase: str) -> Optional[str]:
        """Get the default model for a pipeline phase"""
        if phase not in self.PIPELINE_PHASES:
            logger.warning(f"⚠️ Unknown phase: {phase}")
            return None

        return self.DEFAULT_MODELS_BY_PHASE.get(phase)

    def get_all_phases(self) -> Set[str]:
        """Get all pipeline phases"""
        return self.PIPELINE_PHASES.copy()

    def estimate_cost_by_phase(
        self, models_by_phase: Dict[str, str], tokens_per_phase: Optional[Dict[str, int]] = None
    ) -> float:
        """
        Estimate total cost for a task with specific model selections.

        Args:
            models_by_phase: Model selections for each phase
            tokens_per_phase: Estimated tokens per phase (optional)

        Returns:
            Estimated total cost in USD
        """
        # Default token estimates per phase (reasonable averages)
        default_tokens = {
            "research": 2000,
            "outline": 1500,
            "draft": 3000,
            "assess": 1000,
            "refine": 2000,
            "finalize": 1000,
        }

        tokens = tokens_per_phase or default_tokens
        total_cost = 0.0

        for phase, model in models_by_phase.items():
            # Get cost per token for this model
            if model in self.available_models:
                cost_per_token = self.available_models[model].cost_per_token
            elif model in self.KNOWN_MODELS:
                cost_per_token = self.KNOWN_MODELS[model].get("cost", 0.0)
            else:
                # Assume it's a known model with standard cost
                base_name = model.split(":")[0]
                cost_per_token = self.KNOWN_MODELS.get(base_name, {}).get("cost", 0.0001)

            phase_tokens = tokens.get(phase, 1000)
            phase_cost = cost_per_token * phase_tokens
            total_cost += phase_cost

            logger.debug(f"  📊 {phase}: {model} × {phase_tokens} tokens = ${phase_cost:.6f}")

        logger.info(f"💰 Estimated total cost: ${total_cost:.6f}")
        return round(total_cost, 6)

    def recommend_models_for_quality_level(self, quality_level: str) -> Dict[str, str]:
        """
        Recommend a set of models based on desired quality level.

        Args:
            quality_level: "budget", "balanced", "quality", "premium"

        Returns:
            Dict of phase -> recommended_model
        """
        recommendations = {
            "budget": {
                "research": "llama2",
                "outline": "llama2",
                "draft": "llama2",
                "assess": "llama2",
                "refine": "llama2",
                "finalize": "llama2",
            },
            "balanced": {
                "research": "mistral",
                "outline": "mistral",
                "draft": "neural-chat",
                "assess": "mistral",
                "refine": "neural-chat",
                "finalize": "mistral",
            },
            "quality": {
                "research": "gpt-4",
                "outline": "gpt-4",
                "draft": "gpt-4",
                "assess": "claude-3-sonnet",
                "refine": "gpt-4",
                "finalize": "gpt-4",
            },
            "premium": {
                "research": "claude-3-opus",
                "outline": "claude-3-opus",
                "draft": "gpt-4",
                "assess": "claude-3-opus",
                "refine": "gpt-4",
                "finalize": "claude-3-opus",
            },
        }

        if quality_level not in recommendations:
            logger.warning(f"⚠️ Unknown quality level: {quality_level}, using 'balanced'")
            quality_level = "balanced"

        return recommendations[quality_level]
