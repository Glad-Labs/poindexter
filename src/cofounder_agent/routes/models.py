"""
Model Management Routes for Cofounder Agent

Provides endpoints for:
- Listing available AI models
- Getting provider status
- Model recommendations
"""

import os
import logging
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any, Optional
from datetime import datetime

from services.model_consolidation_service import get_model_consolidation_service
from schemas.models_schemas import (
    ModelInfo,
    ModelsListResponse,
    ProviderStatus,
    ProvidersStatusResponse,
)

logger = logging.getLogger(__name__)

# Router for all model-related endpoints
models_router = APIRouter(prefix="/api/v1/models", tags=["models-v1"])

# Additional router for /api/models endpoint (legacy support)
models_list_router = APIRouter(prefix="/api/models", tags=["models"])


# ============================================================================
# API ENDPOINTS
# ============================================================================

@models_router.get(
    "/available",
    response_model=ModelsListResponse,
    description="Get list of available AI models"
)
async def get_available_models():
    """
    Get all currently available models from the unified model consolidation service.
    
    Returns all models across all providers (Ollama, HuggingFace, Google, Anthropic, OpenAI)
    with unified interface and automatic fallback chain support.
    """
    try:
        service = get_model_consolidation_service()
        models_dict = service.list_models()
        
        # Flatten models from all providers
        models_list = []
        provider_icons = {
            "ollama": "🖥️",
            "huggingface": "🌐",
            "google": "☁️",
            "anthropic": "🧠",
            "openai": "⚡"
        }
        
        for provider, model_names in models_dict.items():
            icon = provider_icons.get(provider, "🤖")
            for model_name in model_names:
                models_list.append(ModelInfo(
                    name=model_name,
                    displayName=f"{model_name} ({provider})",
                    provider=provider,
                    isFree=provider in ["ollama", "huggingface"],
                    size="unknown",
                    estimatedVramGb=0,
                    description=f"Model from {provider}",
                    icon=icon,
                    requiresInternet=provider != "ollama",
                ))
        
        return ModelsListResponse(
            models=models_list,
            total=len(models_list),
            timestamp=datetime.now().isoformat(),
        )
    
    except Exception as e:
        logger.error(f"Error getting available models: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting available models: {str(e)}")


@models_router.get(
    "/status",
    description="Get status of all model providers"
)
async def get_provider_status():
    """
    Get availability status of all model providers in the consolidation service.
    
    Returns provider statuses including:
    - Availability (up/down)
    - Last check time
    - Response metrics
    - Number of available models
    """
    try:
        service = get_model_consolidation_service()
        status = service.get_status()
        
        return {
            "timestamp": datetime.now().isoformat(),
            "providers": status
        }
    
    except Exception as e:
        logger.error(f"Error getting provider status: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting provider status: {str(e)}")


@models_router.get(
    "/recommended",
    response_model=ModelsListResponse,
    description="Get recommended models for current environment"
)
async def get_recommended_models():
    """
    Get models recommended by the model consolidation service.
    
    Uses the fallback chain priority to recommend best models:
    1. Ollama (local, free, zero latency)
    2. HuggingFace (free tier, reasonable quality)
    3. Google Gemini (paid, high quality)
    4. Anthropic Claude (paid, very high quality)
    5. OpenAI GPT (expensive, best quality)
    """
    try:
        service = get_model_consolidation_service()
        models_dict = service.list_models()
        
        # Return models in fallback chain priority order
        models_list = []
        provider_order = ["ollama", "huggingface", "google", "anthropic", "openai"]
        provider_icons = {
            "ollama": "🖥️",
            "huggingface": "🌐",
            "google": "☁️",
            "anthropic": "🧠",
            "openai": "⚡"
        }
        
        for provider in provider_order:
            model_names = models_dict.get(provider, [])
            icon = provider_icons.get(provider, "🤖")
            for model_name in model_names[:1]:  # Just first model per provider
                models_list.append(ModelInfo(
                    name=model_name,
                    displayName=f"{model_name} (Recommended)",
                    provider=provider,
                    isFree=provider in ["ollama", "huggingface"],
                    size="unknown",
                    estimatedVramGb=0,
                    description=f"Recommended model from {provider}",
                    icon=icon,
                    requiresInternet=provider != "ollama",
                ))
        
        return ModelsListResponse(
            models=models_list,
            total=len(models_list),
            timestamp=datetime.now().isoformat(),
        )
    
    except Exception as e:
        logger.error(f"Error getting recommended models: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting recommended models: {str(e)}")


@models_router.get(
    "/rtx5070",
    response_model=ModelsListResponse,
    description="Get models optimized for RTX 5070 (12GB VRAM)"
)
async def get_rtx5070_models():
    """
    Get models that fit within RTX 5070's 12GB VRAM.
    
    RTX 5070 can efficiently run 7B-13B parameter models.
    Falls back to cloud providers if local models exhausted.
    """
    try:
        service = get_model_consolidation_service()
        models_dict = service.list_models()
        
        models_list = []
        provider_icons = {
            "ollama": "🖥️",
            "huggingface": "🌐",
            "google": "☁️",
            "anthropic": "🧠",
            "openai": "⚡"
        }
        
        # Ollama models first (local, use VRAM), then cloud models
        provider_order = ["ollama", "huggingface", "google", "anthropic", "openai"]
        
        for provider in provider_order:
            model_names = models_dict.get(provider, [])
            icon = provider_icons.get(provider, "🤖")
            
            # Limit Ollama to 2 models to respect VRAM
            limit = 2 if provider == "ollama" else 3
            
            for model_name in model_names[:limit]:
                models_list.append(ModelInfo(
                    name=model_name,
                    displayName=f"{model_name} (RTX5070 compatible)",
                    provider=provider,
                    isFree=provider in ["ollama", "huggingface"],
                    size="7B-13B" if provider == "ollama" else "unknown",
                    estimatedVramGb=8 if provider == "ollama" else 0,
                    description=f"Optimized for RTX 5070 from {provider}",
                    icon=icon,
                    requiresInternet=provider != "ollama",
                ))
        
        return ModelsListResponse(
            models=models_list,
            total=len(models_list),
            timestamp=datetime.now().isoformat(),
        )
    
    except Exception as e:
        logger.error(f"Error getting RTX5070 models: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting RTX5070 models: {str(e)}")


# ========== ADDITIONAL ENDPOINTS FOR /api/models (legacy support) ==========

@models_list_router.get(
    "",
    description="Get list of available AI models (legacy endpoint)"
)
async def get_models_list():
    """
    Get all currently available models - legacy endpoint for /api/models.
    Redirects to the new /api/v1/models/available endpoint logic.
    """
    try:
        service = get_model_consolidation_service()
        models_dict = service.list_models()
        
        # Flatten models from all providers
        models_list = []
        provider_icons = {
            "ollama": "🖥️",
            "huggingface": "🌐",
            "google": "☁️",
            "anthropic": "🧠",
            "openai": "⚡"
        }
        
        for provider, model_names in models_dict.items():
            icon = provider_icons.get(provider, "🤖")
            for model_name in model_names:
                models_list.append({
                    "name": model_name,
                    "displayName": f"{model_name} ({provider})",
                    "provider": provider,
                    "isFree": provider in ["ollama", "huggingface"],
                    "size": "unknown",
                    "estimatedVramGb": 0,
                    "description": f"Model from {provider}",
                    "icon": icon,
                    "requiresInternet": provider != "ollama",
                })
        
        return {
            "models": models_list,
            "total": len(models_list),
            "timestamp": datetime.now().isoformat(),
        }
    
    except Exception as e:
        logger.error(f"Error getting models: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting models: {str(e)}")
