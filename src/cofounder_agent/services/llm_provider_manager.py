"""
LLM Provider Configuration and Management

Supports:
- Ollama (local, free, runs on RTX 5070)
- HuggingFace Inference API (free tier available)
- Google Gemini (fallback)
- Model selection based on availability and cost
"""

import os
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Available LLM providers"""
    OLLAMA = "ollama"
    HUGGINGFACE = "huggingface"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"


class ModelSize(str, Enum):
    """Model size categories for resource planning"""
    SMALL = "small"  # < 3B params (fits on CPU)
    MEDIUM = "medium"  # 3-13B params (needs GPU)
    LARGE = "large"  # 13B-70B params (needs good GPU)


@dataclass
class LLMConfig:
    """Configuration for an LLM model"""
    provider: LLMProvider
    model_name: str
    model_display_name: str
    size: ModelSize
    is_free: bool
    requires_internet: bool
    estimated_vram_gb: float
    description: str
    api_url: Optional[str] = None
    api_key: Optional[str] = None


class LLMProviderManager:
    """Manages LLM provider selection and configuration"""

    # Available models for each provider
    OLLAMA_MODELS = {
        "neural-chat:7b": LLMConfig(
            provider=LLMProvider.OLLAMA,
            model_name="neural-chat:7b",
            model_display_name="Neural Chat 7B (Ollama)",
            size=ModelSize.MEDIUM,
            is_free=True,
            requires_internet=False,
            estimated_vram_gb=7.0,
            description="Fast 7B model, good for content. ~7GB VRAM needed.",
        ),
        "mistral:7b": LLMConfig(
            provider=LLMProvider.OLLAMA,
            model_name="mistral:7b",
            model_display_name="Mistral 7B (Ollama)",
            size=ModelSize.MEDIUM,
            is_free=True,
            requires_internet=False,
            estimated_vram_gb=7.0,
            description="Fast, smart 7B model. ~7GB VRAM needed.",
        ),
        "llama2:7b": LLMConfig(
            provider=LLMProvider.OLLAMA,
            model_name="llama2:7b",
            model_display_name="Llama 2 7B (Ollama)",
            size=ModelSize.MEDIUM,
            is_free=True,
            requires_internet=False,
            estimated_vram_gb=7.0,
            description="Meta's Llama 2 model. ~7GB VRAM needed.",
        ),
        "neural-chat:13b": LLMConfig(
            provider=LLMProvider.OLLAMA,
            model_name="neural-chat:13b",
            model_display_name="Neural Chat 13B (Ollama)",
            size=ModelSize.LARGE,
            is_free=True,
            requires_internet=False,
            estimated_vram_gb=12.0,
            description="Better quality 13B model. ~12GB VRAM needed. Perfect for RTX 5070!",
        ),
        "mistral:13b": LLMConfig(
            provider=LLMProvider.OLLAMA,
            model_name="mistral:13b",
            model_display_name="Mistral 13B (Ollama)",
            size=ModelSize.LARGE,
            is_free=True,
            requires_internet=False,
            estimated_vram_gb=12.0,
            description="High-quality 13B model. ~12GB VRAM needed. Perfect for RTX 5070!",
        ),
    }

    HUGGINGFACE_MODELS = {
        "mistralai/Mistral-7B-Instruct-v0.1": LLMConfig(
            provider=LLMProvider.HUGGINGFACE,
            model_name="mistralai/Mistral-7B-Instruct-v0.1",
            model_display_name="Mistral 7B Instruct (HuggingFace)",
            size=ModelSize.MEDIUM,
            is_free=True,
            requires_internet=True,
            estimated_vram_gb=7.0,
            description="Free tier available on HuggingFace. ~7GB VRAM if local.",
        ),
        "meta-llama/Llama-2-7b-chat": LLMConfig(
            provider=LLMProvider.HUGGINGFACE,
            model_name="meta-llama/Llama-2-7b-chat",
            model_display_name="Llama 2 7B Chat (HuggingFace)",
            size=ModelSize.MEDIUM,
            is_free=True,
            requires_internet=True,
            estimated_vram_gb=7.0,
            description="Free tier available on HuggingFace. Requires HF token.",
        ),
        "tiiuae/falcon-7b-instruct": LLMConfig(
            provider=LLMProvider.HUGGINGFACE,
            model_name="tiiuae/falcon-7b-instruct",
            model_display_name="Falcon 7B Instruct (HuggingFace)",
            size=ModelSize.MEDIUM,
            is_free=True,
            requires_internet=True,
            estimated_vram_gb=7.0,
            description="Free tier available. Apache 2.0 licensed.",
        ),
    }

    GEMINI_MODELS = {
        "gemini-2.5-flash": LLMConfig(
            provider=LLMProvider.GEMINI,
            model_name="gemini-2.5-flash",
            model_display_name="Gemini 2.5 Flash (Google)",
            size=ModelSize.LARGE,
            is_free=False,
            requires_internet=True,
            estimated_vram_gb=0.0,  # Cloud-hosted
            description="High-quality cloud model. Costs money (~$0.05-0.10 per 1M tokens).",
        ),
    }

    def __init__(self):
        """Initialize LLM provider manager"""
        self.ollama_url = os.getenv("LOCAL_LLM_API_URL", "http://localhost:11434")
        self.ollama_available = self._check_ollama_available()
        self.huggingface_token = os.getenv("HUGGINGFACE_API_TOKEN")
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.preferred_provider = os.getenv("LLM_PROVIDER", "ollama").lower()

        logger.info(f"LLM Manager initialized")
        logger.info(f"  Ollama available: {self.ollama_available} ({self.ollama_url})")
        logger.info(f"  HuggingFace token: {'Yes' if self.huggingface_token else 'No'}")
        logger.info(f"  Gemini key: {'Yes' if self.gemini_key else 'No'}")
        logger.info(f"  Preferred provider: {self.preferred_provider}")

    def _check_ollama_available(self) -> bool:
        """Check if Ollama is running"""
        try:
            import requests
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=2)
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            return False

    def get_available_models(self) -> Dict[str, LLMConfig]:
        """Get all currently available models"""
        available = {}

        # Add Ollama models if available
        if self.ollama_available:
            available.update(self.OLLAMA_MODELS)

        # Add HuggingFace models if token available
        if self.huggingface_token:
            available.update(self.HUGGINGFACE_MODELS)

        # Always add Gemini as fallback if key available
        if self.gemini_key:
            available.update(self.GEMINI_MODELS)

        return available

    def get_best_model_for_rtx_5070(self) -> Optional[LLMConfig]:
        """Get best model for RTX 5070 (12GB VRAM)"""
        available = self.get_available_models()

        # RTX 5070 has 12GB VRAM, prefer 12GB models
        for model_name, config in available.items():
            if config.provider == LLMProvider.OLLAMA and config.estimated_vram_gb <= 12:
                if "13b" in model_name or "12b" in model_name:
                    return config

        # Fallback to best available Ollama model
        for model_name, config in available.items():
            if config.provider == LLMProvider.OLLAMA:
                return config

        # Fallback to HuggingFace
        for model_name, config in available.items():
            if config.provider == LLMProvider.HUGGINGFACE:
                return config

        # Last resort: Gemini
        return available.get("gemini-2.5-flash")

    def get_recommended_models(self) -> List[LLMConfig]:
        """Get list of recommended models sorted by preference"""
        available = self.get_available_models()
        models = list(available.values())

        # Sort by: local first, free, medium size, then by provider
        def sort_key(config):
            # Local Ollama models first (0), then HuggingFace (1), then cloud (2)
            provider_order = {
                LLMProvider.OLLAMA: 0,
                LLMProvider.HUGGINGFACE: 1,
                LLMProvider.GEMINI: 2,
                LLMProvider.ANTHROPIC: 3,
            }

            # Prefer free (0) over paid (1)
            free_order = 0 if config.is_free else 1

            # Prefer medium (0), then small (1), then large (2)
            size_order = {
                ModelSize.MEDIUM: 0,
                ModelSize.SMALL: 1,
                ModelSize.LARGE: 2,
            }

            return (provider_order[config.provider], free_order, size_order[config.size])

        return sorted(models, key=sort_key)

    def get_model_info(self, model_name: str) -> Optional[LLMConfig]:
        """Get info for a specific model"""
        all_models = {
            **self.OLLAMA_MODELS,
            **self.HUGGINGFACE_MODELS,
            **self.GEMINI_MODELS,
        }
        return all_models.get(model_name)

    def get_provider_status(self) -> Dict[str, Any]:
        """Get status of all providers"""
        return {
            "ollama": {
                "available": self.ollama_available,
                "url": self.ollama_url if self.ollama_available else None,
                "models": len(self.OLLAMA_MODELS),
            },
            "huggingface": {
                "available": bool(self.huggingface_token),
                "has_token": bool(self.huggingface_token),
                "models": len(self.HUGGINGFACE_MODELS),
            },
            "gemini": {
                "available": bool(self.gemini_key),
                "has_key": bool(self.gemini_key),
                "models": len(self.GEMINI_MODELS),
            },
        }


# Global instance
_llm_manager: Optional[LLMProviderManager] = None


def get_llm_manager() -> LLMProviderManager:
    """Get or create global LLM manager instance"""
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMProviderManager()
    return _llm_manager


# Example usage:
if __name__ == "__main__":
    manager = get_llm_manager()

    print("=== Available Models ===")
    for name, config in manager.get_available_models().items():
        print(f"\n{config.model_display_name}")
        print(f"  Provider: {config.provider.value}")
        print(f"  Free: {config.is_free}")
        print(f"  VRAM: {config.estimated_vram_gb}GB")
        print(f"  Description: {config.description}")

    print("\n=== Best for RTX 5070 ===")
    best = manager.get_best_model_for_rtx_5070()
    if best:
        print(f"{best.model_display_name}: {best.description}")

    print("\n=== Provider Status ===")
    import json
    print(json.dumps(manager.get_provider_status(), indent=2))
