"""
Model Constants and Definitions

Centralized location for model metadata to avoid duplication across:
- cost_calculator.py
- model_router.py
- model_consolidation_service.py
"""


# Model cost definitions (API call costs in USD)
MODEL_COSTS: dict[str, float] = {
    # Ollama models (FREE — local inference on RTX 5090 32GB)
    "ollama/qwen3.5:35b": 0.0,  # Best prose quality for blog writing
    "ollama/qwen3.5:122b": 0.0,  # Top-tier (needs CPU offload)
    "ollama/qwen3:8b": 0.0,  # Fast tasks: research, outline, finalize
    "ollama/qwen3:30b": 0.0,  # Mid-tier alternative
    "ollama/gemma3:27b": 0.0,  # QA/critique (different family for diversity)
    "ollama/gemma3:12b": 0.0,  # Lighter Gemma option
    "ollama/llama3:8b": 0.0,  # Llama 3 base
    "ollama/deepseek-r1:32b": 0.0,  # Reasoning tasks
    "ollama/mixtral": 0.0,  # Legacy
    "ollama/phi3:14b": 0.0,  # Lightweight
}

# Default cost for unknown models
DEFAULT_MODEL_COST = 0.005

# Provider metadata
PROVIDER_ICONS = {
    "ollama": "🖥️",
    "huggingface": "🌐",
}

# Model families by provider
MODEL_FAMILIES = {
    "ollama": ["qwen3.5", "qwen3", "gemma3", "llama3", "deepseek-r1", "phi3", "mixtral"],
}
