"""
Model Constants and Definitions

Centralized location for model metadata to avoid duplication across:
- cost_calculator.py
- model_router.py
- model_consolidation_service.py
"""

from typing import Dict

# Model cost definitions (API call costs in USD)
MODEL_COSTS: Dict[str, float] = {
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
    # Gemini models
    "gemini-2.5-flash": 0.0001,
    "gemini-2.5-pro": 0.003,
    "gemini-2.0-flash": 0.0001,
}

# Default cost for unknown models
DEFAULT_MODEL_COST = 0.005

# Provider metadata
PROVIDER_ICONS = {
    "ollama": "🖥️",
    "huggingface": "🌐",
    "google": "☁️",
    "anthropic": "🧠",
    "openai": "⚡",
}

# Model families by provider
MODEL_FAMILIES = {
    "openai": ["gpt-4", "gpt-3.5-turbo"],
    "anthropic": ["claude-opus", "claude-sonnet", "claude-haiku"],
    "google": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
    "ollama": ["llama2", "mistral", "mixtral", "codellama", "phi"],
}
